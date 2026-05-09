"""Local HTTP server for the OpenKB browser client."""
from __future__ import annotations

import asyncio
import inspect
import json
import os
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
    registry = registry or default_registry
    app = FastAPI(title="OpenKB Client", version="0.1.0")
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
    def documents(kb_dir: str | None = Query(default=None)) -> dict[str, Any]:
        try:
            return kb_helpers.get_document_data(_resolve_kb_dir(kb_dir))
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
    ):
        def run(job):
            from openkb.cli import SUPPORTED_EXTENSIONS, add_single_file

            def progress(message: str) -> None:
                job.raise_if_stopped()
                job.add_log(message)

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
            added = 0
            failures: list[dict[str, str]] = []
            for index, file_path in enumerate(files, 1):
                job.raise_if_stopped()
                if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    raise ValueError(f"Unsupported file type: {file_path.suffix}")
                job.set_progress(index - 1, len(files))
                job.set_message(f"Adding {index}/{len(files)}: {file_path.name}")
                try:
                    add_kwargs: dict[str, Any] = {
                        "strict": True,
                        "progress_callback": progress,
                    }
                    if strategy_override:
                        add_kwargs["strategy_override"] = strategy_override
                    if force:
                        add_kwargs["force"] = True
                    if _callable_accepts_keyword(add_single_file, "job"):
                        add_kwargs["job"] = job
                    add_single_file(
                        file_path,
                        target_kb,
                        **add_kwargs,
                    )
                    job.raise_if_stopped()
                except Exception as exc:
                    if isinstance(exc, JobStopped):
                        raise
                    job.add_log(f"Failed {file_path.name}: {exc}", level="error")
                    if len(files) == 1:
                        raise
                    failures.append({
                        "name": file_path.name,
                        "path": str(file_path),
                        "error": str(exc),
                    })
                    job.set_progress(index, len(files))
                    continue
                added += 1
                job.set_progress(index, len(files))
                job.add_log(f"Finished {index}/{len(files)}: {file_path.name}")
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
            return {
                "added": added,
                "failed": len(failures),
                "total": len(files),
                "failures": failures,
            }

        job = registry.submit("add", run, message=f"Queued add: {message_source or source}")
        return job

    @app.post("/api/documents/add")
    def add_document(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            target_kb = _resolve_kb_dir(payload.get("kb_dir"))
            source = Path(str(payload["path"]))
        except Exception as exc:
            raise translate_error(exc) from exc

        job = _submit_add_job(
            target_kb,
            source,
            strategy_override=str(payload.get("strategy_override") or "").strip() or None,
        )
        return {"job": job.to_dict()}

    @app.post("/api/documents/upload")
    async def upload_document(
        file: list[UploadFile] = File(...),
        kb_dir: str | None = None,
        strategy_override: str | None = None,
    ) -> dict[str, Any]:
        try:
            target_kb = _resolve_kb_dir(kb_dir)
            from openkb.cli import SUPPORTED_EXTENSIONS

            uploads = list(file)
            if not uploads:
                raise ValueError("No files uploaded.")
            raw_dir = target_kb / "raw"
            raw_dir.mkdir(exist_ok=True)
            destinations: list[Path] = []
            for upload in uploads:
                suffix = Path(upload.filename or "").suffix.lower()
                if suffix not in SUPPORTED_EXTENSIONS:
                    raise ValueError(f"Unsupported file type: {suffix}")
                destination = raw_dir / Path(upload.filename or "upload").name
                destination.write_bytes(await upload.read())
                destinations.append(destination)
        except Exception as exc:
            raise translate_error(exc) from exc
        job = _submit_add_job(
            target_kb,
            destinations[0],
            preset_files=destinations,
            message_source=f"{len(destinations)} uploaded file(s)",
            strategy_override=str(strategy_override or "").strip() or None,
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
                from openkb.cli import _setup_llm_key

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
                yield _sse_event("session", {"session_id": session.id})

                queue: asyncio.Queue[str | None] = asyncio.Queue()

                async def emit_delta(text: str) -> None:
                    await queue.put(_sse_event("delta", {"text": text}))

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
                                    result = await run_query_session_stream(
                                        question,
                                        target_kb,
                                        model,
                                        session,
                                        emit_delta,
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
                            result = await run_query_session_stream(
                                question,
                                target_kb,
                                model,
                                session,
                                emit_delta,
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


def serve_client(*, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    """Start the local client server."""
    _FastAPI, _File, _FileResponse, _HTTPException, _Query, _Response, _StaticFiles, _UploadFile, uvicorn = _import_web_dependencies()
    url = f"http://{host}:{port}"
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(create_app(), host=host, port=port)
