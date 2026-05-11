from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from openkb.config import DEFAULT_CONFIG, GLOBAL_CONFIG_DIR, load_config


UTC = timezone.utc
_STATUS_LOCK = threading.RLock()


_RUNTIME_ENV_KEYS = (
    "LLM_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
    "OPENKB_WIRE_API",
    "OPENAI_WIRE_API",
    "OPENKB_MODEL_PROVIDER",
    "OPENKB_MODEL_REASONING_EFFORT",
    "OPENKB_DEEPSEEK_THINKING_ENABLED",
)


@dataclass(frozen=True)
class ModelRoute:
    profile_id: str
    profile_name: str
    model: str
    wire_api: str
    base_url: str
    api_key_env: str
    provider: str = "generic"
    reasoning_effort: str = ""
    thinking_enabled: bool = False
    weight: int = 100
    health: str = "unknown"
    latency_ms: int | None = None
    route_id: str = ""

    def __post_init__(self) -> None:
        if not self.route_id:
            object.__setattr__(self, "route_id", route_id(self.profile_id, self.model))


def route_id(profile_id: str, model: str) -> str:
    return f"{profile_id}:{model}"


def route_profile(route: ModelRoute) -> dict[str, str]:
    return {
        "id": route.profile_id,
        "name": route.profile_name,
        "model": route.model,
        "wire_api": route.wire_api,
        "base_url": route.base_url,
        "provider": str(getattr(route, "provider", "generic") or "generic"),
        "reasoning_effort": str(getattr(route, "reasoning_effort", "") or ""),
        "thinking_enabled": bool(getattr(route, "thinking_enabled", False)),
        "api_key_env": route.api_key_env,
    }


def _status_path(kb_dir: Path) -> Path:
    return Path(kb_dir) / ".openkb" / "model-pool" / "status.json"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_status(kb_dir: Path) -> dict[str, Any]:
    path = _status_path(kb_dir)
    if not path.exists():
        return {"routes": {}, "profiles": {}, "scheduler": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return {"routes": {}, "profiles": {}, "scheduler": {}}
    if not isinstance(data, dict):
        return {"routes": {}, "profiles": {}, "scheduler": {}}
    data.setdefault("routes", {})
    data.setdefault("profiles", {})
    data.setdefault("scheduler", {})
    return data


def save_status(kb_dir: Path, status: dict[str, Any]) -> None:
    path = _status_path(kb_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")


def model_pool_config(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("model_pool")
    return raw if isinstance(raw, dict) else {}


def is_model_pool_enabled(kb_dir: Path) -> bool:
    config = load_config(Path(kb_dir) / ".openkb" / "config.yaml")
    return bool(model_pool_config(config).get("enabled", False))


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _profile_env_key(profile_id: str) -> str:
    import re

    env_id = re.sub(r"[^A-Za-z0-9]+", "_", profile_id).strip("_").upper() or "PROFILE"
    return f"OPENKB_LLM_PROFILE_{env_id}_API_KEY"


def profile_routes(profile: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    default_model = str(profile.get("model") or config.get("model") or DEFAULT_CONFIG["model"]).strip()
    raw_models = profile.get("models")
    if isinstance(raw_models, list) and raw_models:
        routes = []
        for raw in raw_models:
            if isinstance(raw, dict):
                name = str(raw.get("name") or raw.get("model") or "").strip()
                weight = int(raw.get("weight") or 100)
            else:
                name = str(raw or "").strip()
                weight = 100
            if name:
                routes.append({"model": name, "weight": max(weight, 1)})
        if routes:
            return routes
    probe_models = _as_list(profile.get("probe_models"))
    if probe_models:
        return [{"model": model, "weight": 100} for model in probe_models]
    return [{"model": default_model, "weight": 100}]


def configured_routes(kb_dir: Path) -> list[ModelRoute]:
    config = load_config(Path(kb_dir) / ".openkb" / "config.yaml")
    raw_profiles = config.get("llm_profiles")
    profiles = raw_profiles if isinstance(raw_profiles, list) else []
    if not profiles:
        profiles = [
            {
                "id": "default",
                "name": "Default",
                "model": config.get("model", DEFAULT_CONFIG["model"]),
                "wire_api": config.get("wire_api", DEFAULT_CONFIG["wire_api"]),
                "base_url": config.get("base_url", DEFAULT_CONFIG.get("base_url", "")),
            }
        ]
    status = load_status(kb_dir)
    routes_status = status.get("routes", {})
    routes: list[ModelRoute] = []
    for index, profile in enumerate(profiles):
        if not isinstance(profile, dict) or profile.get("enabled", True) is False:
            continue
        profile_id = str(profile.get("id") or f"profile-{index + 1}").strip()
        for item in profile_routes(profile, config):
            rid = route_id(profile_id, item["model"])
            route_status = routes_status.get(rid, {}) if isinstance(routes_status, dict) else {}
            routes.append(
                ModelRoute(
                    profile_id=profile_id,
                    profile_name=str(profile.get("name") or profile_id),
                    model=item["model"],
                    wire_api=str(profile.get("wire_api") or config.get("wire_api") or DEFAULT_CONFIG["wire_api"]).strip().lower(),
                    base_url=str(profile.get("base_url") or config.get("base_url") or "").strip().rstrip("/"),
                    api_key_env=str(profile.get("api_key_env") or _profile_env_key(profile_id)).strip(),
                    provider=str(profile.get("provider") or "generic").strip().lower(),
                    reasoning_effort=str(profile.get("reasoning_effort") or "").strip().lower(),
                    thinking_enabled=bool(profile.get("thinking_enabled", False)),
                    weight=max(int(item.get("weight") or 100), 1),
                    health=str(route_status.get("health") or "unknown"),
                    latency_ms=route_status.get("latency_ms"),
                    route_id=rid,
                )
            )
    return routes


def _route_status(route: ModelRoute, **updates: Any) -> dict[str, Any]:
    payload = {
        "profile_id": route.profile_id,
        "profile_name": route.profile_name,
        "model": route.model,
        "wire_api": route.wire_api,
        "base_url": route.base_url,
        "weight": route.weight,
        "last_checked_at": _now(),
    }
    payload.update(updates)
    return payload


def record_route_success(kb_dir: Path, profile_id: str, model: str, *, latency_ms: int | None = None) -> dict[str, Any]:
    with _STATUS_LOCK:
        status = load_status(kb_dir)
        routes_by_id = {route.route_id: route for route in configured_routes(kb_dir)}
        rid = route_id(profile_id, model)
        route = routes_by_id.get(rid) or ModelRoute(profile_id, profile_id, model, "", "", "", route_id=rid)
        current = dict(status.setdefault("routes", {}).get(rid) or {})
        current.update(
            _route_status(
                route,
                health="healthy",
                latency_ms=latency_ms,
                consecutive_failures=0,
                last_error="",
            )
        )
        current["success_count"] = int(current.get("success_count") or 0) + 1
        status["routes"][rid] = current
        save_status(kb_dir, status)
        return current


def record_route_failure(kb_dir: Path, profile_id: str, model: str, error: Exception | str) -> dict[str, Any]:
    with _STATUS_LOCK:
        status = load_status(kb_dir)
        routes_by_id = {route.route_id: route for route in configured_routes(kb_dir)}
        rid = route_id(profile_id, model)
        route = routes_by_id.get(rid) or ModelRoute(profile_id, profile_id, model, "", "", "", route_id=rid)
        current = dict(status.setdefault("routes", {}).get(rid) or {})
        failures = int(current.get("consecutive_failures") or 0) + 1
        message = str(error).splitlines()[0]
        current.update(
            _route_status(
                route,
                health="offline",
                consecutive_failures=failures,
                last_error=message,
            )
        )
        current["failure_count"] = int(current.get("failure_count") or 0) + 1
        status["routes"][rid] = current
        save_status(kb_dir, status)
        return current


def route_candidates(kb_dir: Path, *, exclude: set[str] | None = None) -> list[ModelRoute]:
    excluded = exclude or set()
    candidates = []
    for route in configured_routes(kb_dir):
        if route.route_id in excluded:
            continue
        if route.health in {"offline", "disabled"}:
            continue
        candidates.append(route)
    return candidates


def select_model_route(kb_dir: Path, *, exclude: set[str] | None = None) -> ModelRoute:
    with _STATUS_LOCK:
        candidates = route_candidates(kb_dir, exclude=exclude)
        if not candidates:
            raise RuntimeError("No healthy model routes available.")
        status = load_status(kb_dir)
        scheduler = status.setdefault("scheduler", {})
        weights = {route.route_id: max(int(route.weight), 1) for route in candidates}
        current = scheduler.setdefault("weighted_round_robin_current", {})
        if not isinstance(current, dict):
            current = {}
            scheduler["weighted_round_robin_current"] = current
        candidate_ids = set(weights)
        for route_key in list(current):
            if route_key not in candidate_ids:
                current.pop(route_key, None)

        total_weight = sum(weights.values())
        selected: ModelRoute | None = None
        selected_score: int | None = None
        for route in candidates:
            score = int(current.get(route.route_id) or 0) + weights[route.route_id]
            current[route.route_id] = score
            if selected is None or score > int(selected_score or 0):
                selected = route
                selected_score = score
        assert selected is not None
        current[selected.route_id] = int(current.get(selected.route_id) or 0) - total_weight
        scheduler["weighted_round_robin_cursor"] = int(scheduler.get("weighted_round_robin_cursor") or 0) + 1
        save_status(kb_dir, status)
        return selected


def probe_model_route(kb_dir: Path, route: ModelRoute, *, api_key: str = "", timeout: int = 20) -> dict[str, Any]:
    from openkb.llm_runtime import completion, normalize_model_name

    model = route.model
    wire_api = route.wire_api
    base_url = route.base_url
    if base_url and wire_api != "responses" and model.startswith("openai/"):
        model = model.split("/", 1)[1]
    else:
        model = normalize_model_name(model)

    completion_kwargs: dict[str, Any] = {
        "max_tokens": 8,
        "timeout": timeout,
    }
    if base_url and wire_api != "responses":
        completion_kwargs["custom_llm_provider"] = "custom_openai"
    snapshot = {key: os.environ.get(key) for key in _RUNTIME_ENV_KEYS}
    try:
        env_file = Path(kb_dir) / ".env"
        if env_file.exists():
            load_dotenv(env_file, override=True)
        global_env = GLOBAL_CONFIG_DIR / ".env"
        if global_env.exists():
            load_dotenv(global_env, override=False)
        if wire_api:
            os.environ["OPENKB_WIRE_API"] = wire_api
        if base_url:
            os.environ["OPENAI_BASE_URL"] = base_url
            os.environ["OPENAI_API_BASE"] = base_url
        selected_key = api_key.strip()
        if not selected_key and route.api_key_env:
            selected_key = os.environ.get(route.api_key_env, "").strip()
        if route.provider:
            os.environ["OPENKB_MODEL_PROVIDER"] = route.provider
        if route.reasoning_effort:
            os.environ["OPENKB_MODEL_REASONING_EFFORT"] = route.reasoning_effort
        if route.thinking_enabled:
            os.environ["OPENKB_DEEPSEEK_THINKING_ENABLED"] = "true"
        if selected_key:
            os.environ["LLM_API_KEY"] = selected_key
            os.environ["OPENAI_API_KEY"] = selected_key
        result = completion(
            model=model,
            messages=[{"role": "user", "content": "Ping. Reply with exactly pong."}],
            **completion_kwargs,
        )
    finally:
        for key, value in snapshot.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return {
        "ok": True,
        "model": model,
        "wire_api": wire_api,
        "base_url": base_url,
        "response_text": result.text,
    }
