"""OpenKB CLI — command-line interface for the knowledge base workflow."""
from __future__ import annotations

# Silence import-time warnings (e.g. pydub's missing-ffmpeg warning emitted
# when markitdown pulls it in). markitdown later clobbers the filters during
# its own import, so we re-apply after all imports below.
import warnings
warnings.filterwarnings("ignore")

import asyncio
import concurrent.futures
import json
import logging
import sys
import time
import threading
from pathlib import Path
from typing import Any, Callable

import os

from agents import set_tracing_disabled
set_tracing_disabled(True)
# Use local model cost map — skip fetching from GitHub on every invocation
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import click
import litellm
litellm.suppress_debug_info = True
from dotenv import dotenv_values, load_dotenv

from openkb.config import DEFAULT_CONFIG, load_config, save_config, load_global_config, register_kb
from openkb.ingest_gate import (
    evaluate_candidate,
    gate_is_active,
    gate_summary_line,
    ingest_gate_config,
    record_gate_decision,
    should_continue_ingest,
)
from openkb.llm_runtime import configure_runtime, get_base_url, model_prefers_responses_api
from openkb.converter import convert_document
from openkb.kb_git import commit_kb_changes, ensure_kb_git
from openkb.log import append_log
from openkb.schema import AGENTS_MD

# Suppress warnings after all imports — markitdown overrides filters at import time
import warnings
warnings.filterwarnings("ignore")

load_dotenv()  # load from cwd (covers running inside the KB dir)


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
_INITIAL_RUNTIME_ENV = {key: os.environ.get(key) for key in _RUNTIME_ENV_KEYS}


def _restore_initial_runtime_env() -> None:
    for key, value in _INITIAL_RUNTIME_ENV.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _normalized_llm_profile(raw: dict, fallback_id: str, config: dict) -> dict[str, Any]:
    profile_id = str(raw.get("id") or fallback_id or "default").strip() or "default"
    return {
        "id": profile_id,
        "name": str(raw.get("name") or ("Default" if profile_id == "default" else profile_id)).strip(),
        "model": str(raw.get("model") or config.get("model") or DEFAULT_CONFIG["model"]).strip(),
        "wire_api": str(raw.get("wire_api") or config.get("wire_api") or DEFAULT_CONFIG["wire_api"]).strip().lower(),
        "base_url": str(raw.get("base_url") or config.get("base_url") or DEFAULT_CONFIG.get("base_url", "")).strip().rstrip("/"),
        "provider": str(raw.get("provider") or "generic").strip().lower(),
        "reasoning_effort": str(raw.get("reasoning_effort") or "").strip().lower(),
        "thinking_enabled": bool(raw.get("thinking_enabled", False)),
        "api_key_env": str(raw.get("api_key_env") or "").strip(),
        "enabled": bool(raw.get("enabled", True)),
    }


def _ordered_llm_profiles(config: dict) -> list[dict[str, Any]]:
    raw_profiles = config.get("llm_profiles")
    profiles: list[dict[str, str]] = []
    seen: set[str] = set()

    if isinstance(raw_profiles, list):
        for index, raw in enumerate(raw_profiles, 1):
            if not isinstance(raw, dict):
                continue
            profile = _normalized_llm_profile(raw, str(raw.get("id") or f"profile-{index}"), config)
            if profile["id"] in seen:
                continue
            profiles.append(profile)
            seen.add(profile["id"])
    elif isinstance(raw_profiles, dict):
        for profile_id, raw in raw_profiles.items():
            if not isinstance(raw, dict):
                continue
            profile = _normalized_llm_profile(raw, str(profile_id), config)
            if profile["id"] in seen:
                continue
            profiles.append(profile)
            seen.add(profile["id"])

    if not profiles:
        profiles.append(_normalized_llm_profile(config, "default", config))

    profiles = [profile for profile in profiles if profile.get("enabled", True)]
    if not profiles:
        return []

    active_id = str(config.get("active_llm_profile") or profiles[0]["id"]).strip()
    active = [profile for profile in profiles if profile["id"] == active_id]
    rest = [profile for profile in profiles if profile["id"] != active_id]
    return active + rest if active else profiles


def _profile_label(profile: dict[str, str]) -> str:
    return profile.get("name") or profile.get("id") or profile.get("model") or "profile"


def _is_retryable_llm_failure(exc: Exception) -> bool:
    from openkb.llm_runtime import _is_retryable_llm_error

    return _is_retryable_llm_error(exc)


def _setup_llm_key(kb_dir: Path | None = None, profile_config: dict[str, str] | None = None) -> None:
    """Set LiteLLM API key from LLM_API_KEY env var if present.

    Load order (override=False, so first one wins):
    1. System environment variables (already set)
    2. KB-local .env  (kb_dir/.env)
    3. Global .env    (~/.config/openkb/.env)

    Also propagates to provider-specific env vars (OPENAI_API_KEY, etc.)
    so that the Agents SDK litellm provider can pick them up.
    """
    model_name: str | None = None
    _restore_initial_runtime_env()

    if kb_dir is not None:
        env_file = kb_dir / ".env"
        if env_file.exists():
            load_dotenv(env_file, override=True)

        config_path = kb_dir / ".openkb" / "config.yaml"
        if config_path.exists():
            config = load_config(config_path)
            active_profile = None
            raw_profiles = config.get("llm_profiles")
            active_id = str(config.get("active_llm_profile") or "").strip()
            if isinstance(raw_profiles, list):
                active_profile = next(
                    (
                        profile
                        for profile in raw_profiles
                        if isinstance(profile, dict) and str(profile.get("id") or "").strip() == active_id
                    ),
                    None,
                )
            selected_profile = profile_config or (active_profile if isinstance(active_profile, dict) else config)
            model_name = str(selected_profile.get("model", "")).strip() or None
            wire_api = str(selected_profile.get("wire_api", "")).strip().lower()
            if wire_api:
                os.environ["OPENKB_WIRE_API"] = wire_api
            base_url = str(selected_profile.get("base_url", "")).strip().rstrip("/")
            if base_url:
                os.environ["OPENAI_BASE_URL"] = base_url
                os.environ["OPENAI_API_BASE"] = base_url
            provider = str(selected_profile.get("provider", "") or "").strip().lower()
            if provider:
                os.environ["OPENKB_MODEL_PROVIDER"] = provider
            else:
                os.environ.pop("OPENKB_MODEL_PROVIDER", None)
            reasoning_effort = str(selected_profile.get("reasoning_effort", "") or "").strip().lower()
            if reasoning_effort:
                os.environ["OPENKB_MODEL_REASONING_EFFORT"] = reasoning_effort
            else:
                os.environ.pop("OPENKB_MODEL_REASONING_EFFORT", None)
            if selected_profile.get("thinking_enabled"):
                os.environ["OPENKB_DEEPSEEK_THINKING_ENABLED"] = "true"
            else:
                os.environ.pop("OPENKB_DEEPSEEK_THINKING_ENABLED", None)
            profile_env = str(selected_profile.get("api_key_env", "")).strip()
            if profile_env and os.environ.get(profile_env):
                os.environ["LLM_API_KEY"] = os.environ[profile_env]

    from openkb.config import GLOBAL_CONFIG_DIR
    global_env = GLOBAL_CONFIG_DIR / ".env"
    if global_env.exists():
        load_dotenv(global_env, override=False)

    api_key = os.environ.get("LLM_API_KEY", "")
    if not api_key:
        # Check if any provider key is already set
        has_key = any(os.environ.get(k) for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"))
        if not has_key:
            click.echo(
                "Warning: No LLM API key found. Set one of:\n"
                f"  1. {kb_dir / '.env' if kb_dir else '<kb_dir>/.env'} — LLM_API_KEY=sk-...\n"
                f"  2. {GLOBAL_CONFIG_DIR / '.env'} — LLM_API_KEY=sk-...\n"
                "  3. Export LLM_API_KEY in your shell profile"
            )
    else:
        litellm.api_key = api_key
        for env_var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
            os.environ[env_var] = api_key

    normalized_base = get_base_url(model_name)
    if normalized_base:
        os.environ["OPENAI_BASE_URL"] = normalized_base
        os.environ["OPENAI_API_BASE"] = normalized_base

    configure_runtime(model_name)

# Supported document extensions for the `add` command
SUPPORTED_EXTENSIONS = {
    ".pdf", ".md", ".markdown", ".docx", ".pptx", ".xlsx",
    ".html", ".htm", ".txt", ".csv",
}

# Map raw doc types to display types
_TYPE_DISPLAY_MAP = {
    "long_pdf": "pageindex",
    "local_long_pdf": "local-long",
}

_SHORT_DOC_TYPES = {"pdf", "docx", "md", "markdown", "html", "htm", "txt", "csv", "pptx", "xlsx"}
_WIKI_SCHEMA_DIRS = (
    "sources/images",
    "summaries",
    "companies",
    "industries",
    "concepts",
    "explorations",
    "reports",
)


def _display_type(raw_type: str) -> str:
    """Map a raw stored doc type to a display type string."""
    if raw_type in _TYPE_DISPLAY_MAP:
        return _TYPE_DISPLAY_MAP[raw_type]
    if raw_type in _SHORT_DOC_TYPES:
        return "short"
    return raw_type


def _ensure_wiki_schema(kb_dir: Path) -> None:
    """Ensure an existing KB has the current optional wiki schema scaffolding."""
    wiki_dir = kb_dir / "wiki"
    if not wiki_dir.exists():
        return

    for subdir in _WIKI_SCHEMA_DIRS:
        (wiki_dir / subdir).mkdir(parents=True, exist_ok=True)

    agents_path = wiki_dir / "AGENTS.md"
    if not agents_path.exists():
        agents_path.write_text(AGENTS_MD, encoding="utf-8")
        return

    try:
        agents_text = agents_path.read_text(encoding="utf-8")
    except OSError:
        return
    required_markers = (
        "companies/",
        "industries/",
        "Company Page",
        "must be an actual company",
        "must be a real industry",
    )
    obsolete_markers = ("themes/", "metrics/", "risks/")
    if not all(marker in agents_text for marker in required_markers) or any(
        marker in agents_text for marker in obsolete_markers
    ):
        agents_path.write_text(AGENTS_MD, encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_kb_dir(override: Path | None = None) -> Path | None:
    """Find the KB root: explicit override → walk up from cwd → global default_kb."""
    # 0. Explicit override (--kb-dir or OPENKB_DIR)
    if override is not None:
        if (override / ".openkb").is_dir():
            return override
        return None
    # 1. Walk up from cwd
    current = Path.cwd().resolve()
    while True:
        if (current / ".openkb").is_dir():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    # 2. Fall back to global config default_kb
    gc = load_global_config()
    default = gc.get("default_kb")
    if default:
        p = Path(default)
        if (p / ".openkb").is_dir():
            return p
    return None


ProgressCallback = Callable[[str], None]
CompileOperation = Callable[[str], object]
_KB_MUTATION_LOCKS: dict[Path, threading.RLock] = {}
_KB_MUTATION_LOCKS_LOCK = threading.Lock()


def _kb_mutation_lock(kb_dir: Path) -> threading.RLock:
    key = Path(kb_dir).resolve()
    with _KB_MUTATION_LOCKS_LOCK:
        lock = _KB_MUTATION_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _KB_MUTATION_LOCKS[key] = lock
        return lock


def _emit_progress(progress_callback: ProgressCallback | None, message: str) -> None:
    if progress_callback is not None:
        progress_callback(message)


def _profile_from_model_route(route) -> dict[str, str]:
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


def _runtime_context_for_profile(kb_dir: Path, profile: dict[str, Any]) -> dict[str, Any]:
    env_values = dict(os.environ)
    env_file = kb_dir / ".env"
    if env_file.exists():
        env_values.update({key: str(value) for key, value in dotenv_values(env_file).items() if value is not None})
    from openkb.config import GLOBAL_CONFIG_DIR

    global_env = GLOBAL_CONFIG_DIR / ".env"
    if global_env.exists():
        for key, value in dotenv_values(global_env).items():
            if value is not None and key not in env_values:
                env_values[key] = str(value)
    api_key = ""
    profile_env = str(profile.get("api_key_env") or "").strip()
    if profile_env:
        api_key = env_values.get(profile_env, "").strip()
    if not api_key:
        api_key = env_values.get("LLM_API_KEY", "").strip() or env_values.get("OPENAI_API_KEY", "").strip()
    return {
        "api_key": api_key,
        "wire_api": str(profile.get("wire_api") or "").strip().lower(),
        "base_url": str(profile.get("base_url") or "").strip().rstrip("/"),
        "provider": str(profile.get("provider") or "").strip().lower(),
        "reasoning_effort": str(profile.get("reasoning_effort") or "").strip().lower(),
        "thinking_enabled": bool(profile.get("thinking_enabled", False)),
    }


def _probe_failed_model_route(kb_dir: Path, route) -> None:
    from openkb.model_pool import probe_model_route, record_route_failure, record_route_success

    try:
        probe_model_route(kb_dir, route)
        record_route_success(kb_dir, route.profile_id, route.model)
    except Exception as exc:
        record_route_failure(kb_dir, route.profile_id, route.model, exc)


def _record_external_compile_usage(
    kb_dir: Path,
    *,
    model: str,
    wire_api: str,
    base_url: str,
    started_at: float,
    status: str,
    input_payload: dict[str, Any],
    output_payload: dict[str, Any],
    error: str = "",
) -> None:
    from openkb.llm_usage import record_usage

    record_usage(
        kb_dir=kb_dir,
        feature="compile",
        model=model,
        wire_api=wire_api,
        base_url=base_url,
        status=status,
        duration_ms=max(int((time.perf_counter() - started_at) * 1000), 0),
        error=error,
        input_payload=input_payload,
        output_payload=output_payload,
    )


def _run_pageindex_with_model_pool(
    kb_dir: Path,
    default_profile: dict[str, str],
    default_model: str,
    operation: Callable[[str], object],
    *,
    input_payload: dict[str, Any],
    fixed_route=None,
) -> object:
    from openkb.model_pool import (
        configured_routes,
        is_model_pool_enabled,
        record_route_failure,
        record_route_success,
        route_profile,
        select_model_route,
    )

    if fixed_route is not None:
        route = fixed_route
        started_at = time.perf_counter()
        try:
            from openkb.llm_runtime import llm_runtime_context

            with llm_runtime_context(_runtime_context_for_profile(kb_dir, _profile_from_model_route(route))):
                result = operation(route.model)
            record_route_success(kb_dir, route.profile_id, route.model)
            _record_external_compile_usage(
                kb_dir,
                model=route.model,
                wire_api=route.wire_api,
                base_url=route.base_url,
                started_at=started_at,
                status="succeeded",
                input_payload=input_payload,
                output_payload={
                    "doc_id": str(getattr(result, "doc_id", "") or ""),
                    "description": str(getattr(result, "description", "") or ""),
                },
            )
            return result
        except Exception as exc:
            record_route_failure(kb_dir, route.profile_id, route.model, exc)
            _record_external_compile_usage(
                kb_dir,
                model=route.model,
                wire_api=route.wire_api,
                base_url=route.base_url,
                started_at=started_at,
                status="failed",
                error=str(exc),
                input_payload=input_payload,
                output_payload={},
            )
            raise

    if is_model_pool_enabled(kb_dir):
        excluded_routes: set[str] = set()
        last_exc: Exception | None = None
        max_attempts = max(len(configured_routes(kb_dir)), 1)
        for _attempt in range(max_attempts):
            route = select_model_route(kb_dir, exclude=excluded_routes)
            _setup_llm_key(kb_dir, route_profile(route))
            started_at = time.perf_counter()
            try:
                result = operation(route.model)
                record_route_success(kb_dir, route.profile_id, route.model)
                _record_external_compile_usage(
                    kb_dir,
                    model=route.model,
                    wire_api=route.wire_api,
                    base_url=route.base_url,
                    started_at=started_at,
                    status="succeeded",
                    input_payload=input_payload,
                    output_payload={
                        "doc_id": str(getattr(result, "doc_id", "") or ""),
                        "description": str(getattr(result, "description", "") or ""),
                    },
                )
                return result
            except Exception as exc:
                last_exc = exc
                excluded_routes.add(route.route_id)
                record_route_failure(kb_dir, route.profile_id, route.model, exc)
                _record_external_compile_usage(
                    kb_dir,
                    model=route.model,
                    wire_api=route.wire_api,
                    base_url=route.base_url,
                    started_at=started_at,
                    status="failed",
                    error=str(exc),
                    input_payload=input_payload,
                    output_payload={},
                )
                _probe_failed_model_route(kb_dir, route)
                continue
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("No healthy model routes available for PageIndex.")

    _setup_llm_key(kb_dir, default_profile)
    started_at = time.perf_counter()
    try:
        result = operation(default_model)
    except Exception as exc:
        _record_external_compile_usage(
            kb_dir,
            model=default_model,
            wire_api=default_profile.get("wire_api", ""),
            base_url=default_profile.get("base_url", ""),
            started_at=started_at,
            status="failed",
            error=str(exc),
            input_payload=input_payload,
            output_payload={},
        )
        raise
    _record_external_compile_usage(
        kb_dir,
        model=default_model,
        wire_api=default_profile.get("wire_api", ""),
        base_url=default_profile.get("base_url", ""),
        started_at=started_at,
        status="succeeded",
        input_payload=input_payload,
        output_payload={
            "doc_id": str(getattr(result, "doc_id", "") or ""),
            "description": str(getattr(result, "description", "") or ""),
        },
    )
    return result


def _run_compile_with_model_pool(
    kb_dir: Path,
    progress_callback: ProgressCallback | None,
    operation: CompileOperation,
) -> object:
    from openkb.model_pool import (
        configured_routes,
        record_route_failure,
        record_route_success,
        select_model_route,
    )

    excluded_routes: set[str] = set()
    last_exc: Exception | None = None
    max_attempts = max(len(configured_routes(kb_dir)), 1)
    for attempt in range(max_attempts):
        try:
            route = select_model_route(kb_dir, exclude=excluded_routes)
        except RuntimeError:
            if last_exc is not None:
                raise last_exc
            raise
        if attempt > 0:
            message = f"Retrying with model route {route.profile_name}/{route.model}"
            click.echo(f"  {message}...")
            _emit_progress(progress_callback, message)
        _setup_llm_key(kb_dir, _profile_from_model_route(route))
        try:
            result = asyncio.run(operation(route.model))
            record_route_success(kb_dir, route.profile_id, route.model)
            return result
        except Exception as exc:
            last_exc = exc
            excluded_routes.add(route.route_id)
            record_route_failure(kb_dir, route.profile_id, route.model, exc)
            _probe_failed_model_route(kb_dir, route)
            continue
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("No healthy model routes available for compilation.")


def _run_compile_with_fixed_route(
    kb_dir: Path,
    route,
    operation: CompileOperation,
) -> object:
    from openkb.llm_runtime import llm_runtime_context
    from openkb.model_pool import record_route_failure, record_route_success

    profile = _profile_from_model_route(route)
    try:
        with llm_runtime_context(_runtime_context_for_profile(kb_dir, profile)):
            result = asyncio.run(operation(route.model))
        record_route_success(kb_dir, route.profile_id, route.model)
        return result
    except Exception as exc:
        record_route_failure(kb_dir, route.profile_id, route.model, exc)
        raise


def _run_compile_with_profile_fallback(
    kb_dir: Path,
    profiles: list[dict[str, str]],
    progress_callback: ProgressCallback | None,
    operation: CompileOperation,
    *,
    fixed_route=None,
) -> object:
    from openkb.model_pool import is_model_pool_enabled

    if fixed_route is not None:
        return _run_compile_with_fixed_route(kb_dir, fixed_route, operation)

    if is_model_pool_enabled(kb_dir):
        return _run_compile_with_model_pool(kb_dir, progress_callback, operation)

    last_exc: Exception | None = None
    for profile_index, profile in enumerate(profiles):
        if profile_index > 0:
            message = f"Retrying with LLM profile {_profile_label(profile)}"
            click.echo(f"  {message}...")
            _emit_progress(progress_callback, message)
        _setup_llm_key(kb_dir, profile)
        model_name = profile["model"]
        for attempt in range(2):
            try:
                return asyncio.run(operation(model_name))
            except Exception as exc:
                last_exc = exc
                if attempt == 0:
                    click.echo(f"  Retrying compilation in 2s...")
                    time.sleep(2)
                    continue
                break
        if last_exc is None or not _is_retryable_llm_failure(last_exc):
            raise last_exc
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("No LLM profiles available for compilation.")


def _safe_echo(message: object = "", **kwargs) -> None:
    """Echo text without crashing on narrow Windows console encodings."""
    try:
        click.echo(message, **kwargs)
        return
    except UnicodeEncodeError:
        text = str(message)
        encoding = getattr(sys.exc_info()[1], "encoding", None) or getattr(sys.stdout, "encoding", None) or "utf-8"
        try:
            safe_text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        except LookupError:
            safe_text = text.encode("ascii", errors="replace").decode("ascii")
        click.echo(safe_text, **kwargs)


def add_single_file(
    file_path: Path,
    kb_dir: Path,
    *,
    force: bool = False,
    force_gate_pass: bool = False,
    force_gate_reject: bool = False,
    gate_reason: str = "",
    gate_operator: str = "",
    strict: bool = False,
    strategy_override: str | None = None,
    progress_callback: ProgressCallback | None = None,
    job=None,
    model_route=None,
) -> None:
    """Convert, index, and compile a single document into the knowledge base.

    Steps:
    1. Load config to get the model name.
    2. Convert the document (hash-check; skip if already known).
    3. If long doc: run PageIndex then compile_long_doc.
    4. Else: compile_short_doc.
    """
    from openkb.agent.compiler import (
        compile_local_long_doc,
        compile_long_doc,
        compile_short_doc,
        compile_progress_callback,
    )
    from openkb.llm_usage import llm_usage_context

    logger = logging.getLogger(__name__)
    openkb_dir = kb_dir / ".openkb"
    config = load_config(openkb_dir / "config.yaml")
    profiles = _ordered_llm_profiles(config)
    if model_route is None:
        if not profiles:
            message = "No enabled LLM profiles available."
            click.echo(f"  [ERROR] {message}")
            if strict:
                raise RuntimeError(message)
            return
        active_profile = profiles[0]
    else:
        active_profile = _profile_from_model_route(model_route)
    _setup_llm_key(kb_dir, active_profile)
    compile_max_concurrency = max(int(config.get("compile_max_concurrency", DEFAULT_CONFIG["compile_max_concurrency"])), 1)
    gate_config = ingest_gate_config(config)

    def _run_compile(operation):
        with llm_usage_context(kb_dir, "compile"):
            return _run_compile_with_profile_fallback(
                kb_dir,
                profiles,
                progress_callback,
                operation,
                fixed_route=model_route,
            )

    if force_gate_pass and not gate_config.allow_force_pass:
        message = "Ingest gate force-pass is disabled by config."
        click.echo(f"  [ERROR] {message}")
        if strict:
            raise RuntimeError(message)
        return
    if force_gate_reject and not gate_config.allow_force_reject:
        message = "Ingest gate force-reject is disabled by config."
        click.echo(f"  [ERROR] {message}")
        if strict:
            raise RuntimeError(message)
        return

    if gate_is_active(gate_config, force_pass=force_gate_pass, force_reject=force_gate_reject):
        gate_model = str(config.get("ingest_gate", {}).get("model") or active_profile["model"]).strip()
        click.echo(f"  Pre-ingest gate evaluating: {file_path.name}")
        _emit_progress(progress_callback, f"Pre-ingest gate: {file_path.name}")
        try:
            with llm_usage_context(kb_dir, "ingest_gate"):
                gate_result = evaluate_candidate(
                    file_path,
                    kb_dir,
                    model=gate_model,
                    language=str(config.get("language") or DEFAULT_CONFIG["language"]),
                    config=gate_config,
                    force_pass=force_gate_pass,
                    force_reject=force_gate_reject,
                    force_reason=gate_reason,
                    operator=gate_operator,
                )
        except Exception as exc:
            click.echo(f"  [ERROR] Ingest gate failed: {exc}")
            logger.debug("Ingest gate traceback:", exc_info=True)
            if strict:
                raise RuntimeError(f"Ingest gate failed: {exc}") from exc
            return

        if gate_config.log_all_decisions:
            with _kb_mutation_lock(kb_dir):
                record_gate_decision(
                    kb_dir,
                    gate_result,
                    language=str(config.get("language") or DEFAULT_CONFIG["language"]),
                )
                append_log(kb_dir / "wiki", "ingest-gate", f"{file_path.name} | {gate_summary_line(gate_result)}")
                commit_kb_changes(
                    kb_dir,
                    f"Gate {file_path.name} -> {gate_result.get('final_decision')} {gate_result.get('total_score', 'unscored')}",
                )

        click.echo(f"  Gate result: {gate_summary_line(gate_result)}")
        if not should_continue_ingest(gate_result):
            click.echo(f"  [SKIP] Ingest blocked by gate: {file_path.name}")
            _emit_progress(progress_callback, f"Gate blocked ingest: {file_path.name}")
            return

    # 2. Convert document
    click.echo(f"Adding: {file_path.name}")
    _emit_progress(progress_callback, f"Converting: {file_path.name}")
    try:
        result = convert_document(file_path, kb_dir, force=force, strategy_override=strategy_override, job=job)
    except Exception as exc:
        click.echo(f"  [ERROR] Conversion failed: {exc}")
        logger.debug("Conversion traceback:", exc_info=True)
        if strict:
            raise RuntimeError(f"Conversion failed: {exc}") from exc
        return

    if result.skipped:
        click.echo(f"  [SKIP] Already in knowledge base: {file_path.name}")
        _emit_progress(progress_callback, f"Skipped already-known file: {file_path.name}")
        return

    doc_name = file_path.stem
    removed_stale_pages: list[str] = []

    # 3/4. Index and compile
    if result.local_long_doc:
        click.echo("  Document routed to local page index compilation...")
        _emit_progress(progress_callback, f"Compiling local page index document: {file_path.name}")
        try:
            with compile_progress_callback(progress_callback):
                removed_stale_pages = _run_compile(
                    lambda model: compile_local_long_doc(
                        doc_name,
                        result.source_path,
                        kb_dir,
                        model,
                        max_concurrency=compile_max_concurrency,
                        cleanup_existing=force,
                    )
                )
        except Exception as exc:
            click.echo(f"  [ERROR] Compilation failed: {exc}")
            logger.debug("Compilation traceback:", exc_info=True)
            if strict:
                raise RuntimeError(f"Compilation failed: {exc}") from exc
            return
    elif result.selected_strategy == "ocr-pageindex-local":
        click.echo("  Document routed to OCR + local PageIndex...")
        _emit_progress(progress_callback, f"Indexing OCR document locally: {file_path.name}")
        try:
            if result.source_path is None or result.pageindex_input_path is None:
                raise RuntimeError("OCR PageIndex artifacts are missing.")
            from openkb.indexer import index_ocr_with_local_pageindex

            pageindex_model = str(config.get("pageindex_local_model") or profiles[0]["model"]).strip()
            index_result = _run_pageindex_with_model_pool(
                kb_dir,
                profiles[0],
                pageindex_model,
                lambda selected_model: index_ocr_with_local_pageindex(
                    doc_name,
                    result.source_path,
                    result.pageindex_input_path,
                    kb_dir,
                    model=selected_model,
                    job=job,
                ),
                input_payload={
                    "doc_name": doc_name,
                    "source_path": str(result.source_path),
                    "pageindex_input_path": str(result.pageindex_input_path),
                    "mode": "pageindex_local",
                },
                fixed_route=model_route,
            )
        except Exception as exc:
            click.echo(f"  [WARN] Local PageIndex failed; falling back to OCR local-long: {exc}")
            logger.debug("Local PageIndex traceback:", exc_info=True)
            _emit_progress(progress_callback, f"Falling back to OCR local-long: {file_path.name}")
            try:
                with compile_progress_callback(progress_callback):
                    removed_stale_pages = _run_compile(
                        lambda model: compile_local_long_doc(
                            doc_name,
                            result.source_path,
                            kb_dir,
                            model,
                            max_concurrency=compile_max_concurrency,
                            cleanup_existing=force,
                        )
                    )
            except Exception as fallback_exc:
                click.echo(f"  [ERROR] Compilation failed: {fallback_exc}")
                logger.debug("Fallback compilation traceback:", exc_info=True)
                if strict:
                    raise RuntimeError(f"Compilation failed: {fallback_exc}") from fallback_exc
                return
        else:
            summary_path = kb_dir / "wiki" / "summaries" / f"{doc_name}.md"
            click.echo(f"  Compiling local PageIndex doc (doc_id={index_result.doc_id})...")
            _emit_progress(progress_callback, f"Compiling local PageIndex document: {file_path.name}")
            try:
                with compile_progress_callback(progress_callback):
                    removed_stale_pages = _run_compile(
                        lambda model: compile_long_doc(
                            doc_name,
                            summary_path,
                            index_result.doc_id,
                            kb_dir,
                            model,
                            doc_description=index_result.description,
                            max_concurrency=compile_max_concurrency,
                            cleanup_existing=force,
                        )
                    )
            except Exception as exc:
                click.echo(f"  [ERROR] Compilation failed: {exc}")
                logger.debug("Compilation traceback:", exc_info=True)
                if strict:
                    raise RuntimeError(f"Compilation failed: {exc}") from exc
                return
    elif result.is_long_doc:
        click.echo(f"  Long document detected — indexing with PageIndex...")
        _emit_progress(progress_callback, f"Indexing long document: {file_path.name}")
        try:
            from openkb.indexer import index_long_document
            index_result = _run_pageindex_with_model_pool(
                kb_dir,
                profiles[0],
                str(config.get("model") or profiles[0]["model"]).strip(),
                lambda selected_model: index_long_document(result.raw_path, kb_dir, model=selected_model),
                input_payload={
                    "doc_name": doc_name,
                    "raw_path": str(result.raw_path),
                    "mode": "pageindex_cloud",
                },
                fixed_route=model_route,
            )
        except Exception as exc:
            click.echo(f"  [ERROR] Indexing failed: {exc}")
            logger.debug("Indexing traceback:", exc_info=True)
            if strict:
                raise RuntimeError(f"Indexing failed: {exc}") from exc
            return

        summary_path = kb_dir / "wiki" / "summaries" / f"{doc_name}.md"
        click.echo(f"  Compiling long doc (doc_id={index_result.doc_id})...")
        _emit_progress(progress_callback, f"Compiling long document: {file_path.name}")
        try:
            with compile_progress_callback(progress_callback):
                removed_stale_pages = _run_compile(
                    lambda model: compile_long_doc(
                        doc_name,
                        summary_path,
                        index_result.doc_id,
                        kb_dir,
                        model,
                        doc_description=index_result.description,
                        max_concurrency=compile_max_concurrency,
                        cleanup_existing=force,
                    )
                )
        except Exception as exc:
            click.echo(f"  [ERROR] Compilation failed: {exc}")
            logger.debug("Compilation traceback:", exc_info=True)
            if strict:
                raise RuntimeError(f"Compilation failed: {exc}") from exc
            return
    else:
        click.echo(f"  Compiling short doc...")
        _emit_progress(progress_callback, f"Compiling short document: {file_path.name}")
        try:
            with compile_progress_callback(progress_callback):
                removed_stale_pages = _run_compile(
                    lambda model: compile_short_doc(
                        doc_name,
                        result.source_path,
                        kb_dir,
                        model,
                        max_concurrency=compile_max_concurrency,
                        cleanup_existing=force,
                    )
                )
        except Exception as exc:
            click.echo(f"  [ERROR] Compilation failed: {exc}")
            logger.debug("Compilation traceback:", exc_info=True)
            if strict:
                raise RuntimeError(f"Compilation failed: {exc}") from exc
            return

    if removed_stale_pages:
        click.echo(f"  Removed {len(removed_stale_pages)} stale generated page(s) for recompilation.")

    # Register hash only after successful compilation
    if result.file_hash:
        _emit_progress(progress_callback, f"Registering document: {file_path.name}")
        from openkb.workflows.import_pipeline import register_converted_document

        register_converted_document(
            file_path,
            kb_dir,
            result,
            summary_state="ready",
            review_state="approved",
            promotion_state="promoted",
            source_state="ready",
        )

    with _kb_mutation_lock(kb_dir):
        append_log(kb_dir / "wiki", "ingest", file_path.name)
        commit_kb_changes(kb_dir, f"Add {file_path.name}")
    _emit_progress(progress_callback, f"Finished: {file_path.name}")
    click.echo(f"  [OK] {file_path.name} added to knowledge base.")


def _healthy_model_pool_routes(kb_dir: Path) -> list[Any]:
    from openkb.model_pool import is_model_pool_enabled, route_candidates

    if not is_model_pool_enabled(kb_dir):
        return []
    routes = []
    seen_profiles: set[str] = set()
    for route in route_candidates(kb_dir):
        if route.health != "healthy":
            continue
        if route.profile_id in seen_profiles:
            continue
        routes.append(route)
        seen_profiles.add(route.profile_id)
    return routes


def _add_directory_files(
    files: list[Path],
    kb_dir: Path,
    *,
    force: bool = False,
    force_gate_pass: bool = False,
    force_gate_reject: bool = False,
    gate_reason: str = "",
    gate_operator: str = "",
) -> None:
    total = len(files)
    routes = _healthy_model_pool_routes(kb_dir)
    if len(routes) < 2 or total < 2:
        for i, f in enumerate(files, 1):
            click.echo(f"\n[{i}/{total}] ", nl=False)
            if force:
                add_single_file(
                    f,
                    kb_dir,
                    force=True,
                    force_gate_pass=force_gate_pass,
                    force_gate_reject=force_gate_reject,
                    gate_reason=gate_reason,
                    gate_operator=gate_operator,
                )
            else:
                add_single_file(
                    f,
                    kb_dir,
                    force_gate_pass=force_gate_pass,
                    force_gate_reject=force_gate_reject,
                    gate_reason=gate_reason,
                    gate_operator=gate_operator,
                )
        return

    max_workers = min(len(routes), total)
    click.echo(f"Importing with {max_workers} parallel task(s) across healthy LLM profile(s).")

    route_buckets: list[list[tuple[int, Path]]] = [[] for _ in range(max_workers)]
    for offset, item in enumerate(enumerate(files, 1)):
        route_buckets[offset % max_workers].append(item)
    errors: list[tuple[Path, Exception]] = []
    errors_lock = threading.Lock()

    def run_worker(route, bucket: list[tuple[int, Path]]) -> None:
        for index, file_path in bucket:
            try:
                click.echo(f"\n[{index}/{total}] {file_path.name} -> {route.profile_name}/{route.model}")
                add_single_file(
                    file_path,
                    kb_dir,
                    force=force,
                    force_gate_pass=force_gate_pass,
                    force_gate_reject=force_gate_reject,
                    gate_reason=gate_reason,
                    gate_operator=gate_operator,
                    strict=True,
                    model_route=route,
                )
            except Exception as exc:
                with errors_lock:
                    errors.append((file_path, exc))

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(run_worker, route, bucket)
            for route, bucket in zip(routes[:max_workers], route_buckets)
        ]
        for future in concurrent.futures.as_completed(futures):
            future.result()
    for file_path, exc in errors:
        click.echo(f"  [ERROR] {file_path.name} failed: {exc}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.group()
@click.option("-v", "--verbose", is_flag=True, default=False, help="Enable verbose logging.")
@click.option("--kb-dir", "kb_dir_override", default=None, type=click.Path(exists=True, file_okay=False, resolve_path=True), help="Path to a KB root directory (overrides auto-detection).")
@click.pass_context
def cli(ctx, verbose, kb_dir_override):
    """OpenKB — Karpathy's LLM Knowledge Base workflow, powered by PageIndex."""
    logging.basicConfig(
        format="%(name)s %(levelname)s: %(message)s",
        level=logging.WARNING,
    )
    if verbose:
        logging.getLogger("openkb").setLevel(logging.DEBUG)
    ctx.ensure_object(dict)
    if kb_dir_override:
        ctx.obj["kb_dir_override"] = Path(kb_dir_override)
    else:
        env_kb = os.environ.get("OPENKB_DIR")
        if env_kb:
            ctx.obj["kb_dir_override"] = Path(env_kb).resolve()
        else:
            ctx.obj["kb_dir_override"] = None


@cli.command()
@click.argument("path", default=".")
def use(path):
    """Set PATH as the default knowledge base."""
    target = Path(path).resolve()
    if not (target / ".openkb").is_dir():
        click.echo(f"Not a knowledge base: {target}")
        return
    register_kb(target)
    click.echo(f"Default KB set to: {target}")


@cli.command()
def init():
    """Initialise a new knowledge base in the current directory."""
    openkb_dir = Path(".openkb")
    if openkb_dir.exists():
        click.echo("Knowledge base already initialized.")
        return

    # Interactive prompts
    click.echo("Pick an LLM in `provider/model` LiteLLM format:")
    click.echo("  OpenAI:    gpt-5.4-mini, gpt-5.4")
    click.echo("  Anthropic: anthropic/claude-sonnet-4-6, anthropic/claude-opus-4-6")
    click.echo("  Gemini:    gemini/gemini-3.1-pro-preview, gemini/gemini-3-flash-preview")
    click.echo("  Others:    see https://docs.litellm.ai/docs/providers")
    click.echo()
    model = click.prompt(
        f"Model (enter for default {DEFAULT_CONFIG['model']})",
        default=DEFAULT_CONFIG["model"],
        show_default=False,
    )
    api_key = click.prompt(
        "LLM API Key (saved to .env, enter to skip)",
        default="",
        hide_input=True,
        show_default=False,
    ).strip()
    # Create directory structure
    Path("raw").mkdir(exist_ok=True)
    Path("wiki/sources/images").mkdir(parents=True, exist_ok=True)
    Path("wiki/summaries").mkdir(parents=True, exist_ok=True)
    Path("wiki/companies").mkdir(parents=True, exist_ok=True)
    Path("wiki/industries").mkdir(parents=True, exist_ok=True)
    Path("wiki/concepts").mkdir(parents=True, exist_ok=True)

    # Write wiki files
    Path("wiki/AGENTS.md").write_text(AGENTS_MD, encoding="utf-8")
    Path("wiki/index.md").write_text(
        "# Knowledge Base Index\n\n"
        "## Documents\n\n"
        "## Companies\n\n"
        "## Industries\n\n"
        "## Concepts\n\n"
        "## Explorations\n",
        encoding="utf-8",
    )
    Path("wiki/log.md").write_text("# Operations Log\n\n", encoding="utf-8")

    # Create .openkb/ state directory
    openkb_dir.mkdir()
    resolved_wire_api = "responses" if model_prefers_responses_api(model) else DEFAULT_CONFIG["wire_api"]
    config = {
        "active_llm_profile": "default",
        "llm_profiles": [
            {
                "id": "default",
                "name": "Default",
                "model": model,
                "wire_api": resolved_wire_api,
                "base_url": DEFAULT_CONFIG["base_url"],
                "api_key_env": "OPENKB_LLM_PROFILE_DEFAULT_API_KEY",
            }
        ],
        "model": model,
        "language": DEFAULT_CONFIG["language"],
        "pageindex_threshold": DEFAULT_CONFIG["pageindex_threshold"],
        "compile_max_concurrency": DEFAULT_CONFIG["compile_max_concurrency"],
        "wire_api": resolved_wire_api,
        "base_url": DEFAULT_CONFIG["base_url"],
    }
    save_config(openkb_dir / "config.yaml", config)
    (openkb_dir / "hashes.json").write_text(json.dumps({}), encoding="utf-8")

    # Write API key to KB-local .env (0600) if the user provided one
    if api_key:
        env_path = Path(".env")
        if env_path.exists():
            click.echo(".env already exists, skipping write. Add LLM_API_KEY manually if needed.")
        else:
            env_path.write_text(f"LLM_API_KEY={api_key}\n", encoding="utf-8")
            os.chmod(env_path, 0o600)
            click.echo("Saved LLM API key to .env.")

    git_result = ensure_kb_git(Path.cwd(), initial_commit_message="Initialize OpenKB knowledge base")
    if not git_result.git_available:
        click.echo("[WARN] Git not available; knowledge base initialized without Git history.")

    # Register this KB in the global config
    register_kb(Path.cwd())

    click.echo("Knowledge base initialized.")


@cli.command()
@click.option("--force", is_flag=True, default=False, help="Recompile even if the document hash is already indexed.")
@click.option("--force-pass", is_flag=True, default=False, help="Force the ingest gate to allow this document.")
@click.option("--force-reject", is_flag=True, default=False, help="Force the ingest gate to reject this document.")
@click.option("--gate-reason", default="", help="Reason recorded for force-pass or force-reject.")
@click.option("--gate-operator", default="", help="Operator name recorded in ingest gate audit.")
@click.option("--import-only", is_flag=True, default=False, help="Only import raw/source artifacts; do not compile summaries or wiki pages.")
@click.argument("path")
@click.pass_context
def add(ctx, force, force_pass, force_reject, gate_reason, gate_operator, import_only, path):
    """Add a document or directory of documents at PATH to the knowledge base."""
    if force_pass and force_reject:
        click.echo("Cannot use --force-pass and --force-reject together.")
        return
    if (force_pass or force_reject) and not gate_reason.strip():
        click.echo("--gate-reason is required when using --force-pass or --force-reject.")
        return
    kb_dir = _find_kb_dir(ctx.obj.get("kb_dir_override"))
    if kb_dir is None:
        click.echo("No knowledge base found. Run `openkb init` first.")
        return
    _ensure_wiki_schema(kb_dir)

    target = Path(path)
    if not target.exists():
        click.echo(f"Path does not exist: {path}")
        return

    if target.is_dir():
        files = [
            f for f in sorted(target.rglob("*"))
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
        ]
        if not files:
            click.echo(f"No supported files found in {path}.")
            return
        if import_only:
            _run_import_files(files, kb_dir, force=force, path_label=path)
            return
        total = len(files)
        click.echo(f"Found {total} supported file(s) in {path}.")
        _add_directory_files(
            files,
            kb_dir,
            force=force,
            force_gate_pass=force_pass,
            force_gate_reject=force_reject,
            gate_reason=gate_reason,
            gate_operator=gate_operator,
        )
    else:
        if target.suffix.lower() not in SUPPORTED_EXTENSIONS:
            click.echo(
                f"Unsupported file type: {target.suffix}. "
                f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            )
            return
        if import_only:
            _run_import_files([target], kb_dir, force=force, path_label=path)
            return
        if force:
            add_single_file(
                target,
                kb_dir,
                force=True,
                force_gate_pass=force_pass,
                force_gate_reject=force_reject,
                gate_reason=gate_reason,
                gate_operator=gate_operator,
            )
        else:
            add_single_file(
                target,
                kb_dir,
                force_gate_pass=force_pass,
                force_gate_reject=force_reject,
                gate_reason=gate_reason,
                gate_operator=gate_operator,
            )


def import_single_file(
    file_path: Path,
    kb_dir: Path,
    *,
    force: bool = False,
    strict: bool = False,
    strategy_override: str | None = None,
    job=None,
) -> dict[str, Any] | None:
    """Import one document into raw/source inventory without wiki compilation."""
    from openkb.workflows.import_pipeline import import_document_source

    click.echo(f"Importing: {file_path.name}")
    try:
        result = import_document_source(
            file_path,
            kb_dir,
            force=force,
            strategy_override=strategy_override,
            job=job,
        )
    except Exception as exc:
        click.echo(f"  [ERROR] Import failed: {exc}")
        if strict:
            raise RuntimeError(f"Import failed: {exc}") from exc
        return None

    if result.get("skipped"):
        click.echo(f"  [SKIP] Already in inventory: {file_path.name}")
    else:
        source_path = result.get("source_path") or "source pending"
        click.echo(f"  [OK] Source ready: {source_path}")
    return result


def _run_import_files(
    files: list[Path],
    kb_dir: Path,
    *,
    force: bool = False,
    strict: bool = False,
    strategy_override: str | None = None,
    path_label: str = "",
) -> tuple[int, int, int]:
    imported = 0
    skipped = 0
    failed = 0
    if len(files) > 1:
        click.echo(f"Found {len(files)} supported file(s) in {path_label or 'input'}.")
    for index, file_path in enumerate(files, 1):
        if len(files) > 1:
            click.echo(f"\n[{index}/{len(files)}] ", nl=False)
        try:
            result = import_single_file(
                file_path,
                kb_dir,
                force=force,
                strict=strict,
                strategy_override=strategy_override,
            )
        except RuntimeError:
            raise
        if result is None:
            failed += 1
        elif result.get("skipped"):
            skipped += 1
        else:
            imported += 1

    if imported:
        with _kb_mutation_lock(kb_dir):
            append_log(kb_dir / "wiki", "import", f"{imported} source document(s)")
            commit_kb_changes(kb_dir, f"Import {imported} source document(s)")
    click.echo(f"Import complete: {imported} imported, {skipped} skipped, {failed} failed.")
    return imported, skipped, failed


@cli.command(name="import")
@click.option("--force", is_flag=True, default=False, help="Re-import even if the document hash is already indexed.")
@click.option("--strict", is_flag=True, default=False, help="Stop on the first import failure.")
@click.option("--strategy-override", default=None, help="Override PDF preparation strategy, for example ocr-local-long.")
@click.argument("path")
@click.pass_context
def import_command(ctx, force, strict, strategy_override, path):
    """Import documents into raw/source inventory without compiling wiki pages."""
    kb_dir = _find_kb_dir(ctx.obj.get("kb_dir_override"))
    if kb_dir is None:
        click.echo("No knowledge base found. Run `openkb init` first.")
        return
    _ensure_wiki_schema(kb_dir)

    target = Path(path)
    if not target.exists():
        click.echo(f"Path does not exist: {path}")
        return

    if target.is_dir():
        files = [
            f for f in sorted(target.rglob("*"))
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
        ]
        if not files:
            click.echo(f"No supported files found in {path}.")
            return
    else:
        if target.suffix.lower() not in SUPPORTED_EXTENSIONS:
            click.echo(
                f"Unsupported file type: {target.suffix}. "
                f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            )
            return
        files = [target]

    _run_import_files(
        files,
        kb_dir,
        force=force,
        strict=strict,
        strategy_override=strategy_override,
        path_label=path,
    )


@cli.command(name="backfill-ledger")
@click.pass_context
def backfill_ledger_command(ctx):
    """Backfill staged document ledger records from the current KB state."""
    kb_dir = _find_kb_dir(ctx.obj.get("kb_dir_override"))
    if kb_dir is None:
        click.echo("No knowledge base found. Run `openkb init` first.")
        return
    _ensure_wiki_schema(kb_dir)

    from openkb.document_ledger import backfill_document_ledger

    result = backfill_document_ledger(kb_dir)
    click.echo(
        "Document ledger backfill complete: "
        f"{result['added']} added, "
        f"{result['updated']} updated, "
        f"{result['unchanged']} unchanged, "
        f"{result['total']} total."
    )
    if result["added"] or result["updated"]:
        with _kb_mutation_lock(kb_dir):
            commit_kb_changes(kb_dir, "Backfill document ledger")


@cli.command()
@click.option("--strict", is_flag=True, default=False, help="Stop on the first rebuild failure.")
@click.pass_context
def rebuild(ctx, strict):
    """Rebuild all supported documents from raw/ using the force path."""
    kb_dir = _find_kb_dir(ctx.obj.get("kb_dir_override"))
    if kb_dir is None:
        click.echo("No knowledge base found. Run `openkb init` first.")
        return
    _ensure_wiki_schema(kb_dir)

    raw_dir = kb_dir / "raw"
    if not raw_dir.exists():
        click.echo("No raw/ directory found. Nothing to rebuild.")
        return

    files = [
        f for f in sorted(raw_dir.rglob("*"))
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    if not files:
        click.echo("No supported raw documents found. Nothing to rebuild.")
        return

    total = len(files)
    click.echo(f"Rebuilding {total} raw document(s) from {raw_dir}...")
    for i, f in enumerate(files, 1):
        click.echo(f"\n[{i}/{total}] ", nl=False)
        kwargs = {"force": True}
        if strict:
            kwargs["strict"] = True
        add_single_file(f, kb_dir, **kwargs)


@cli.command()
@click.argument("question")
@click.option("--save", is_flag=True, default=False, help="Save the answer to wiki/explorations/.")
@click.option(
    "--raw", "raw",
    is_flag=True, default=False,
    help="Show raw markdown source instead of rendered output (keeps tool-call colors).",
)
@click.pass_context
def query(ctx, question, save, raw):
    """Query the knowledge base with QUESTION."""
    kb_dir = _find_kb_dir(ctx.obj.get("kb_dir_override"))
    if kb_dir is None:
        click.echo("No knowledge base found. Run `openkb init` first.")
        return
    _ensure_wiki_schema(kb_dir)

    from openkb.agent.query import QueryReferenceTracker, format_query_exploration, run_query

    openkb_dir = kb_dir / ".openkb"
    config = load_config(openkb_dir / "config.yaml")
    profiles = _ordered_llm_profiles(config)
    if not profiles:
        click.echo("[ERROR] No enabled LLM profiles available.")
        return
    _setup_llm_key(kb_dir, profiles[0])
    model = profiles[0]["model"]

    reference_tracker = QueryReferenceTracker() if save else None
    try:
        answer = asyncio.run(
            run_query(
                question,
                kb_dir,
                model,
                stream=True,
                raw=raw,
                reference_tracker=reference_tracker,
            )
        )
    except Exception as exc:
        click.echo(f"[ERROR] Query failed: {exc}")
        return

    append_log(kb_dir / "wiki", "query", question)

    if save and answer:
        import re
        slug = re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")[:60]
        explore_dir = kb_dir / "wiki" / "explorations"
        explore_dir.mkdir(parents=True, exist_ok=True)
        explore_path = explore_dir / f"{slug}.md"
        explore_path.write_text(
            format_query_exploration(
                question,
                answer,
                reference_tracker.references() if reference_tracker is not None else [],
            ),
            encoding="utf-8",
        )
        click.echo(f"\nSaved to {explore_path}")
    commit_kb_changes(kb_dir, f"Query {question}")


@cli.command()
@click.option(
    "--resume", "-r", "resume",
    is_flag=False, flag_value="__latest__", default=None, metavar="[ID]",
    help="Resume the latest chat session, or a specific one by id or prefix.",
)
@click.option(
    "--list", "list_sessions_flag",
    is_flag=True, default=False,
    help="List chat sessions.",
)
@click.option(
    "--delete", "delete_id",
    default=None, metavar="ID",
    help="Delete a chat session by id or prefix.",
)
@click.option(
    "--no-color", "no_color",
    is_flag=True, default=False,
    help="Disable colored output.",
)
@click.option(
    "--raw", "raw",
    is_flag=True, default=False,
    help="Show raw markdown source instead of rendered output (keeps prompt and tool-call colors).",
)
@click.pass_context
def chat(ctx, resume, list_sessions_flag, delete_id, no_color, raw):
    """Start an interactive chat with the knowledge base."""
    kb_dir = _find_kb_dir(ctx.obj.get("kb_dir_override"))
    if kb_dir is None:
        click.echo("No knowledge base found. Run `openkb init` first.")
        return
    _ensure_wiki_schema(kb_dir)

    from openkb.agent.chat_session import (
        ChatSession,
        delete_session,
        list_sessions,
        load_session,
        relative_time,
        resolve_session_id,
    )

    if list_sessions_flag:
        sessions = list_sessions(kb_dir)
        if not sessions:
            click.echo("No chat sessions yet.")
            return
        click.echo(f"  {'ID':<22} {'TURNS':<6} {'UPDATED':<12} TITLE")
        click.echo(f"  {'-'*22} {'-'*6} {'-'*12} {'-'*30}")
        for s in sessions:
            rel = relative_time(s.get("updated_at", ""))
            title = s.get("title") or "(empty)"
            click.echo(
                f"  {s['id']:<22} {s['turn_count']:<6} {rel:<12} {title}"
            )
        click.echo(
            f"\n{len(sessions)} session(s) in {kb_dir / '.openkb' / 'chats'}"
        )
        return

    if delete_id is not None:
        try:
            resolved = resolve_session_id(kb_dir, delete_id)
        except ValueError as exc:
            click.echo(f"[ERROR] {exc}")
            return
        if not resolved:
            click.echo(f"No matching session: {delete_id}")
            return
        if delete_session(kb_dir, resolved):
            commit_kb_changes(kb_dir, f"Delete chat {resolved}")
            click.echo(f"Deleted session {resolved}")
        else:
            click.echo(f"Could not delete session: {resolved}")
        return

    openkb_dir = kb_dir / ".openkb"
    config = load_config(openkb_dir / "config.yaml")
    _setup_llm_key(kb_dir)

    if resume is not None:
        try:
            resolved = resolve_session_id(kb_dir, resume)
        except ValueError as exc:
            click.echo(f"[ERROR] {exc}")
            return
        if not resolved:
            if resume == "__latest__":
                click.echo("No previous chat sessions to resume.")
            else:
                click.echo(f"No matching session: {resume}")
            return
        session = load_session(kb_dir, resolved)
    else:
        model: str = config.get("model", DEFAULT_CONFIG["model"])
        language: str = config.get("language", "en")
        session = ChatSession.new(kb_dir, model, language)

    from openkb.agent.chat import run_chat

    try:
        asyncio.run(run_chat(kb_dir, session, no_color=no_color, raw=raw))
    except Exception as exc:
        click.echo(f"[ERROR] Chat failed: {exc}")


@cli.command()
@click.pass_context
def watch(ctx):
    """Watch the raw/ directory for new documents and process them automatically."""
    kb_dir = _find_kb_dir(ctx.obj.get("kb_dir_override"))
    if kb_dir is None:
        click.echo("No knowledge base found. Run `openkb init` first.")
        return
    _ensure_wiki_schema(kb_dir)

    from openkb.watcher import watch_directory

    raw_dir = kb_dir / "raw"
    raw_dir.mkdir(exist_ok=True)

    def on_new_files(paths):
        for p in paths:
            fp = Path(p)
            if fp.suffix.lower() not in SUPPORTED_EXTENSIONS:
                click.echo(
                    f"Skipping unsupported file type: {fp.suffix}. "
                    f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
                )
                continue
            add_single_file(fp, kb_dir)

    click.echo(f"Watching {raw_dir} for new documents. Press Ctrl+C to stop.")
    watch_directory(raw_dir, on_new_files)


async def run_lint(kb_dir: Path, *, fix: bool = False) -> Path | None:
    """Run structural + knowledge lint, write report, return report path.

    Returns ``None`` if the KB has no indexed documents (nothing to lint).
    Async because knowledge lint uses an LLM agent. Usable from CLI
    (via ``asyncio.run``) and directly from the chat REPL.
    """
    from openkb.lint import (
        cleanup_legacy_generated_placeholders,
        format_legacy_placeholder_fixes,
        run_structural_lint,
    )
    from openkb.agent.linter import (
        apply_coverage_gap_concept_candidates,
        extract_coverage_gap_concept_candidates,
        format_coverage_gap_fixes,
        format_coverage_gap_concept_candidates,
        run_knowledge_lint,
    )
    from openkb.llm_usage import llm_usage_context

    openkb_dir = kb_dir / ".openkb"

    # Skip lint entirely when the KB has no indexed documents
    hashes_file = openkb_dir / "hashes.json"
    if hashes_file.exists():
        hashes = json.loads(hashes_file.read_text(encoding="utf-8"))
    else:
        hashes = {}
    if not hashes:
        click.echo("Nothing to lint — no documents indexed yet. Run `openkb add` first.")
        return

    config = load_config(openkb_dir / "config.yaml")
    profiles = _ordered_llm_profiles(config)
    if profiles:
        _setup_llm_key(kb_dir, profiles[0])

    legacy_placeholder_fix_report = ""
    if fix:
        cleaned = cleanup_legacy_generated_placeholders(kb_dir / "wiki")
        legacy_placeholder_fix_report = format_legacy_placeholder_fixes(cleaned)
        _safe_echo(legacy_placeholder_fix_report)

    _safe_echo("Running structural lint...")
    structural_report = run_structural_lint(kb_dir)
    _safe_echo(structural_report)

    _safe_echo("Running knowledge lint...")
    knowledge_report = ""
    from openkb.model_pool import (
        configured_routes,
        is_model_pool_enabled,
        record_route_failure,
        record_route_success,
        select_model_route,
    )

    if is_model_pool_enabled(kb_dir):
        excluded_routes: set[str] = set()
        max_attempts = max(len(configured_routes(kb_dir)), 1)
        for attempt in range(max_attempts):
            try:
                route = select_model_route(kb_dir, exclude=excluded_routes)
            except RuntimeError as exc:
                knowledge_report = f"Knowledge lint failed: {exc}"
                break
            if attempt > 0:
                _safe_echo(f"Retrying knowledge lint with model route {route.profile_name}/{route.model}...")
            _setup_llm_key(kb_dir, _profile_from_model_route(route))
            try:
                with llm_usage_context(kb_dir, "lint"):
                    knowledge_report = await run_knowledge_lint(kb_dir, route.model)
                record_route_success(kb_dir, route.profile_id, route.model)
                break
            except Exception as exc:
                excluded_routes.add(route.route_id)
                record_route_failure(kb_dir, route.profile_id, route.model, exc)
                _probe_failed_model_route(kb_dir, route)
                knowledge_report = f"Knowledge lint failed: {exc}"
                continue
    else:
        if not profiles:
            knowledge_report = "Knowledge lint failed: No enabled LLM profiles available."
        else:
            for profile_index, profile in enumerate(profiles):
                if profile_index > 0:
                    _safe_echo(f"Retrying knowledge lint with LLM profile {_profile_label(profile)}...")
                _setup_llm_key(kb_dir, profile)
                try:
                    with llm_usage_context(kb_dir, "lint"):
                        knowledge_report = await run_knowledge_lint(kb_dir, profile["model"])
                    break
                except Exception as exc:
                    if not _is_retryable_llm_failure(exc) or profile_index == len(profiles) - 1:
                        knowledge_report = f"Knowledge lint failed: {exc}"
                        break
    _safe_echo(knowledge_report)

    coverage_candidates = extract_coverage_gap_concept_candidates(
        kb_dir / "wiki",
        knowledge_report,
    )
    coverage_report = format_coverage_gap_concept_candidates(coverage_candidates)
    if coverage_candidates:
        _safe_echo(coverage_report)

    coverage_fix_report = ""
    if fix:
        created = apply_coverage_gap_concept_candidates(kb_dir / "wiki", coverage_candidates)
        coverage_fix_report = format_coverage_gap_fixes(created)
        _safe_echo(coverage_fix_report)
        if created:
            _safe_echo(f"Created coverage-gap draft concept page(s): {len(created)}")

    # Write combined report
    reports_dir = kb_dir / "wiki" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    import datetime
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = reports_dir / f"lint_{timestamp}.md"
    report_content = (
        f"# Lint Report - {timestamp}\n\n"
        f"{legacy_placeholder_fix_report}\n\n"
        f"## Structural\n\n{structural_report}\n\n"
        f"## Semantic\n\n{knowledge_report}\n\n"
        f"{coverage_report}\n"
    )
    if coverage_fix_report:
        report_content += f"\n{coverage_fix_report}\n"
    report_path.write_text(report_content, encoding="utf-8")
    append_log(kb_dir / "wiki", "lint", f"report → {report_path.name}")
    commit_kb_changes(kb_dir, "Run lint")
    _safe_echo(f"\nReport written to {report_path}")
    return report_path


@cli.command()
@click.option(
    "--fix",
    is_flag=True,
    default=False,
    help="Create safe draft concept pages for lint coverage-gap candidates.",
)
@click.pass_context
def lint(ctx, fix):
    """Lint the knowledge base for structural and semantic inconsistencies."""
    kb_dir = _find_kb_dir(ctx.obj.get("kb_dir_override"))
    if kb_dir is None:
        click.echo("No knowledge base found. Run `openkb init` first.")
        return
    _ensure_wiki_schema(kb_dir)
    asyncio.run(run_lint(kb_dir, fix=fix))


def print_list(kb_dir: Path) -> None:
    """Print all documents in the knowledge base. Usable from CLI and chat REPL."""
    openkb_dir = kb_dir / ".openkb"
    hashes_file = openkb_dir / "hashes.json"
    if not hashes_file.exists():
        click.echo("No documents indexed yet.")
        return

    hashes = json.loads(hashes_file.read_text(encoding="utf-8"))
    if not hashes:
        click.echo("No documents indexed yet.")
        return

    # Display documents table with count in header
    doc_count = len(hashes)
    click.echo(f"Documents ({doc_count}):")
    click.echo(f"  {'Name':<40} {'Type':<12} {'Pages':<8}")
    click.echo(f"  {'-'*40} {'-'*12} {'-'*8}")
    for file_hash, meta in hashes.items():
        name = meta.get("name", "unknown")
        raw_type = meta.get("type", "unknown")
        display = _display_type(raw_type)
        pages = meta.get("pages", "")
        pages_str = str(pages) if pages else ""
        click.echo(f"  {name:<40} {display:<12} {pages_str:<8}")

    # Display summaries
    summaries_dir = kb_dir / "wiki" / "summaries"
    if summaries_dir.exists():
        summaries = sorted(p.stem for p in summaries_dir.glob("*.md"))
        if summaries:
            click.echo(f"\nSummaries ({len(summaries)}):")
            for s in summaries:
                click.echo(f"  - {s}")

    # Display companies
    companies_dir = kb_dir / "wiki" / "companies"
    if companies_dir.exists():
        companies = sorted(p.stem for p in companies_dir.glob("*.md"))
        if companies:
            click.echo(f"\nCompanies ({len(companies)}):")
            for c in companies:
                click.echo(f"  - {c}")

    industries_dir = kb_dir / "wiki" / "industries"
    if industries_dir.exists():
        industries = sorted(p.stem for p in industries_dir.glob("*.md"))
        if industries:
            click.echo(f"\nIndustries ({len(industries)}):")
            for industry in industries:
                click.echo(f"  - {industry}")

    # Display concepts
    concepts_dir = kb_dir / "wiki" / "concepts"
    if concepts_dir.exists():
        concepts = sorted(p.stem for p in concepts_dir.glob("*.md"))
        if concepts:
            click.echo(f"\nConcepts ({len(concepts)}):")
            for c in concepts:
                click.echo(f"  - {c}")

    # Display reports
    reports_dir = kb_dir / "wiki" / "reports"
    if reports_dir.exists():
        reports = sorted(p.name for p in reports_dir.glob("*.md"))
        if reports:
            click.echo(f"\nReports ({len(reports)}):")
            for r in reports:
                click.echo(f"  - {r}")


@cli.command(name="list")
@click.pass_context
def list_cmd(ctx):
    """List all documents in the knowledge base."""
    kb_dir = _find_kb_dir(ctx.obj.get("kb_dir_override"))
    if kb_dir is None:
        click.echo("No knowledge base found. Run `openkb init` first.")
        return
    _ensure_wiki_schema(kb_dir)
    print_list(kb_dir)


def _iter_related_page_groups(document: dict) -> list[tuple[str, list[dict]]]:
    labels = (
        ("summaries", "Summaries"),
        ("companies", "Companies"),
        ("industries", "Industries"),
        ("concepts", "Concepts"),
    )
    related = document.get("related_pages") or {}
    return [
        (label, list(related.get(key) or []))
        for key, label in labels
        if related.get(key)
    ]


def print_source_detail(document: dict) -> None:
    """Print one indexed source document and its generated wiki pages."""
    click.echo(f"Source document: {document.get('name', 'unknown')}")
    click.echo(f"  Hash:     {document.get('hash', '')}")
    click.echo(f"  Type:     {_display_type(str(document.get('type', 'unknown')))}")
    pages = document.get("pages")
    if pages:
        click.echo(f"  Pages:    {pages}")
    click.echo(f"  Raw:      {document.get('raw_path', '')}")
    if document.get("source_path"):
        click.echo(f"  Full text:{'':<2}{document.get('source_path')}")
    click.echo(f"  Summary:  {document.get('source_summary', '')}")
    click.echo(f"\nRelated pages ({document.get('related_count', 0)}):")
    groups = _iter_related_page_groups(document)
    if not groups:
        click.echo("  (none)")
        return
    for label, pages in groups:
        click.echo(f"  {label}:")
        for page in pages:
            suffix = " (shared)" if page.get("shared") else ""
            click.echo(f"    - {page.get('path')}{suffix}")


@cli.command(name="source")
@click.argument("selector", required=False)
@click.pass_context
def source_cmd(ctx, selector):
    """Show generated wiki pages associated with a source document."""
    kb_dir = _find_kb_dir(ctx.obj.get("kb_dir_override"))
    if kb_dir is None:
        click.echo("No knowledge base found. Run `openkb init` first.")
        return
    _ensure_wiki_schema(kb_dir)

    from openkb.source_relations import get_source_documents, get_source_document_detail

    if selector:
        try:
            document = get_source_document_detail(kb_dir, selector)
        except ValueError as exc:
            click.echo(f"[ERROR] {exc}")
            return
        print_source_detail(document)
        return

    documents = get_source_documents(kb_dir)
    if not documents:
        click.echo("No documents indexed yet.")
        return
    click.echo(f"Source documents ({len(documents)}):")
    click.echo(f"  {'Name':<40} {'Type':<12} {'Related':<8}")
    click.echo(f"  {'-'*40} {'-'*12} {'-'*8}")
    for document in documents:
        name = document.get("name", "unknown")
        display = _display_type(str(document.get("type", "unknown")))
        related = document.get("related_count", 0)
        click.echo(f"  {name:<40} {display:<12} {related:<8}")
    click.echo("\nUse `openkb source <name-or-hash>` to inspect related pages.")


@cli.command(name="delete-source")
@click.argument("selector")
@click.option("--yes", "-y", is_flag=True, default=False, help="Delete without prompting for confirmation.")
@click.pass_context
def delete_source_cmd(ctx, selector, yes):
    """Delete an indexed source document and safely clean generated pages."""
    kb_dir = _find_kb_dir(ctx.obj.get("kb_dir_override"))
    if kb_dir is None:
        click.echo("No knowledge base found. Run `openkb init` first.")
        return
    _ensure_wiki_schema(kb_dir)

    from openkb.source_relations import delete_source_document, get_source_document_detail

    try:
        document = get_source_document_detail(kb_dir, selector)
    except ValueError as exc:
        click.echo(f"[ERROR] {exc}")
        return

    owned_pages = [
        page["path"]
        for _label, pages in _iter_related_page_groups(document)
        for page in pages
        if not page.get("shared")
    ]
    shared_pages = [
        page["path"]
        for _label, pages in _iter_related_page_groups(document)
        for page in pages
        if page.get("shared")
    ]
    if not yes:
        click.echo(f"Delete source document: {document.get('name', 'unknown')}")
        click.echo(f"  Owned pages to remove: {len(owned_pages)}")
        click.echo(f"  Shared pages to update: {len(shared_pages)}")
        if not click.confirm("Continue?"):
            click.echo("Aborted.")
            return

    result = delete_source_document(kb_dir, selector)
    append_log(kb_dir / "wiki", "delete-source", str(result["document"].get("name", selector)))
    commit_kb_changes(kb_dir, f"Delete source {result['document'].get('name', selector)}")

    click.echo(f"Deleted source document: {result['document'].get('name', 'unknown')}")
    click.echo(f"Removed pages: {len(result['removed_pages'])}")
    for page in result["removed_pages"]:
        click.echo(f"  - {page}")
    click.echo(f"Updated shared pages: {len(result['updated_pages'])}")
    for page in result["updated_pages"]:
        click.echo(f"  - {page}")
    if result["removed_files"]:
        click.echo(f"Removed files: {len(result['removed_files'])}")
        for path in result["removed_files"]:
            click.echo(f"  - {path}")


def print_status(kb_dir: Path) -> None:
    """Print knowledge base status. Usable from CLI and chat REPL."""
    wiki_dir = kb_dir / "wiki"
    subdirs = [
        "sources",
        "summaries",
        "companies",
        "industries",
        "concepts",
        "reports",
    ]

    click.echo("Knowledge Base Status:")
    click.echo(f"  {'Directory':<20} {'Files':<10}")
    click.echo(f"  {'-'*20} {'-'*10}")

    for subdir in subdirs:
        path = wiki_dir / subdir
        if path.exists():
            count = len(list(path.glob("*.md")))
        else:
            count = 0
        click.echo(f"  {subdir:<20} {count:<10}")

    # Raw files
    raw_dir = kb_dir / "raw"
    if raw_dir.exists():
        raw_count = len([f for f in raw_dir.iterdir() if f.is_file()])
        click.echo(f"  {'raw':<20} {raw_count:<10}")

    # Hash registry summary
    openkb_dir = kb_dir / ".openkb"
    hashes_file = openkb_dir / "hashes.json"
    if hashes_file.exists():
        hashes = json.loads(hashes_file.read_text(encoding="utf-8"))
        click.echo(f"\n  Total indexed: {len(hashes)} document(s)")
    ledger_file = openkb_dir / "document_ledger.json"
    if ledger_file.exists():
        try:
            ledger_payload = json.loads(ledger_file.read_text(encoding="utf-8") or "{}")
            ledger_count = len(ledger_payload.get("documents", {})) if isinstance(ledger_payload, dict) else 0
        except json.JSONDecodeError:
            ledger_count = 0
        click.echo(f"  Ledger records: {ledger_count} document(s)")

    # Last compile time: newest file in wiki/summaries/
    summaries_dir = wiki_dir / "summaries"
    if summaries_dir.exists():
        summaries = list(summaries_dir.glob("*.md"))
        if summaries:
            newest_summary = max(summaries, key=lambda p: p.stat().st_mtime)
            import datetime
            mtime = datetime.datetime.fromtimestamp(newest_summary.stat().st_mtime)
            click.echo(f"  Last compile:  {mtime.strftime('%Y-%m-%d %H:%M:%S')}")

    # Last lint time: newest file in wiki/reports/
    reports_dir = wiki_dir / "reports"
    if reports_dir.exists():
        reports = list(reports_dir.glob("*.md"))
        if reports:
            newest_report = max(reports, key=lambda p: p.stat().st_mtime)
            import datetime
            mtime = datetime.datetime.fromtimestamp(newest_report.stat().st_mtime)
            click.echo(f"  Last lint:     {mtime.strftime('%Y-%m-%d %H:%M:%S')}")


@cli.command()
@click.pass_context
def status(ctx):
    """Show the current status of the knowledge base."""
    kb_dir = _find_kb_dir(ctx.obj.get("kb_dir_override"))
    if kb_dir is None:
        click.echo("No knowledge base found. Run `openkb init` first.")
        return
    _ensure_wiki_schema(kb_dir)
    print_status(kb_dir)


@cli.command()
@click.option("--host", default="0.0.0.0", show_default=True, help="Host to bind the local client server.")
@click.option("--port", default=8765, show_default=True, type=int, help="Port for the local client server.")
@click.option("--no-browser", is_flag=True, default=False, help="Do not open a browser automatically.")
def client(host, port, no_browser):
    """Start the local OpenKB browser client."""
    try:
        from openkb.client.server import ClientDependencyError, serve_client

        serve_client(host=host, port=port, open_browser=not no_browser)
    except ClientDependencyError as exc:
        raise click.ClickException(
            f"{exc}\nRun: pip install \"openkb[client]\""
        ) from exc
