"""Local HTTP server for the OpenKB browser client."""
from __future__ import annotations

import asyncio
import os
import webbrowser
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from openkb.client.jobs import JobRegistry, default_registry
from openkb.client import kb as kb_helpers
from openkb.config import DEFAULT_CONFIG, load_config, load_global_config


class ClientDependencyError(RuntimeError):
    """Raised when optional Web client dependencies are unavailable."""


def _import_web_dependencies():
    try:
        import uvicorn
        from fastapi import FastAPI, File, HTTPException, Query, UploadFile
        from fastapi.responses import FileResponse, Response
        from fastapi.staticfiles import StaticFiles
    except ModuleNotFoundError as exc:
        raise ClientDependencyError(
            "OpenKB client dependencies are not installed."
        ) from exc
    # `create_app()` defines routes under postponed evaluation of annotations,
    # so FastAPI resolves names like `UploadFile` from module globals.
    globals()["UploadFile"] = UploadFile
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


def _static_dir() -> Path:
    return Path(__file__).resolve().parent / "static"


_RUNTIME_ENV_KEYS = (
    "LLM_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
    "OPENKB_WIRE_API",
    "OPENAI_WIRE_API",
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

    def _submit_add_job(
        target_kb: Path,
        source: Path,
        *,
        preset_files: list[Path] | None = None,
        message_source: str | None = None,
    ):
        def run(job):
            from openkb.cli import SUPPORTED_EXTENSIONS, add_single_file

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
                if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    raise ValueError(f"Unsupported file type: {file_path.suffix}")
                job.set_progress(index - 1, len(files))
                job.set_message(f"Adding {index}/{len(files)}: {file_path.name}")
                try:
                    add_single_file(
                        file_path,
                        target_kb,
                        strict=True,
                        progress_callback=job.add_log,
                    )
                except Exception as exc:
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

        job = _submit_add_job(target_kb, source)
        return {"job": job.to_dict()}

    @app.post("/api/documents/upload")
    async def upload_document(file: list[UploadFile] = File(...), kb_dir: str | None = None) -> dict[str, Any]:
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
        )
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
            from openkb.agent.query import run_query
            from openkb.cli import _setup_llm_key

            _job.add_log("Loading model configuration")
            config = load_config(target_kb / ".openkb" / "config.yaml")
            _setup_llm_key(target_kb)
            _job.add_log("Running query")
            answer = asyncio.run(
                run_query(
                    question,
                    target_kb,
                    str(config.get("model", DEFAULT_CONFIG["model"])),
                    stream=False,
                )
            )
            if payload.get("save") and answer:
                import re

                slug = re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")[:60] or "query"
                explore_dir = target_kb / "wiki" / "explorations"
                explore_dir.mkdir(parents=True, exist_ok=True)
                explore_path = explore_dir / f"{slug}.md"
                explore_path.write_text(
                    f"---\nquery: \"{question}\"\n---\n\n{answer}\n",
                    encoding="utf-8",
                )
                _job.add_log(f"Saved exploration: {explore_path.name}")
            return {"answer": answer}

        job = registry.submit("query", run, message=question)
        return {"job": job.to_dict()}

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

            return {"deleted": delete_session(_resolve_kb_dir(kb_dir), session_id)}
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

            _job.add_log("Running structural and knowledge lint")
            report = asyncio.run(run_lint(target_kb))
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
            job.add_log("Extracting safe fix candidates")
            plan = kb_helpers.build_lint_fix_plan(target_kb, report)
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
            job.add_log("Applying approved lint fixes")
            result = kb_helpers.apply_lint_fix_candidates(target_kb, candidates)
            job.add_log(f"Created {len(result['created'])} draft page(s)")
            return result

        job = registry.submit("lint_fix_apply", run, message="Applying approved lint fixes")
        return {"job": job.to_dict()}

    @app.get("/api/jobs")
    def jobs() -> dict[str, Any]:
        return {"jobs": [job.to_dict() for job in registry.list_jobs()]}

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str) -> dict[str, Any]:
        found = registry.get(job_id)
        if not found:
            raise HTTPException(status_code=404, detail="Job not found")
        return found.to_dict()

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
