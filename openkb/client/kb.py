"""Structured helpers used by the local OpenKB client.

The CLI primarily prints human-readable output. These helpers return JSON-ready
data so a browser client can reuse the same storage conventions without parsing
terminal text.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from openkb.config import (
    DEFAULT_CONFIG,
    load_config,
    load_global_config,
    register_kb,
    save_config,
)
from openkb.llm_runtime import model_prefers_responses_api
from openkb.schema import AGENTS_MD


class ClientError(RuntimeError):
    """Base error raised by client helpers."""


class PathSecurityError(ClientError, ValueError):
    """Raised when a requested path escapes the allowed root."""


_TYPE_DISPLAY_MAP = {
    "long_pdf": "pageindex",
}

_SHORT_DOC_TYPES = {"pdf", "docx", "md", "markdown", "html", "htm", "txt", "csv", "pptx", "xlsx"}

_CONFIG_KEYS = {"model", "language", "pageindex_threshold", "wire_api", "base_url"}


def is_kb_dir(path: Path) -> bool:
    """Return True when *path* looks like an initialized OpenKB directory."""
    return (Path(path) / ".openkb").is_dir()


def require_kb_dir(kb_dir: Path) -> Path:
    """Resolve and validate a KB directory."""
    resolved = Path(kb_dir).resolve()
    if not is_kb_dir(resolved):
        raise ClientError(f"Not an OpenKB knowledge base: {resolved}")
    return resolved


def _display_type(raw_type: str) -> str:
    if raw_type in _TYPE_DISPLAY_MAP:
        return _TYPE_DISPLAY_MAP[raw_type]
    if raw_type in _SHORT_DOC_TYPES:
        return "short"
    return raw_type


def _read_hashes(kb_dir: Path) -> dict[str, dict[str, Any]]:
    hashes_file = kb_dir / ".openkb" / "hashes.json"
    if not hashes_file.exists():
        return {}
    return json.loads(hashes_file.read_text(encoding="utf-8") or "{}")


def _list_stems(path: Path) -> list[str]:
    if not path.exists():
        return []
    return sorted(p.stem for p in path.glob("*.md") if p.is_file())


def _list_names(path: Path) -> list[str]:
    if not path.exists():
        return []
    return sorted(p.name for p in path.glob("*.md") if p.is_file())


def _format_mtime(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")


def get_status_data(kb_dir: Path) -> dict[str, Any]:
    """Return status counts for a knowledge base."""
    kb_dir = require_kb_dir(kb_dir)
    wiki_dir = kb_dir / "wiki"
    directories: dict[str, int] = {}
    for name in (
        "sources",
        "summaries",
        "companies",
        "industries",
        "themes",
        "metrics",
        "risks",
        "concepts",
        "reports",
    ):
        path = wiki_dir / name
        directories[name] = len(list(path.glob("*.md"))) if path.exists() else 0

    raw_dir = kb_dir / "raw"
    directories["raw"] = len([p for p in raw_dir.iterdir() if p.is_file()]) if raw_dir.exists() else 0

    hashes = _read_hashes(kb_dir)
    summaries = list((wiki_dir / "summaries").glob("*.md")) if (wiki_dir / "summaries").exists() else []
    reports = list((wiki_dir / "reports").glob("*.md")) if (wiki_dir / "reports").exists() else []

    newest_summary = max(summaries, key=lambda p: p.stat().st_mtime) if summaries else None
    newest_report = max(reports, key=lambda p: p.stat().st_mtime) if reports else None

    return {
        "kb_dir": str(kb_dir),
        "directories": directories,
        "total_indexed": len(hashes),
        "last_compile": _format_mtime(newest_summary) if newest_summary else None,
        "last_lint": _format_mtime(newest_report) if newest_report else None,
    }


def get_document_data(kb_dir: Path) -> dict[str, Any]:
    """Return indexed documents and wiki page lists."""
    kb_dir = require_kb_dir(kb_dir)
    hashes = _read_hashes(kb_dir)
    documents = []
    for file_hash, meta in hashes.items():
        documents.append(
            {
                "hash": file_hash,
                "name": meta.get("name", "unknown"),
                "type": _display_type(str(meta.get("type", "unknown"))),
                "pages": meta.get("pages", ""),
            }
        )

    wiki_dir = kb_dir / "wiki"
    return {
        "documents": documents,
        "summaries": _list_stems(wiki_dir / "summaries"),
        "companies": _list_stems(wiki_dir / "companies"),
        "industries": _list_stems(wiki_dir / "industries"),
        "themes": _list_stems(wiki_dir / "themes"),
        "metrics": _list_stems(wiki_dir / "metrics"),
        "risks": _list_stems(wiki_dir / "risks"),
        "concepts": _list_stems(wiki_dir / "concepts"),
        "reports": _list_names(wiki_dir / "reports"),
    }


def _resolve_wiki_path(kb_dir: Path, relative_path: str) -> Path:
    kb_dir = require_kb_dir(kb_dir)
    root = (kb_dir / "wiki").resolve()
    full_path = (root / relative_path).resolve()
    if not full_path.is_relative_to(root):
        raise PathSecurityError("Path escapes wiki root.")
    return full_path


def build_wiki_tree(kb_dir: Path) -> list[dict[str, Any]]:
    """Return a flat, sorted tree of readable wiki files."""
    kb_dir = require_kb_dir(kb_dir)
    root = kb_dir / "wiki"
    if not root.exists():
        return []
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".md", ".json"}:
            continue
        relative = path.relative_to(root).as_posix()
        entries.append(
            {
                "path": relative,
                "name": path.name,
                "size": path.stat().st_size,
                "modified": _format_mtime(path),
            }
        )
    return entries


def read_wiki_file(kb_dir: Path, relative_path: str) -> dict[str, str]:
    """Read a file from `wiki/` after path validation."""
    full_path = _resolve_wiki_path(kb_dir, relative_path)
    if not full_path.exists() or not full_path.is_file():
        raise FileNotFoundError(relative_path)
    return {
        "path": full_path.relative_to((Path(kb_dir).resolve() / "wiki").resolve()).as_posix(),
        "content": full_path.read_text(encoding="utf-8"),
    }


def write_wiki_file(kb_dir: Path, relative_path: str, content: str) -> dict[str, str]:
    """Write a file under `wiki/` after path validation."""
    full_path = _resolve_wiki_path(kb_dir, relative_path)
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    return {"path": full_path.relative_to((Path(kb_dir).resolve() / "wiki").resolve()).as_posix()}


def _resolve_report_path(kb_dir: Path, report: str | None = None) -> tuple[Path, str]:
    """Resolve a lint report path under ``wiki/reports``."""
    kb_dir = require_kb_dir(kb_dir)
    if report:
        relative = str(report).replace("\\", "/").lstrip("/")
        if not relative.startswith("reports/"):
            raise PathSecurityError("Lint report must be under wiki/reports.")
        full_path = _resolve_wiki_path(kb_dir, relative)
    else:
        reports_dir = kb_dir / "wiki" / "reports"
        reports = sorted(
            (path for path in reports_dir.glob("*.md") if path.is_file()),
            key=lambda path: (path.stat().st_mtime, path.name),
            reverse=True,
        ) if reports_dir.exists() else []
        if not reports:
            raise FileNotFoundError("No lint reports found.")
        full_path = reports[0]

    reports_root = (kb_dir / "wiki" / "reports").resolve()
    if not full_path.resolve().is_relative_to(reports_root):
        raise PathSecurityError("Lint report must be under wiki/reports.")
    if not full_path.exists() or not full_path.is_file():
        raise FileNotFoundError(report or "lint report")
    relative_path = full_path.relative_to((kb_dir / "wiki").resolve()).as_posix()
    return full_path, relative_path


def build_lint_fix_plan(kb_dir: Path, report: str | None = None) -> dict[str, Any]:
    """Return safe lint fix candidates from a report without modifying the wiki."""
    kb_dir = require_kb_dir(kb_dir)
    report_path, relative_path = _resolve_report_path(kb_dir, report)
    from openkb.agent.linter import extract_lint_fix_candidates

    candidates = extract_lint_fix_candidates(
        kb_dir / "wiki",
        report_path.read_text(encoding="utf-8"),
    )
    return {"report": relative_path, "candidates": candidates}


def _approved_lint_fix_candidates(candidates: Any) -> list[dict[str, Any]]:
    if candidates is None:
        return []
    if not isinstance(candidates, list):
        raise ClientError("Candidates must be a list.")

    approved: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        if item.get("approved") is not True:
            continue
        action = str(item.get("action") or "create").strip().lower()
        if action != "create":
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        approved_item = {
            "name": name,
            "title": str(item.get("title") or name.replace("_", " ")).strip(),
            "action": "create",
        }
        for key in ("path", "type", "source_section", "reason"):
            if item.get(key):
                approved_item[key] = str(item[key])
        approved.append(approved_item)
    return approved


def apply_lint_fix_candidates(kb_dir: Path, candidates: Any) -> dict[str, Any]:
    """Create approved lint draft pages while preserving existing pages."""
    kb_dir = require_kb_dir(kb_dir)
    approved = _approved_lint_fix_candidates(candidates)
    from openkb.agent.linter import apply_lint_fix_candidates

    created = apply_lint_fix_candidates(kb_dir / "wiki", approved)
    return {"approved": approved, "created": created}


def _api_key_configured(kb_dir: Path) -> bool:
    if any(os.environ.get(key) for key in ("LLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY")):
        return True
    env_path = kb_dir / ".env"
    if not env_path.exists():
        return False
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("LLM_API_KEY=") and line.split("=", 1)[1].strip():
            return True
    return False


def _write_api_key(kb_dir: Path, api_key: str) -> None:
    env_path = kb_dir / ".env"
    replacement = f"LLM_API_KEY={api_key}\n"
    if not env_path.exists():
        env_path.write_text(replacement, encoding="utf-8")
        os.chmod(env_path, 0o600)
        return

    lines = env_path.read_text(encoding="utf-8").splitlines()
    updated: list[str] = []
    found = False
    for line in lines:
        if line.strip().startswith("LLM_API_KEY="):
            updated.append(f"LLM_API_KEY={api_key}")
            found = True
        else:
            updated.append(line)
    if not found:
        updated.append(f"LLM_API_KEY={api_key}")
    env_path.write_text("\n".join(updated).rstrip("\n") + "\n", encoding="utf-8")
    os.chmod(env_path, 0o600)


def get_config_data(kb_dir: Path) -> dict[str, Any]:
    """Return public configuration data without exposing secret values."""
    kb_dir = require_kb_dir(kb_dir)
    config = load_config(kb_dir / ".openkb" / "config.yaml")
    return {
        "model": config.get("model", DEFAULT_CONFIG["model"]),
        "language": config.get("language", DEFAULT_CONFIG["language"]),
        "pageindex_threshold": config.get("pageindex_threshold", DEFAULT_CONFIG["pageindex_threshold"]),
        "wire_api": config.get("wire_api", DEFAULT_CONFIG["wire_api"]),
        "base_url": config.get("base_url", DEFAULT_CONFIG.get("base_url", "")) or "",
        "api_key_configured": _api_key_configured(kb_dir),
    }


def update_config_data(kb_dir: Path, updates: dict[str, Any]) -> dict[str, Any]:
    """Persist allowed config fields and return the public config view."""
    kb_dir = require_kb_dir(kb_dir)
    config_path = kb_dir / ".openkb" / "config.yaml"
    config = load_config(config_path)
    for key, value in updates.items():
        if key not in _CONFIG_KEYS:
            continue
        if key == "pageindex_threshold":
            value = int(value)
        if key == "base_url":
            value = str(value or "").strip().rstrip("/")
        config[key] = value
    save_config(config_path, config)
    api_key = str(updates.get("api_key") or "").strip()
    if api_key:
        _write_api_key(kb_dir, api_key)
    return get_config_data(kb_dir)


def init_kb(
    kb_dir: Path,
    *,
    model: str,
    language: str = "en",
    pageindex_threshold: int = 20,
    wire_api: str | None = None,
    base_url: str = "",
    api_key: str = "",
    make_default: bool = False,
) -> dict[str, Any]:
    """Create a new OpenKB directory layout."""
    kb_dir = Path(kb_dir).resolve()
    openkb_dir = kb_dir / ".openkb"
    if openkb_dir.exists():
        raise ClientError("Knowledge base already initialized.")

    (kb_dir / "raw").mkdir(parents=True, exist_ok=True)
    (kb_dir / "wiki" / "sources" / "images").mkdir(parents=True, exist_ok=True)
    (kb_dir / "wiki" / "summaries").mkdir(parents=True, exist_ok=True)
    (kb_dir / "wiki" / "companies").mkdir(parents=True, exist_ok=True)
    (kb_dir / "wiki" / "industries").mkdir(parents=True, exist_ok=True)
    (kb_dir / "wiki" / "themes").mkdir(parents=True, exist_ok=True)
    (kb_dir / "wiki" / "metrics").mkdir(parents=True, exist_ok=True)
    (kb_dir / "wiki" / "risks").mkdir(parents=True, exist_ok=True)
    (kb_dir / "wiki" / "concepts").mkdir(parents=True, exist_ok=True)
    (kb_dir / "wiki" / "explorations").mkdir(parents=True, exist_ok=True)
    (kb_dir / "wiki" / "reports").mkdir(parents=True, exist_ok=True)

    (kb_dir / "wiki" / "AGENTS.md").write_text(AGENTS_MD, encoding="utf-8")
    (kb_dir / "wiki" / "index.md").write_text(
        "# Knowledge Base Index\n\n"
        "## Documents\n\n"
        "## Companies\n\n"
        "## Industries\n\n"
        "## Themes\n\n"
        "## Metrics\n\n"
        "## Risks\n\n"
        "## Concepts\n\n"
        "## Explorations\n",
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "log.md").write_text("# Operations Log\n\n", encoding="utf-8")

    openkb_dir.mkdir(parents=True)
    config = {
        "model": model,
        "language": language,
        "pageindex_threshold": int(pageindex_threshold),
        "wire_api": wire_api or ("responses" if model_prefers_responses_api(model) else DEFAULT_CONFIG["wire_api"]),
        "base_url": base_url.strip().rstrip("/"),
    }
    save_config(openkb_dir / "config.yaml", config)
    (openkb_dir / "hashes.json").write_text("{}", encoding="utf-8")

    if api_key:
        _write_api_key(kb_dir, api_key)

    if make_default:
        register_kb(kb_dir)

    return {"kb_dir": str(kb_dir), "config": get_config_data(kb_dir)}


def get_known_kbs() -> dict[str, Any]:
    """Return globally registered KBs with existence metadata."""
    config = load_global_config()
    default = config.get("default_kb")
    known = []
    for raw_path in config.get("known_kbs", []):
        path = Path(raw_path)
        known.append(
            {
                "path": str(path),
                "exists": path.exists(),
                "is_kb": is_kb_dir(path),
                "is_default": str(path.resolve()) == str(Path(default).resolve()) if default else False,
            }
        )
    return {"default_kb": default, "known_kbs": known}


def use_kb(kb_dir: Path) -> dict[str, Any]:
    """Set a KB as the global default."""
    kb_dir = require_kb_dir(kb_dir)
    register_kb(kb_dir)
    return {"kb_dir": str(kb_dir)}
