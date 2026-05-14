"""Local HTTP server for the OpenKB browser client."""
from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import json
import os
import shutil
import tempfile
import threading
import time
import webbrowser
from collections.abc import AsyncIterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from openkb.client.jobs import JobRegistry, JobStopped, default_registry
from openkb.kb_git import commit_kb_changes
from openkb.client import kb as kb_helpers
from openkb.config import DEFAULT_CONFIG, load_config, load_global_config


class ClientDependencyError(RuntimeError):
    """Raised when optional Web client dependencies are unavailable."""


def _import_web_dependencies():
    try:
        import uvicorn
        from fastapi import FastAPI, File, HTTPException, Query, UploadFile
        from fastapi.responses import FileResponse, Response, StreamingResponse
        from fastapi.staticfiles import StaticFiles
    except ModuleNotFoundError as exc:
        raise ClientDependencyError(
            "OpenKB client dependencies are not installed."
        ) from exc
    # `create_app()` defines routes under postponed evaluation of annotations,
    # so FastAPI resolves names like `UploadFile` from module globals.
    globals()["UploadFile"] = UploadFile
    globals()["StreamingResponse"] = StreamingResponse
    return FastAPI, File, FileResponse, HTTPException, Query, Response, StaticFiles, UploadFile, uvicorn


def _default_kb_dir() -> Path | None:
    config = load_global_config()
    default = config.get("default_kb")
    if not default:
        return None
    path = Path(default)
    return path if kb_helpers.is_kb_dir(path) else None


def _resolve_kb_dir(raw: str | None = None) -> Path:
    if raw:
        return kb_helpers.require_kb_dir(Path(raw))
    default = _default_kb_dir()
    if default is None:
        raise kb_helpers.ClientError("No default knowledge base configured.")
    return kb_helpers.require_kb_dir(default)


def _sse_event(name: str, payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {name}\ndata: {data}\n\n"


def _static_dir() -> Path:
    return Path(__file__).resolve().parent / "static"


def _callable_accepts_keyword(fn: Any, name: str) -> bool:
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    return name in signature.parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )


_LOCAL_CORS_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"


def _cors_settings() -> dict[str, Any]:
    raw_origins = os.getenv("OPENKB_CLIENT_CORS_ORIGINS", "").strip()
    if not raw_origins:
        return {
            "allow_origins": [],
            "allow_origin_regex": _LOCAL_CORS_ORIGIN_REGEX,
            "allow_credentials": True,
        }

    origins = [origin.strip().rstrip("/") for origin in raw_origins.split(",") if origin.strip()]
    if "*" in origins:
        return {
            "allow_origins": ["*"],
            "allow_origin_regex": None,
            "allow_credentials": False,
        }

    return {
        "allow_origins": origins,
        "allow_origin_regex": _LOCAL_CORS_ORIGIN_REGEX,
        "allow_credentials": True,
    }


def _route_profile_payload(route: Any) -> dict[str, str]:
    return {
        "id": str(getattr(route, "profile_id", "") or ""),
        "name": str(getattr(route, "profile_name", "") or ""),
        "model": str(getattr(route, "model", "") or ""),
        "wire_api": str(getattr(route, "wire_api", "") or ""),
        "base_url": str(getattr(route, "base_url", "") or ""),
        "provider": str(getattr(route, "provider", "") or ""),
        "reasoning_effort": str(getattr(route, "reasoning_effort", "") or ""),
        "thinking_enabled": bool(getattr(route, "thinking_enabled", False)),
        "api_key_env": str(getattr(route, "api_key_env", "") or ""),
    }


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


@contextmanager
def _runtime_env_override():
    snapshot = {key: os.environ.get(key) for key in _RUNTIME_ENV_KEYS}
    try:
        yield
    finally:
        for key, value in snapshot.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _mask_secret(secret: str) -> str:
    value = secret.strip()
    if not value:
        return "(empty)"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _effective_llm_request_info(*, model: str, wire_api: str, base_url: str) -> dict[str, str]:
    from openkb.llm_runtime import normalize_model_name

    effective_model = normalize_model_name(model)
    endpoint_path = "/responses" if wire_api == "responses" else "/chat/completions"
    base = base_url.rstrip("/")
    effective_url = f"{base}{endpoint_path}" if base else endpoint_path
    return {
        "effective_model": effective_model,
        "effective_wire_api": wire_api,
        "effective_url": effective_url,
    }


def _test_llm_config(payload: dict[str, Any]) -> dict[str, Any]:
    from openkb.llm_runtime import completion, normalize_model_name
    from openkb.config import GLOBAL_CONFIG_DIR

    target_kb = _resolve_kb_dir(payload.get("kb_dir"))
    config = load_config(target_kb / ".openkb" / "config.yaml")
    model = str(payload.get("model") or config.get("model") or DEFAULT_CONFIG["model"]).strip()
    if not model:
        raise ValueError("Model is required.")
    wire_api = str(payload.get("wire_api") or config.get("wire_api") or DEFAULT_CONFIG["wire_api"]).strip().lower()
    base_url = str(payload.get("base_url") or config.get("base_url") or "").strip().rstrip("/")
    if base_url and wire_api != "responses" and model.startswith("openai/"):
        model = model.split("/", 1)[1]
    else:
        model = normalize_model_name(model)
    provider = str(payload.get("provider") or "generic").strip().lower()
    reasoning_effort = str(payload.get("reasoning_effort") or "").strip().lower()
    thinking_enabled = bool(payload.get("thinking_enabled", False))
    api_key = str(payload.get("api_key") or "").strip()
    request_info = _effective_llm_request_info(model=model, wire_api=wire_api, base_url=base_url)

    with _runtime_env_override():
        env_file = target_kb / ".env"
        if env_file.exists():
            load_dotenv(env_file, override=True)
        global_env = GLOBAL_CONFIG_DIR / ".env"
        if global_env.exists():
            load_dotenv(global_env, override=False)

        if wire_api:
            os.environ["OPENKB_WIRE_API"] = wire_api
        if provider:
            os.environ["OPENKB_MODEL_PROVIDER"] = provider
        else:
            os.environ.pop("OPENKB_MODEL_PROVIDER", None)
        if reasoning_effort:
            os.environ["OPENKB_MODEL_REASONING_EFFORT"] = reasoning_effort
        else:
            os.environ.pop("OPENKB_MODEL_REASONING_EFFORT", None)
        if thinking_enabled:
            os.environ["OPENKB_DEEPSEEK_THINKING_ENABLED"] = "true"
        else:
            os.environ.pop("OPENKB_DEEPSEEK_THINKING_ENABLED", None)

        if "base_url" in payload:
            if base_url:
                os.environ["OPENAI_BASE_URL"] = base_url
                os.environ["OPENAI_API_BASE"] = base_url
            else:
                os.environ.pop("OPENAI_BASE_URL", None)
                os.environ.pop("OPENAI_API_BASE", None)

        if api_key:
            os.environ["LLM_API_KEY"] = api_key
            os.environ["OPENAI_API_KEY"] = api_key
        else:
            saved_key = os.environ.get("LLM_API_KEY", "").strip() or os.environ.get("OPENAI_API_KEY", "").strip()
            if saved_key:
                os.environ["LLM_API_KEY"] = saved_key
                os.environ["OPENAI_API_KEY"] = saved_key

        try:
            completion_kwargs: dict[str, Any] = {
                "max_tokens": 8,
                "timeout": 20,
            }
            if base_url and wire_api != "responses":
                completion_kwargs["custom_llm_provider"] = "custom_openai"
            result = completion(
                model=model,
                messages=[{"role": "user", "content": "Ping. Reply with exactly pong."}],
                **completion_kwargs,
            )
        except Exception as exc:
            raise ValueError(
                "\n".join(
                    [
                        f"LLM test failed: {exc}",
                        f"effective_model: {request_info['effective_model']}",
                        f"effective_wire_api: {request_info['effective_wire_api'] or '(empty)'}",
                        f"effective_url: {request_info['effective_url']}",
                        f"api_key: {_mask_secret(api_key or os.environ.get('OPENAI_API_KEY', '') or os.environ.get('LLM_API_KEY', ''))}",
                    ]
                )
            ) from exc

    return {
        "ok": True,
        "message": "LLM test succeeded.",
        "model": model,
        "wire_api": wire_api,
        "base_url": base_url,
        **request_info,
        "response_text": result.text,
    }


@contextmanager
def _workflow_llm_context(target_kb: Path, *, model: str | None = None, feature: str = ""):
    """Hydrate the active LLM profile for one staged workflow job."""
    from openkb.cli import _ordered_llm_profiles, _runtime_context_for_profile, _setup_llm_key
    from openkb.llm_runtime import llm_runtime_context
    from openkb.llm_usage import llm_usage_context

    config = load_config(target_kb / ".openkb" / "config.yaml")
    profiles = _ordered_llm_profiles(config)
    profile = profiles[0] if profiles else None
    _setup_llm_key(target_kb, profile)
    runtime_config = _runtime_context_for_profile(target_kb, profile or config)
    if model:
        selected_model = model
    elif profile is not None:
        selected_model = str(profile.get("model") or config.get("model") or DEFAULT_CONFIG["model"]).strip()
    else:
        selected_model = str(config.get("model") or DEFAULT_CONFIG["model"]).strip()
    with llm_runtime_context(runtime_config), llm_usage_context(target_kb, feature):
        yield selected_model


def _cleanup_staged_upload(target_kb: Path, staged: Path) -> None:
    uploads_root = (target_kb / ".openkb" / "uploads").resolve()
    resolved = staged.resolve()
    if not resolved.is_relative_to(uploads_root):
        return
    if resolved.exists() and resolved.is_file():
        resolved.unlink()
    parent = resolved.parent
    if parent != uploads_root and parent.exists():
        shutil.rmtree(parent, ignore_errors=True)


def _probe_model_pool_profile(target_kb: Path, profile_id: str) -> dict[str, Any]:
    from openkb.model_pool import configured_routes, probe_model_route, record_route_failure, record_route_success

    profile = kb_helpers.get_model_pool_profile(target_kb, profile_id)
    if not profile.get("enabled", True):
        return kb_helpers.save_model_pool_profile_status(
            target_kb,
            profile_id,
            {
                "health": "disabled",
                "last_checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "latency_ms": None,
                "available_models": [],
                "failed_models": {},
                "last_error": "",
            },
        )

    routes = [route for route in configured_routes(target_kb) if route.profile_id == profile_id]
    available_models: list[str] = []
    failed_models: dict[str, str] = {}
    latencies: list[int] = []
    for route in routes:
        started = time.perf_counter()
        try:
            probe_model_route(target_kb, route, api_key=str(profile.get("api_key") or ""))
            latency = int((time.perf_counter() - started) * 1000)
            available_models.append(route.model)
            latencies.append(latency)
            record_route_success(target_kb, route.profile_id, route.model, latency_ms=latency)
        except Exception as exc:
            failed_models[route.model] = str(exc).splitlines()[0]
            record_route_failure(target_kb, route.profile_id, route.model, exc)

    if available_models and failed_models:
        health = "degraded"
    elif available_models:
        health = "healthy"
    else:
        health = "offline"
    consecutive_failures = 0 if available_models else int(profile.get("consecutive_failures") or 0) + 1
    last_error = "; ".join(f"{model}: {error}" for model, error in failed_models.items())
    return kb_helpers.save_model_pool_profile_status(
        target_kb,
        profile_id,
        {
            "health": health,
            "last_checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "latency_ms": min(latencies) if latencies else None,
            "consecutive_failures": consecutive_failures,
            "available_models": available_models,
            "failed_models": failed_models,
            "last_error": last_error,
        },
    )


def create_app(registry: JobRegistry | None = None):
    """Create the FastAPI application.

    FastAPI and Uvicorn are imported lazily so base OpenKB installs can still
    import the package and use the CLI without the optional client extra.
    """
    FastAPI, File, FileResponse, HTTPException, Query, Response, StaticFiles, UploadFile, _uvicorn = _import_web_dependencies()
    from fastapi.middleware.cors import CORSMiddleware
    registry = registry or default_registry
    app = FastAPI(title="OpenKB Client", version="0.1.0")
    cors_settings = _cors_settings()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_settings["allow_origins"],
        allow_origin_regex=cors_settings["allow_origin_regex"],
        allow_credentials=cors_settings["allow_credentials"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    static_dir = _static_dir()

    def translate_error(exc: Exception) -> HTTPException:
        if isinstance(exc, FileNotFoundError):
            return HTTPException(status_code=404, detail=str(exc))
        if isinstance(exc, (kb_helpers.ClientError, kb_helpers.PathSecurityError, ValueError)):
            return HTTPException(status_code=400, detail=str(exc))
        return HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        default = _default_kb_dir()
        return {"ok": True, "default_kb": str(default) if default else None}

    @app.get("/api/kbs")
    def kbs() -> dict[str, Any]:
        return kb_helpers.get_known_kbs()

    @app.post("/api/kbs/use")
    def use_kb(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return kb_helpers.use_kb(Path(str(payload["path"])))
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.post("/api/kbs/init")
    def init_kb(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            model = str(payload.get("model") or DEFAULT_CONFIG["model"])
            return kb_helpers.init_kb(
                Path(str(payload["path"])),
                model=model,
                language=str(payload.get("language") or DEFAULT_CONFIG["language"]),
                pageindex_threshold=int(payload.get("pageindex_threshold") or DEFAULT_CONFIG["pageindex_threshold"]),
                compile_max_concurrency=int(payload.get("compile_max_concurrency") or DEFAULT_CONFIG["compile_max_concurrency"]),
                ingest_gate_enabled=payload.get("ingest_gate_enabled"),
                ingest_gate_pass_threshold=payload.get("ingest_gate_pass_threshold"),
                ingest_gate_hold_threshold=payload.get("ingest_gate_hold_threshold"),
                ingest_gate_hard_reject_enabled=payload.get("ingest_gate_hard_reject_enabled"),
                ingest_gate_log_all_decisions=payload.get("ingest_gate_log_all_decisions"),
                ingest_gate_allow_force_pass=payload.get("ingest_gate_allow_force_pass"),
                ingest_gate_allow_force_reject=payload.get("ingest_gate_allow_force_reject"),
                wire_api=payload.get("wire_api"),
                base_url=str(payload.get("base_url") or ""),
                api_key=str(payload.get("api_key") or ""),
                make_default=True,
            )
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.get("/api/status")
    def status(kb_dir: str | None = Query(default=None)) -> dict[str, Any]:
        try:
            return kb_helpers.get_status_data(_resolve_kb_dir(kb_dir))
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.get("/api/documents")
    def documents(
        kb_dir: str | None = Query(default=None),
        q: str | None = Query(default=None),
        workflow_status: str | None = Query(default=None),
        ingest_state: str | None = Query(default=None),
        ocr_state: str | None = Query(default=None),
        source_state: str | None = Query(default=None),
        summary_state: str | None = Query(default=None),
        review_state: str | None = Query(default=None),
        promotion_state: str | None = Query(default=None),
    ) -> dict[str, Any]:
        try:
            return kb_helpers.get_document_data(
                _resolve_kb_dir(kb_dir),
                query=q or "",
                workflow_status=workflow_status or "",
                ingest_state=ingest_state or "",
                ocr_state=ocr_state or "",
                source_state=source_state or "",
                summary_state=summary_state or "",
                review_state=review_state or "",
                promotion_state=promotion_state or "",
            )
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.get("/api/documents/{selector}")
    def document_detail(selector: str, kb_dir: str | None = Query(default=None)) -> dict[str, Any]:
        try:
            return kb_helpers.get_source_document_data(_resolve_kb_dir(kb_dir), selector)
        except Exception as exc:
            raise translate_error(exc) from exc

    def _submit_add_job(
        target_kb: Path,
        source: Path,
        *,
        preset_files: list[Path] | None = None,
        message_source: str | None = None,
        strategy_override: str | None = None,
        force: bool = False,
        force_gate_pass: bool = False,
        force_gate_reject: bool = False,
        gate_reason: str = "",
        gate_operator: str = "",
    ):
        def run(job):
            from openkb.cli import SUPPORTED_EXTENSIONS, _healthy_model_pool_routes, add_single_file

            def progress(message: str) -> None:
                job.raise_if_stopped()
                job.add_log(message)

            class _ParallelChildJobProxy:
                """Forward logs/stop checks without letting child tasks overwrite batch progress."""

                def add_log(self, message: str, level: str = "info") -> None:
                    job.raise_if_stopped()
                    job.add_log(message, level=level)

                def set_message(self, message: str) -> None:
                    self.add_log(message)

                def set_progress(self, current: int, total: int) -> None:
                    return None

                def raise_if_stopped(self) -> None:
                    job.raise_if_stopped()

            files: list[Path]
            if preset_files is not None:
                files = list(preset_files)
            else:
                if not source.exists():
                    raise FileNotFoundError(source)
                if source.is_dir():
                    job.add_log(f"Scanning folder: {source}")
                    unsupported = [
                        p
                        for p in sorted(source.rglob("*"))
                        if p.is_file() and p.suffix.lower() not in SUPPORTED_EXTENSIONS
                    ]
                    if unsupported:
                        job.add_log(f"Skipped {len(unsupported)} unsupported file(s)")
                    files = [
                        p
                        for p in sorted(source.rglob("*"))
                        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
                    ]
                else:
                    files = [source]
            if not files:
                raise ValueError("No supported files found.")
            job.set_progress(0, len(files))
            job.add_log(f"Found {len(files)} supported file(s)")
            routes = _healthy_model_pool_routes(target_kb) if len(files) > 1 else []
            if len(routes) >= 2:
                max_workers = min(len(routes), len(files))
                job.add_log(f"Importing with {max_workers} parallel task(s) across healthy LLM profile(s).")
            else:
                max_workers = 1
            added = 0
            failures: list[dict[str, str]] = []
            completed = 0
            state_lock = threading.Lock()

            def run_one(index: int, file_path: Path, *, model_route=None, child_job=None) -> None:
                job.raise_if_stopped()
                if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    raise ValueError(f"Unsupported file type: {file_path.suffix}")
                job.set_message(f"Adding {index}/{len(files)}: {file_path.name}")
                if model_route is not None:
                    route_name = f"{model_route.profile_name}/{model_route.model}"
                    job.add_log(f"Dispatching {index}/{len(files)}: {file_path.name} -> {route_name}")
                add_kwargs: dict[str, Any] = {
                    "strict": True,
                    "progress_callback": progress,
                }
                if strategy_override:
                    add_kwargs["strategy_override"] = strategy_override
                if force:
                    add_kwargs["force"] = True
                if _callable_accepts_keyword(add_single_file, "force_gate_pass"):
                    add_kwargs["force_gate_pass"] = force_gate_pass
                if _callable_accepts_keyword(add_single_file, "force_gate_reject"):
                    add_kwargs["force_gate_reject"] = force_gate_reject
                if _callable_accepts_keyword(add_single_file, "gate_reason"):
                    add_kwargs["gate_reason"] = gate_reason
                if _callable_accepts_keyword(add_single_file, "gate_operator"):
                    add_kwargs["gate_operator"] = gate_operator
                if child_job is not None and _callable_accepts_keyword(add_single_file, "job"):
                    add_kwargs["job"] = child_job
                elif _callable_accepts_keyword(add_single_file, "job"):
                    add_kwargs["job"] = job
                if model_route is not None and _callable_accepts_keyword(add_single_file, "model_route"):
                    add_kwargs["model_route"] = model_route
                add_single_file(
                    file_path,
                    target_kb,
                    **add_kwargs,
                )
                job.raise_if_stopped()

            def record_success(index: int, file_path: Path) -> None:
                nonlocal added, completed
                with state_lock:
                    added += 1
                    completed += 1
                    job.set_progress(completed, len(files))
                    job.add_log(f"Finished {index}/{len(files)}: {file_path.name}")

            def record_failure(index: int, file_path: Path, exc: Exception) -> None:
                nonlocal completed
                if isinstance(exc, JobStopped):
                    raise exc
                with state_lock:
                    job.add_log(f"Failed {file_path.name}: {exc}", level="error")
                    if len(files) == 1:
                        raise exc
                    failures.append({
                        "name": file_path.name,
                        "path": str(file_path),
                        "error": str(exc),
                    })
                    completed += 1
                    job.set_progress(completed, len(files))

            if max_workers == 1:
                for index, file_path in enumerate(files, 1):
                    try:
                        run_one(index, file_path)
                    except Exception as exc:
                        record_failure(index, file_path, exc)
                        continue
                    record_success(index, file_path)
            else:
                route_buckets: list[list[tuple[int, Path]]] = [[] for _ in range(max_workers)]
                for offset, item in enumerate(enumerate(files, 1)):
                    route_buckets[offset % max_workers].append(item)

                def worker(route, bucket: list[tuple[int, Path]]) -> None:
                    child_job = _ParallelChildJobProxy()
                    for index, file_path in bucket:
                        try:
                            run_one(index, file_path, model_route=route, child_job=child_job)
                        except Exception as exc:
                            record_failure(index, file_path, exc)
                            continue
                        record_success(index, file_path)

                with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = [
                        executor.submit(worker, route, bucket)
                        for route, bucket in zip(routes[:max_workers], route_buckets)
                        if bucket
                    ]
                    for future in concurrent.futures.as_completed(futures):
                        future.result()
            if failures and added == 0:
                if len(failures) == 1:
                    raise RuntimeError(failures[0]["error"])
                raise RuntimeError(f"All {len(failures)} file(s) failed. First failure: {failures[0]['error']}")
            if failures:
                failure_word = "failure" if len(failures) == 1 else "failures"
                job.add_log(
                    f"Completed with {added} added and {len(failures)} {failure_word}.",
                    level="warning",
                )
            job.raise_if_stopped()
            for staged in preset_files or []:
                try:
                    _cleanup_staged_upload(target_kb, staged)
                except OSError:
                    pass
            return {
                "added": added,
                "failed": len(failures),
                "total": len(files),
                "failures": failures,
            }

        job = registry.submit("add", run, message=f"Queued add: {message_source or source}")
        return job

    def _submit_import_job(
        target_kb: Path,
        source: Path,
        *,
        preset_files: list[Path] | None = None,
        message_source: str | None = None,
        strategy_override: str | None = None,
        force: bool = False,
    ):
        def run(job):
            from openkb.cli import SUPPORTED_EXTENSIONS
            from openkb.workflows.import_pipeline import import_document_source

            files: list[Path]
            if preset_files is not None:
                files = list(preset_files)
            else:
                if not source.exists():
                    raise FileNotFoundError(source)
                if source.is_dir():
                    job.add_log(f"Scanning folder: {source}")
                    unsupported = [
                        p
                        for p in sorted(source.rglob("*"))
                        if p.is_file() and p.suffix.lower() not in SUPPORTED_EXTENSIONS
                    ]
                    if unsupported:
                        job.add_log(f"Skipped {len(unsupported)} unsupported file(s)")
                    files = [
                        p
                        for p in sorted(source.rglob("*"))
                        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
                    ]
                else:
                    files = [source]
            if not files:
                raise ValueError("No supported files found.")

            imported = 0
            skipped = 0
            failures: list[dict[str, str]] = []
            results: list[dict[str, Any]] = []
            job.set_progress(0, len(files))
            job.add_log(f"Found {len(files)} supported file(s)")
            for index, file_path in enumerate(files, 1):
                job.raise_if_stopped()
                if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    raise ValueError(f"Unsupported file type: {file_path.suffix}")
                job.set_message(f"Importing {index}/{len(files)}: {file_path.name}")
                try:
                    result = import_document_source(
                        file_path,
                        target_kb,
                        force=force,
                        strategy_override=strategy_override,
                        job=job,
                    )
                except Exception as exc:
                    if len(files) == 1:
                        raise exc
                    failures.append({
                        "name": file_path.name,
                        "path": str(file_path),
                        "error": str(exc),
                    })
                    job.add_log(f"Failed {file_path.name}: {exc}", level="error")
                else:
                    results.append(result)
                    if result.get("skipped"):
                        skipped += 1
                        job.add_log(f"Skipped already-known file: {file_path.name}")
                    else:
                        imported += 1
                        job.add_log(f"Imported source artifacts: {file_path.name}")
                job.set_progress(index, len(files))

            if failures and imported == 0 and skipped == 0:
                raise RuntimeError(f"All {len(failures)} file(s) failed. First failure: {failures[0]['error']}")
            if imported:
                commit_kb_changes(target_kb, f"Import {imported} source document(s)")
            for staged in preset_files or []:
                try:
                    _cleanup_staged_upload(target_kb, staged)
                except OSError:
                    pass
            return {
                "imported": imported,
                "skipped": skipped,
                "failed": len(failures),
                "total": len(files),
                "failures": failures,
                "documents": results,
            }

        job = registry.submit("import", run, message=f"Queued import: {message_source or source}")
        return job

    @app.post("/api/documents/add")
    def add_document(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            target_kb = _resolve_kb_dir(payload.get("kb_dir"))
            source = Path(str(payload["path"]))
        except Exception as exc:
            raise translate_error(exc) from exc

        if bool(payload.get("import_only", False)):
            job = _submit_import_job(
                target_kb,
                source,
                strategy_override=str(payload.get("strategy_override") or "").strip() or None,
                force=bool(payload.get("force", False)),
            )
        else:
            job = _submit_add_job(
                target_kb,
                source,
                strategy_override=str(payload.get("strategy_override") or "").strip() or None,
                force_gate_pass=bool(payload.get("force_gate_pass", False)),
                force_gate_reject=bool(payload.get("force_gate_reject", False)),
                gate_reason=str(payload.get("gate_reason") or "").strip(),
                gate_operator=str(payload.get("gate_operator") or "").strip(),
            )
        return {"job": job.to_dict()}

    @app.post("/api/documents/import")
    def import_document(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            target_kb = _resolve_kb_dir(payload.get("kb_dir"))
            source = Path(str(payload["path"]))
        except Exception as exc:
            raise translate_error(exc) from exc

        job = _submit_import_job(
            target_kb,
            source,
            strategy_override=str(payload.get("strategy_override") or "").strip() or None,
            force=bool(payload.get("force", False)),
        )
        return {"job": job.to_dict()}

    @app.post("/api/documents/summarize")
    def summarize_documents(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            target_kb = _resolve_kb_dir(payload.get("kb_dir"))
            file_hashes = payload.get("file_hashes")
            if file_hashes is not None and not isinstance(file_hashes, list):
                raise ValueError("file_hashes must be a list when provided.")
        except Exception as exc:
            raise translate_error(exc) from exc

        def run(job):
            from openkb.workflows.summary_pipeline import summarize_documents as run_summarize_documents

            job.raise_if_stopped()
            job.add_log("Generating document summaries")
            with _workflow_llm_context(
                target_kb,
                model=str(payload.get("model") or "").strip() or None,
                feature="summary",
            ) as selected_model:
                result = run_summarize_documents(
                    target_kb,
                    file_hashes=[str(item) for item in file_hashes] if file_hashes else None,
                    model=selected_model,
                    force=bool(payload.get("force", False)),
                )
            if result["generated"]:
                commit_kb_changes(target_kb, f"Summarize {result['generated']} document(s)")
            job.raise_if_stopped()
            job.add_log(
                f"Generated {result['generated']} summary document(s), "
                f"skipped {result['skipped']}, failed {result['failed']}"
            )
            return result

        job = registry.submit("summarize", run, message="Queued document summarization")
        return {"job": job.to_dict()}

    @app.post("/api/documents/review-summary")
    def review_summaries(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            target_kb = _resolve_kb_dir(payload.get("kb_dir"))
            reviews = payload.get("reviews")
            if not isinstance(reviews, list):
                raise ValueError("reviews must be a list.")
        except Exception as exc:
            raise translate_error(exc) from exc

        def run(job):
            from openkb.workflows.summary_pipeline import update_summary_reviews

            job.raise_if_stopped()
            job.add_log("Updating summary review metadata")
            result = update_summary_reviews(target_kb, reviews)
            if result["updated"]:
                commit_kb_changes(target_kb, f"Review {result['updated']} summary document(s)")
            job.raise_if_stopped()
            job.add_log(f"Updated {result['updated']} review record(s), failed {result['failed']}")
            return result

        job = registry.submit("review_summary", run, message="Queued summary review update")
        return {"job": job.to_dict()}

    @app.post("/api/documents/promote")
    def promote_documents(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            target_kb = _resolve_kb_dir(payload.get("kb_dir"))
            file_hashes = payload.get("file_hashes")
            if file_hashes is not None and not isinstance(file_hashes, list):
                raise ValueError("file_hashes must be a list when provided.")
        except Exception as exc:
            raise translate_error(exc) from exc

        def run(job):
            from openkb.workflows.promotion_pipeline import promote_summary_documents

            job.raise_if_stopped()
            job.add_log("Promoting approved summaries")
            with _workflow_llm_context(
                target_kb,
                model=str(payload.get("model") or "").strip() or None,
                feature="promotion",
            ) as selected_model:
                result = promote_summary_documents(
                    target_kb,
                    file_hashes=[str(item) for item in file_hashes] if file_hashes else None,
                    model=selected_model,
                    force=bool(payload.get("force", False)),
                )
            if result["promoted"]:
                commit_kb_changes(target_kb, f"Promote {result['promoted']} summary document(s)")
            job.raise_if_stopped()
            job.add_log(
                f"Promoted {result['promoted']} summary document(s), "
                f"skipped {result['skipped']}, failed {result['failed']}"
            )
            return result

        job = registry.submit("promote", run, message="Queued summary promotion")
        return {"job": job.to_dict()}

    @app.post("/api/documents/upload")
    async def upload_document(
        file: list[UploadFile] = File(...),
        kb_dir: str | None = None,
        strategy_override: str | None = None,
        force_gate_pass: bool = False,
        force_gate_reject: bool = False,
        gate_reason: str = "",
        gate_operator: str = "",
        import_only: bool = False,
        force: bool = False,
    ) -> dict[str, Any]:
        try:
            target_kb = _resolve_kb_dir(kb_dir)
            from openkb.cli import SUPPORTED_EXTENSIONS

            uploads = list(file)
            if not uploads:
                raise ValueError("No files uploaded.")
            staging_dir = target_kb / ".openkb" / "uploads"
            staging_dir.mkdir(parents=True, exist_ok=True)
            destinations: list[Path] = []
            for upload in uploads:
                suffix = Path(upload.filename or "").suffix.lower()
                if suffix not in SUPPORTED_EXTENSIONS:
                    raise ValueError(f"Unsupported file type: {suffix}")
                safe_name = Path(upload.filename or "upload").name
                destination = staging_dir / f"upload-{next(tempfile._get_candidate_names())}" / safe_name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(await upload.read())
                destinations.append(destination)
        except Exception as exc:
            raise translate_error(exc) from exc
        if import_only:
            job = _submit_import_job(
                target_kb,
                destinations[0],
                preset_files=destinations,
                message_source=f"{len(destinations)} uploaded file(s)",
                strategy_override=str(strategy_override or "").strip() or None,
                force=force,
            )
        else:
            job = _submit_add_job(
                target_kb,
                destinations[0],
                preset_files=destinations,
                message_source=f"{len(destinations)} uploaded file(s)",
                strategy_override=str(strategy_override or "").strip() or None,
                force=force,
                force_gate_pass=force_gate_pass,
                force_gate_reject=force_gate_reject,
                gate_reason=str(gate_reason or "").strip(),
                gate_operator=str(gate_operator or "").strip(),
            )
        return {"job": job.to_dict()}

    @app.delete("/api/documents/{selector}")
    def delete_document(selector: str, kb_dir: str | None = Query(default=None)) -> dict[str, Any]:
        try:
            target_kb = _resolve_kb_dir(kb_dir)
        except Exception as exc:
            raise translate_error(exc) from exc

        def run(job):
            job.raise_if_stopped()
            job.add_log(f"Resolving source document: {selector}")
            result = kb_helpers.delete_source_document_data(target_kb, selector)
            job.raise_if_stopped()
            removed = len(result["removed_pages"])
            updated = len(result["updated_pages"])
            job.add_log(f"Removed {removed} page(s), updated {updated} shared page(s)")
            return result

        job = registry.submit("delete_source", run, message=f"Deleting source: {selector}")
        return {"job": job.to_dict()}

    @app.get("/api/wiki/tree")
    def wiki_tree(kb_dir: str | None = Query(default=None)) -> dict[str, Any]:
        try:
            return {"files": kb_helpers.build_wiki_tree(_resolve_kb_dir(kb_dir))}
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.get("/api/wiki/file")
    def wiki_file(path: str, kb_dir: str | None = Query(default=None)) -> dict[str, Any]:
        try:
            return kb_helpers.read_wiki_file(_resolve_kb_dir(kb_dir), path)
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.get("/api/review-summary/file")
    def review_summary_file(path: str, kb_dir: str | None = Query(default=None)) -> dict[str, Any]:
        try:
            return kb_helpers.read_review_summary_file(_resolve_kb_dir(kb_dir), path)
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.put("/api/wiki/file")
    def save_wiki_file(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return kb_helpers.write_wiki_file(
                _resolve_kb_dir(payload.get("kb_dir")),
                str(payload["path"]),
                str(payload.get("content") or ""),
            )
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.post("/api/query")
    def query(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            target_kb = _resolve_kb_dir(payload.get("kb_dir"))
            question = str(payload["question"]).strip()
            if not question:
                raise ValueError("Question is required.")
        except Exception as exc:
            raise translate_error(exc) from exc

        def run(_job):
            from openkb.agent.chat_session import ChatSession, load_session
            from openkb.agent.query import format_query_exploration, run_query_session
            from openkb.cli import _setup_llm_key

            _job.raise_if_stopped()
            _job.add_log("Loading model configuration")
            config = load_config(target_kb / ".openkb" / "config.yaml")
            _setup_llm_key(target_kb)
            model = str(config.get("model", DEFAULT_CONFIG["model"]))
            language = str(config.get("language", DEFAULT_CONFIG.get("language", "en")))
            session_id = str(payload.get("session_id") or "").strip()
            session = (
                load_session(target_kb, session_id)
                if session_id
                else ChatSession.new(target_kb, model, language)
            )
            _job.add_log("Running query")
            from openkb.model_pool import is_model_pool_enabled, record_route_failure, record_route_success, select_model_route

            if is_model_pool_enabled(target_kb):
                excluded_routes: set[str] = set()
                last_error: Exception | None = None
                query_result = None
                for _attempt in range(3):
                    route = select_model_route(target_kb, exclude=excluded_routes)
                    try:
                        _setup_llm_key(target_kb, _route_profile_payload(route))
                        query_result = asyncio.run(
                            run_query_session(
                                question,
                                target_kb,
                                model,
                                session,
                                route=route,
                            )
                        )
                        record_route_success(target_kb, route.profile_id, route.model)
                        break
                    except Exception as exc:
                        last_error = exc
                        excluded_routes.add(route.route_id)
                        record_route_failure(target_kb, route.profile_id, route.model, exc)
                        try:
                            from openkb.model_pool import probe_model_route

                            probe_model_route(target_kb, route)
                        except Exception as probe_exc:
                            record_route_failure(target_kb, route.profile_id, route.model, probe_exc)
                if query_result is None:
                    raise last_error or RuntimeError("Model pool query failed.")
            else:
                query_result = asyncio.run(
                    run_query_session(
                        question,
                        target_kb,
                        model,
                        session,
                    )
                )
            answer = query_result["answer"]
            _job.raise_if_stopped()
            if payload.get("save") and answer:
                import re

                slug = re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")[:60] or "query"
                explore_dir = target_kb / "wiki" / "explorations"
                explore_dir.mkdir(parents=True, exist_ok=True)
                explore_path = explore_dir / f"{slug}.md"
                explore_path.write_text(
                    format_query_exploration(question, answer, query_result["references"]),
                    encoding="utf-8",
                )
                _job.add_log(f"Saved exploration: {explore_path.name}")
            commit_kb_changes(target_kb, f"Query {question}")
            return {
                "answer": answer,
                "session_id": session.id,
                "session": session.to_dict(),
                "references": query_result["references"],
            }

        job = registry.submit("query", run, message=question)
        return {"job": job.to_dict()}

    @app.post("/api/query/stream")
    def query_stream(payload: dict[str, Any]):
        try:
            target_kb = _resolve_kb_dir(payload.get("kb_dir"))
            question = str(payload["question"]).strip()
            if not question:
                raise ValueError("Question is required.")
        except Exception as exc:
            raise translate_error(exc) from exc

        async def events() -> AsyncIterator[str]:
            try:
                from openkb.agent.chat_session import ChatSession, load_session
                from openkb.agent.query import format_query_exploration, run_query_session_stream
                from openkb.cli import _ordered_llm_profiles, _runtime_context_for_profile, _setup_llm_key
                from openkb.llm_runtime import llm_runtime_context

                config = load_config(target_kb / ".openkb" / "config.yaml")
                profiles = _ordered_llm_profiles(config)
                active_profile = profiles[0] if profiles else None
                _setup_llm_key(target_kb, active_profile)
                runtime_config = _runtime_context_for_profile(target_kb, active_profile or config)
                model = (
                    str((active_profile or {}).get("model") or config.get("model") or DEFAULT_CONFIG["model"]).strip()
                )
                language = str(config.get("language", DEFAULT_CONFIG.get("language", "en")))
                session_id = str(payload.get("session_id") or "").strip()
                session = (
                    load_session(target_kb, session_id)
                    if session_id
                    else ChatSession.new(target_kb, model, language)
                )
                yield _sse_event("session", {"session_id": session.id})

                queue: asyncio.Queue[str | None] = asyncio.Queue()

                async def emit_delta(text: str) -> None:
                    await queue.put(_sse_event("delta", {"text": text}))

                async def emit_status(message: str) -> None:
                    await queue.put(_sse_event("status", {"message": message}))

                async def run_query_task() -> None:
                    try:
                        from openkb.model_pool import (
                            is_model_pool_enabled,
                            probe_model_route,
                            record_route_failure,
                            record_route_success,
                            select_model_route,
                        )

                        if is_model_pool_enabled(target_kb):
                            excluded_routes: set[str] = set()
                            last_error: Exception | None = None
                            result = None
                            for _attempt in range(3):
                                route = select_model_route(target_kb, exclude=excluded_routes)
                                try:
                                    _setup_llm_key(target_kb, _route_profile_payload(route))
                                    route_runtime_config = _runtime_context_for_profile(target_kb, _route_profile_payload(route))
                                    with llm_runtime_context(route_runtime_config):
                                        result = await run_query_session_stream(
                                            question,
                                            target_kb,
                                            model,
                                            session,
                                            emit_delta,
                                            on_status=emit_status,
                                            route=route,
                                        )
                                    record_route_success(target_kb, route.profile_id, route.model)
                                    break
                                except Exception as exc:
                                    last_error = exc
                                    excluded_routes.add(route.route_id)
                                    record_route_failure(target_kb, route.profile_id, route.model, exc)
                                    try:
                                        probe_model_route(target_kb, route)
                                    except Exception as probe_exc:
                                        record_route_failure(target_kb, route.profile_id, route.model, probe_exc)
                            if result is None:
                                raise last_error or RuntimeError("Model pool query failed.")
                        else:
                            with llm_runtime_context(runtime_config):
                                result = await run_query_session_stream(
                                    question,
                                    target_kb,
                                    model,
                                    session,
                                    emit_delta,
                                    on_status=emit_status,
                                )
                        answer = result["answer"]
                        if payload.get("save") and answer:
                            import re

                            slug = re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")[:60] or "query"
                            explore_dir = target_kb / "wiki" / "explorations"
                            explore_dir.mkdir(parents=True, exist_ok=True)
                            explore_path = explore_dir / f"{slug}.md"
                            explore_path.write_text(
                                format_query_exploration(question, answer, result["references"]),
                                encoding="utf-8",
                            )
                        commit_kb_changes(target_kb, f"Query {question}")
                        await queue.put(
                            _sse_event(
                                "done",
                                {
                                    "answer": answer,
                                    "session_id": session.id,
                                    "session": session.to_dict(),
                                    "references": result["references"],
                                },
                            )
                        )
                    except Exception as exc:
                        await queue.put(_sse_event("error", {"message": str(exc)}))
                    finally:
                        await queue.put(None)

                task = asyncio.create_task(run_query_task())
                try:
                    while True:
                        item = await queue.get()
                        if item is None:
                            break
                        yield item
                finally:
                    if not task.done():
                        task.cancel()
            except Exception as exc:
                yield _sse_event("error", {"message": str(exc)})

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/api/chats")
    def chats(kb_dir: str | None = Query(default=None)) -> dict[str, Any]:
        try:
            from openkb.agent.chat_session import list_sessions

            return {"sessions": list_sessions(_resolve_kb_dir(kb_dir))}
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.post("/api/chats")
    def create_chat(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            from openkb.agent.chat_session import ChatSession
            from openkb.cli import _ordered_llm_profiles

            target_kb = _resolve_kb_dir(payload.get("kb_dir"))
            config = load_config(target_kb / ".openkb" / "config.yaml")
            profiles = _ordered_llm_profiles(config)
            active_profile = profiles[0] if profiles else None
            model = str((active_profile or {}).get("model") or config.get("model") or DEFAULT_CONFIG["model"]).strip()
            language = str(config.get("language", DEFAULT_CONFIG.get("language", "en")))
            session = ChatSession.new(target_kb, model, language)
            session.save()
            commit_kb_changes(target_kb, f"Create chat {session.id}")
            return session.to_dict()
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.get("/api/chats/{session_id}")
    def chat(session_id: str, kb_dir: str | None = Query(default=None)) -> dict[str, Any]:
        try:
            from openkb.agent.chat_session import load_session

            return load_session(_resolve_kb_dir(kb_dir), session_id).to_dict()
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.delete("/api/chats/{session_id}")
    def delete_chat(session_id: str, kb_dir: str | None = Query(default=None)) -> dict[str, Any]:
        try:
            from openkb.agent.chat_session import delete_session

            target_kb = _resolve_kb_dir(kb_dir)
            deleted = delete_session(target_kb, session_id)
            if deleted:
                commit_kb_changes(target_kb, f"Delete chat {session_id}")
            return {"deleted": deleted}
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.post("/api/lint")
    def lint(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            target_kb = _resolve_kb_dir(payload.get("kb_dir"))
        except Exception as exc:
            raise translate_error(exc) from exc

        def run(_job):
            from openkb.cli import run_lint

            _job.raise_if_stopped()
            _job.add_log("Running structural and knowledge lint")
            report = asyncio.run(run_lint(target_kb))
            _job.raise_if_stopped()
            if report:
                _job.add_log(f"Report written: {report}")
            return {"report": str(report) if report else None}

        job = registry.submit("lint", run, message="Running lint")
        return {"job": job.to_dict()}

    @app.post("/api/lint/fix-plan")
    def lint_fix_plan(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            target_kb = _resolve_kb_dir(payload.get("kb_dir"))
            report = str(payload["report"]) if payload.get("report") else None
        except Exception as exc:
            raise translate_error(exc) from exc

        def run(job):
            job.raise_if_stopped()
            job.add_log("Extracting safe fix candidates")
            plan = kb_helpers.build_lint_fix_plan(target_kb, report)
            job.raise_if_stopped()
            job.add_log(f"Found {len(plan['candidates'])} candidate(s)")
            return plan

        job = registry.submit("lint_fix_plan", run, message="Generating lint fix plan")
        return {"job": job.to_dict()}

    @app.post("/api/lint/apply-fixes")
    def lint_apply_fixes(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            target_kb = _resolve_kb_dir(payload.get("kb_dir"))
            candidates = payload.get("candidates")
        except Exception as exc:
            raise translate_error(exc) from exc

        def run(job):
            job.raise_if_stopped()
            job.add_log("Applying approved lint fixes")
            result = kb_helpers.apply_lint_fix_candidates(target_kb, candidates)
            job.raise_if_stopped()
            job.add_log(f"Created {len(result['created'])} draft page(s)")
            return result

        job = registry.submit("lint_fix_apply", run, message="Applying approved lint fixes")
        return {"job": job.to_dict()}

    @app.post("/api/concept-merges/propose")
    def propose_concept_merges(payload: dict[str, Any]) -> dict[str, Any]:
        """Dry-run: scan the KB for duplicate concept clusters."""
        try:
            target_kb = _resolve_kb_dir(payload.get("kb_dir"))
        except Exception as exc:
            raise translate_error(exc) from exc

        def run(job):
            from openkb.concept_merge import propose_merges

            job.raise_if_stopped()
            job.add_log("Scanning concepts/ for duplicate clusters")
            proposals = propose_merges(target_kb)
            job.raise_if_stopped()
            payload_out = [
                {
                    "canonical": p.canonical,
                    "merged": list(p.merged),
                    "rationale": dict(p.rationale),
                    "sources_union": list(p.sources_union),
                }
                for p in proposals
            ]
            total_dupes = sum(len(p["merged"]) - 1 for p in payload_out)
            job.add_log(f"Found {len(payload_out)} cluster(s), {total_dupes} duplicate page(s)")
            return {
                "proposals": payload_out,
                "total_clusters": len(payload_out),
                "total_duplicates": total_dupes,
            }

        job = registry.submit("concept_merges_propose", run, message="Scanning for duplicate concepts")
        return {"job": job.to_dict()}

    @app.post("/api/concept-merges/apply")
    def apply_concept_merges(payload: dict[str, Any]) -> dict[str, Any]:
        """Execute concept merges. Accepts either the previously-returned
        proposal list (filtered by the user) or recomputes them when omitted.
        """
        try:
            target_kb = _resolve_kb_dir(payload.get("kb_dir"))
        except Exception as exc:
            raise translate_error(exc) from exc
        client_proposals = payload.get("proposals")

        def run(job):
            from openkb.concept_merge import MergeProposal, propose_merges, apply_merges

            job.raise_if_stopped()
            if isinstance(client_proposals, list) and client_proposals:
                proposals = [
                    MergeProposal(
                        canonical=str(item.get("canonical") or ""),
                        merged=list(item.get("merged") or []),
                        rationale={k: float(v) for k, v in (item.get("rationale") or {}).items()},
                        sources_union=list(item.get("sources_union") or []),
                    )
                    for item in client_proposals
                    if isinstance(item, dict) and item.get("canonical") and len(item.get("merged") or []) >= 2
                ]
                job.add_log(f"Applying {len(proposals)} user-selected cluster(s)")
            else:
                job.add_log("No proposals supplied; recomputing")
                proposals = propose_merges(target_kb)
            job.raise_if_stopped()
            result = apply_merges(target_kb, proposals)
            job.add_log(
                f"Merged {result['clusters_merged']} cluster(s); "
                f"deleted {result['files_deleted']} file(s); "
                f"rewrote refs in {result['files_rewritten']} file(s)"
            )
            return result

        job = registry.submit("concept_merges_apply", run, message="Applying concept merges")
        return {"job": job.to_dict()}

    @app.post("/api/lint/h1-fix")
    def lint_h1_fix(payload: dict[str, Any]) -> dict[str, Any]:
        """Apply safe in-place H1 repairs across concepts/companies/industries."""
        try:
            target_kb = _resolve_kb_dir(payload.get("kb_dir"))
        except Exception as exc:
            raise translate_error(exc) from exc

        def run(job):
            from openkb.lint import apply_safe_h1_fix, iter_h1_violations

            job.raise_if_stopped()
            wiki = target_kb / "wiki"
            fixed: list[str] = []
            scanned = 0
            for namespace in ("concepts", "companies", "industries"):
                for path, kinds in iter_h1_violations(wiki, namespace):
                    scanned += 1
                    if apply_safe_h1_fix(path):
                        fixed.append(f"{namespace}/{path.name}")
            job.add_log(f"Scanned {scanned} flagged page(s); fixed {len(fixed)}")
            return {"fixed_files": fixed, "fixed_count": len(fixed), "scanned": scanned}

        job = registry.submit("lint_h1_fix", run, message="Applying safe H1 fixes")
        return {"job": job.to_dict()}

    @app.post("/api/compact")
    def compact_kb(payload: dict[str, Any]) -> dict[str, Any]:
        """One-shot KB hygiene: H1 audit + dedupe scan + structural lint.

        Writes a report file under ``wiki/reports/`` and returns its path.
        Always dry-run from the API; users trigger merges/fixes through the
        dedicated endpoints after reviewing.
        """
        try:
            target_kb = _resolve_kb_dir(payload.get("kb_dir"))
        except Exception as exc:
            raise translate_error(exc) from exc

        def run(job):
            from datetime import datetime
            from openkb.lint import find_h1_issues, run_structural_lint
            from openkb.concept_merge import propose_merges

            job.raise_if_stopped()
            wiki = target_kb / "wiki"
            today = datetime.now().strftime("%Y%m%d")

            job.add_log("H1 audit")
            h1_issues = find_h1_issues(wiki)

            job.raise_if_stopped()
            job.add_log("Duplicate concept scan")
            proposals = propose_merges(target_kb)
            total_dupes = sum(len(p.merged) - 1 for p in proposals)

            job.raise_if_stopped()
            job.add_log("Structural lint")
            structural = run_structural_lint(target_kb)

            reports_dir = wiki / "reports"
            reports_dir.mkdir(parents=True, exist_ok=True)
            report_path = reports_dir / f"compact-{today}.md"
            lines = [
                f"# KB Compact Report — {today}",
                "",
                f"- H1 issues: **{len(h1_issues)}**",
                f"- Duplicate clusters: **{len(proposals)}** covering **{total_dupes}** page(s)",
                "",
                "## H1 issues",
            ]
            if h1_issues:
                lines.extend(f"- {it}" for it in h1_issues[:200])
                if len(h1_issues) > 200:
                    lines.append(f"- ... and {len(h1_issues) - 200} more")
            else:
                lines.append("- (none)")
            lines.append("")
            lines.append("## Duplicate concept clusters")
            if proposals:
                for proposal in proposals:
                    lines.append(f"- canonical: `{proposal.canonical}`")
                    for slug in proposal.merged[1:]:
                        sim = proposal.rationale.get(slug, 0.0)
                        lines.append(f"  - merge: `{slug}` (sim={sim:.3f})")
            else:
                lines.append("- (none)")
            lines.append("")
            lines.append("## Structural lint")
            lines.append("")
            lines.append(structural)
            report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            job.add_log(f"Report written: {report_path.relative_to(target_kb)}")
            return {
                "report_path": str(report_path.relative_to(target_kb)).replace("\\", "/"),
                "h1_issue_count": len(h1_issues),
                "cluster_count": len(proposals),
                "duplicate_count": total_dupes,
            }

        job = registry.submit("compact", run, message="Running KB compact audit")
        return {"job": job.to_dict()}

    @app.get("/api/jobs")
    def jobs() -> dict[str, Any]:
        return {"jobs": [job.to_dict() for job in registry.list_jobs()]}

    @app.get("/api/model-pool")
    def model_pool(kb_dir: str | None = Query(default=None)) -> dict[str, Any]:
        try:
            return kb_helpers.get_model_pool_data(_resolve_kb_dir(kb_dir))
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.post("/api/model-pool/probe")
    def model_pool_probe(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            target_kb = _resolve_kb_dir(payload.get("kb_dir"))
            pool = kb_helpers.get_model_pool_data(target_kb)
            results = [_probe_model_pool_profile(target_kb, profile["id"]) for profile in pool["profiles"]]
            return {
                "profiles": results,
                "model_pool": kb_helpers.get_model_pool_data(target_kb),
            }
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.post("/api/model-pool/profiles")
    def model_pool_profile_create(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return kb_helpers.save_model_pool_profile(_resolve_kb_dir(payload.get("kb_dir")), payload)
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.put("/api/model-pool/profiles/{profile_id}")
    def model_pool_profile_update(profile_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return kb_helpers.save_model_pool_profile(_resolve_kb_dir(payload.get("kb_dir")), payload, profile_id)
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.delete("/api/model-pool/profiles/{profile_id}")
    def model_pool_profile_delete(profile_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return kb_helpers.delete_model_pool_profile(_resolve_kb_dir(payload.get("kb_dir")), profile_id)
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.post("/api/model-pool/profiles/{profile_id}/probe")
    def model_pool_profile_probe(profile_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            target_kb = _resolve_kb_dir(payload.get("kb_dir"))
            kb_helpers.get_model_pool_profile(target_kb, profile_id)
            profile = _probe_model_pool_profile(target_kb, profile_id)
            return {
                "profile": profile,
                "model_pool": kb_helpers.get_model_pool_data(target_kb),
            }
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.post("/api/model-pool/profiles/{profile_id}/enable")
    def model_pool_profile_enable(profile_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            target_kb = _resolve_kb_dir(payload.get("kb_dir"))
            updates = dict(payload)
            updates["profile_id"] = profile_id
            updates["enabled"] = True
            config = kb_helpers.update_config_data(target_kb, updates)
            return {"config": config, "model_pool": kb_helpers.get_model_pool_data(target_kb)}
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.post("/api/model-pool/profiles/{profile_id}/disable")
    def model_pool_profile_disable(profile_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            target_kb = _resolve_kb_dir(payload.get("kb_dir"))
            updates = dict(payload)
            updates["profile_id"] = profile_id
            updates["enabled"] = False
            config = kb_helpers.update_config_data(target_kb, updates)
            profile = kb_helpers.save_model_pool_profile_status(
                target_kb,
                profile_id,
                {"health": "disabled", "available_models": [], "failed_models": {}, "last_error": ""},
            )
            return {"config": config, "profile": profile, "model_pool": kb_helpers.get_model_pool_data(target_kb)}
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.get("/api/llm-usage")
    def llm_usage(
        kb_dir: str | None = Query(default=None),
        q: str = Query(default=""),
        page: int = Query(default=1),
        page_size: int = Query(default=50),
    ) -> dict[str, Any]:
        try:
            return kb_helpers.get_llm_usage_data(
                _resolve_kb_dir(kb_dir),
                q=q,
                page=page,
                page_size=page_size,
            )
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.get("/api/llm-usage/export")
    def llm_usage_export(
        kb_dir: str | None = Query(default=None),
        q: str = Query(default=""),
    ) -> dict[str, Any]:
        try:
            return kb_helpers.export_llm_usage_data(_resolve_kb_dir(kb_dir), q=q)
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.get("/api/ocr/cache")
    def ocr_cache(kb_dir: str | None = Query(default=None)) -> dict[str, Any]:
        try:
            return kb_helpers.list_ocr_cache_entries(_resolve_kb_dir(kb_dir))
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.post("/api/ocr/cache/{file_hash}/invalidate")
    def ocr_cache_invalidate(file_hash: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return kb_helpers.invalidate_ocr_cache_entry(_resolve_kb_dir(payload.get("kb_dir")), file_hash)
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.post("/api/ocr/cache/{file_hash}/rerun")
    def ocr_cache_rerun(file_hash: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            target_kb = _resolve_kb_dir(payload.get("kb_dir"))
            source = kb_helpers.source_path_for_ocr_cache_entry(target_kb, file_hash)
        except Exception as exc:
            raise translate_error(exc) from exc

        job = _submit_add_job(
            target_kb,
            source,
            message_source=f"OCR rerun: {source.name}",
            strategy_override=str(payload.get("strategy_override") or "").strip() or None,
            force=True,
        )
        return {"job": job.to_dict()}

    @app.post("/api/ocr/cache/{file_hash}/retry")
    def ocr_cache_retry(file_hash: str, payload: dict[str, Any]) -> dict[str, Any]:
        return ocr_cache_rerun(file_hash, payload)

    @app.get("/api/pageindex-local/status")
    def pageindex_local_status(kb_dir: str | None = Query(default=None)) -> dict[str, Any]:
        try:
            return kb_helpers.get_pageindex_local_status(_resolve_kb_dir(kb_dir))
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.get("/api/ingest-gate")
    def ingest_gate(kb_dir: str | None = Query(default=None), limit: int = Query(default=250, ge=1, le=1000)) -> dict[str, Any]:
        try:
            return kb_helpers.get_ingest_gate_data(_resolve_kb_dir(kb_dir), limit=limit)
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str) -> dict[str, Any]:
        found = registry.get(job_id)
        if not found:
            raise HTTPException(status_code=404, detail="Job not found")
        return found.to_dict()

    @app.post("/api/jobs/{job_id}/stop")
    def stop_job(job_id: str) -> dict[str, Any]:
        found = registry.stop(job_id)
        if not found:
            raise HTTPException(status_code=404, detail="Job not found")
        return found.to_dict()

    @app.post("/api/jobs/{job_id}/retry")
    def retry_job(job_id: str) -> dict[str, Any]:
        try:
            retried = registry.retry(job_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not retried:
            raise HTTPException(status_code=404, detail="Job not found")
        return {"job": retried.to_dict()}

    @app.get("/api/config")
    def config(kb_dir: str | None = Query(default=None)) -> dict[str, Any]:
        try:
            return kb_helpers.get_config_data(_resolve_kb_dir(kb_dir))
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.put("/api/config")
    def update_config(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return kb_helpers.update_config_data(_resolve_kb_dir(payload.get("kb_dir")), payload)
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.get("/api/config/export")
    def export_config(kb_dir: str | None = Query(default=None)) -> dict[str, Any]:
        try:
            return kb_helpers.export_config_data(_resolve_kb_dir(kb_dir))
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.post("/api/config/import")
    def import_config(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            imported = payload.get("config") if isinstance(payload.get("config"), dict) else {
                key: value for key, value in payload.items() if key != "kb_dir"
            }
            return kb_helpers.import_config_data(_resolve_kb_dir(payload.get("kb_dir")), imported)
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.post("/api/config/test-llm")
    def test_llm(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return _test_llm_config(payload)
        except Exception as exc:
            raise translate_error(exc) from exc

    if static_dir.exists():
        app.mount("/assets", StaticFiles(directory=static_dir), name="assets")

    @app.get("/")
    def index() -> Any:
        return FileResponse(static_dir / "index.html")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Any:
        return Response(status_code=204)

    return app


def serve_client(*, host: str = "0.0.0.0", port: int = 8765, open_browser: bool = True) -> None:
    """Start the local client server."""
    _FastAPI, _File, _FileResponse, _HTTPException, _Query, _Response, _StaticFiles, _UploadFile, uvicorn = _import_web_dependencies()
    url = f"http://{host}:{port}"
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(create_app(), host=host, port=port)
