"""Structured helpers used by the local OpenKB client.

The CLI primarily prints human-readable output. These helpers return JSON-ready
data so a browser client can reuse the same storage conventions without parsing
terminal text.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from datetime import datetime, timezone
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
from openkb.kb_git import commit_kb_changes, commit_kb_paths, ensure_kb_git
from openkb.model_pool import model_pool_config
from openkb.schema import AGENTS_MD, LEGACY_WIKI_DIRS
from openkb.source_relations import (
    formal_raw_relative_path,
    normalize_kb_relative_path,
    resolve_source_artifact_path,
    review_summary_storage_relative_path,
    source_images_dir_for_source_path,
)


UTC = timezone.utc


class ClientError(RuntimeError):
    """Base error raised by client helpers."""


class PathSecurityError(ClientError, ValueError):
    """Raised when a requested path escapes the allowed root."""


_TYPE_DISPLAY_MAP = {
    "long_pdf": "pageindex",
    "local_long_pdf": "pageindex",
    "page_json": "pageindex",
    "pageindex_cloud": "pageindex",
    "local_long_json": "pageindex",
}

_SHORT_DOC_TYPES = {"pdf", "docx", "md", "markdown", "html", "htm", "txt", "csv", "pptx", "xlsx"}

_GENERAL_CONFIG_KEYS = {
    "language",
    "pageindex_threshold",
    "compile_max_concurrency",
    "ingest_gate_enabled",
    "ingest_gate_pass_threshold",
    "ingest_gate_hold_threshold",
    "ingest_gate_hard_reject_enabled",
    "ingest_gate_log_all_decisions",
    "ingest_gate_allow_force_pass",
    "ingest_gate_allow_force_reject",
    "ocr_enabled",
    "ocr_detection_mode",
    "ocr_default_model",
    "ocr_chunk_pages",
    "ocr_auto_recommend",
    "pageindex_local_enabled",
    "pageindex_local_model",
    "pageindex_local_installation_state",
}
_MODEL_POOL_CONFIG_KEYS = {
    "model_pool_enabled",
    "model_pool_strategy",
    "model_pool_probe_interval_seconds",
    "model_pool_failure_threshold",
    "model_pool_timeout_seconds",
}
_PROFILE_CONFIG_KEYS = {"model", "wire_api", "base_url", "provider", "reasoning_effort"}
_PROFILE_LIST_KEYS = {"tags", "features", "probe_models"}
_PROFILE_BOOL_KEYS = {"enabled", "thinking_enabled"}
_PROFILE_INT_KEYS = {"priority"}
_DEFAULT_PROFILE_ID = "default"
_CONFIG_EXPORT_FORMAT = "openkb.settings-config.v1"
_PADDLEOCR_TOKEN_ENV = "PADDLEOCR_TOKEN"
_PAGEINDEX_LOCAL_RUNTIME_FIELD_MAP = {
    "pageindex_local_repo_dir": "repo_dir",
    "pageindex_local_python_path": "python_path",
    "pageindex_local_script_path": "script_path",
    "pageindex_local_version": "version",
}


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
        "concepts",
        "reports",
    ):
        path = wiki_dir / name
        directories[name] = len(list(path.glob("*.md"))) if path.exists() else 0

    raw_count = 0
    raw_dir = kb_dir / "raw"
    if raw_dir.exists():
        raw_count += len([p for p in raw_dir.iterdir() if p.is_file()])
    staged_raw_root = kb_dir / ".openkb" / "raw"
    if staged_raw_root.exists():
        raw_count += len([p for p in staged_raw_root.glob("*/*") if p.is_file()])
    directories["raw"] = raw_count

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


def get_document_data(
    kb_dir: Path,
    *,
    query: str = "",
    workflow_status: str = "",
    ingest_state: str = "",
    ocr_state: str = "",
    source_state: str = "",
    summary_state: str = "",
    review_state: str = "",
    promotion_state: str = "",
) -> dict[str, Any]:
    """Return indexed documents and wiki page lists."""
    kb_dir = require_kb_dir(kb_dir)
    from openkb.document_ledger import (
        list_effective_document_ledger_records,
    )
    from openkb.source_relations import get_source_documents

    ledger_records = list_effective_document_ledger_records(kb_dir)
    documents = []
    for document in get_source_documents(kb_dir):
        file_hash = str(document["hash"])
        ledger_record = ledger_records[file_hash]
        review_summary_path = (
            str(ledger_record.get("review_summary_path") or "")
            or document.get("review_summary_path")
        )
        review_summary_exists = bool(
            str(ledger_record.get("review_summary_path") or "")
            or document.get("review_summary_exists", False)
        )
        documents.append(
            {
                "hash": file_hash,
                "name": document["name"],
                "type": _display_type(str(document.get("type", "unknown"))),
                "pages": document.get("pages", ""),
                "stem": ledger_record.get("stem") or document["stem"],
                "raw_path": ledger_record.get("raw_path") or document.get("raw_path"),
                "raw_exists": bool(
                    (ledger_record.get("raw_path") or document.get("raw_path"))
                    and (kb_dir / normalize_kb_relative_path(ledger_record.get("raw_path") or document.get("raw_path"))).exists()
                ),
                "source_path": ledger_record.get("source_path") or document.get("source_path"),
                "source_summary": document.get("source_summary"),
                "summary_exists": document.get("summary_exists", False),
                "review_summary_path": review_summary_path,
                "review_summary_exists": review_summary_exists,
                "ingested_at": document.get("ingested_at"),
                "ingested_date": document.get("ingested_date"),
                "related_count": document["related_count"],
                "related_pages": document["related_pages"],
                "source_kind": ledger_record["source_kind"],
                "scan_detected": ledger_record["scan_detected"],
                "workflow_state": ledger_record["workflow_state"],
                "review": _review_payload_with_fallback_score(
                    kb_dir,
                    ledger_record["review"],
                    review_summary_path=review_summary_path,
                    review_summary_exists=review_summary_exists,
                ),
                "execution": ledger_record["execution"],
            }
        )
    known_hashes = {document["hash"] for document in documents}
    for file_hash, ledger_record in ledger_records.items():
        if file_hash in known_hashes:
            continue
        review_summary_path = str(ledger_record.get("review_summary_path") or "") or None
        review_summary_exists = bool(
            review_summary_storage_relative_path(ledger_record.get("review_summary_path"))
            and (
                kb_dir
                / ".openkb"
                / review_summary_storage_relative_path(ledger_record.get("review_summary_path"))
            ).exists()
        )
        documents.append(
            {
                "hash": file_hash,
                "name": ledger_record["name"],
                "type": _display_type(str(ledger_record.get("source_kind") or "unknown")),
                "pages": ledger_record.get("page_count") or "",
                "stem": ledger_record["stem"],
                "raw_path": ledger_record["raw_path"],
                "raw_exists": bool(
                    ledger_record["raw_path"]
                    and (kb_dir / normalize_kb_relative_path(ledger_record["raw_path"])).exists()
                ),
                "source_path": str(ledger_record.get("source_path") or "") or None,
                "source_summary": f"summaries/{ledger_record['stem']}.md" if ledger_record["stem"] else None,
                "summary_exists": False,
                "review_summary_path": review_summary_path,
                "review_summary_exists": review_summary_exists,
                "ingested_at": ledger_record.get("ingested_at"),
                "ingested_date": _ingested_date_from_iso(ledger_record.get("ingested_at")),
                "related_count": 0,
                "related_pages": {
                    "summaries": [],
                    "companies": [],
                    "industries": [],
                    "concepts": [],
                },
                "source_kind": ledger_record["source_kind"],
                "scan_detected": ledger_record["scan_detected"],
                "workflow_state": ledger_record["workflow_state"],
                "review": _review_payload_with_fallback_score(
                    kb_dir,
                    ledger_record["review"],
                    review_summary_path=review_summary_path,
                    review_summary_exists=review_summary_exists,
                ),
                "execution": ledger_record["execution"],
            }
        )
    documents = _filter_document_payloads(
        documents,
        query=query,
        workflow_status=workflow_status,
        state_filters={
            "ingest_state": ingest_state,
            "ocr_state": ocr_state,
            "source_state": source_state,
            "summary_state": summary_state,
            "review_state": review_state,
            "promotion_state": promotion_state,
        },
    )

    wiki_dir = kb_dir / "wiki"
    return {
        "documents": documents,
        "summaries": _list_stems(wiki_dir / "summaries"),
        "companies": _list_stems(wiki_dir / "companies"),
        "industries": _list_stems(wiki_dir / "industries"),
        "concepts": _list_stems(wiki_dir / "concepts"),
        "reports": _list_names(wiki_dir / "reports"),
    }


def _filter_document_payloads(
    documents: list[dict[str, Any]],
    *,
    query: str = "",
    workflow_status: str = "",
    state_filters: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    filtered = list(documents)
    needle = str(query or "").strip().casefold()
    if needle:
        filtered = [
            document
            for document in filtered
            if _document_matches_query(document, needle)
        ]

    for state_name, raw_value in (state_filters or {}).items():
        allowed = _split_filter_values(raw_value)
        if not allowed:
            continue
        filtered = [
            document
            for document in filtered
            if str(document.get("workflow_state", {}).get(state_name) or "").casefold() in allowed
        ]
    workflow_allowed = _split_filter_values(workflow_status)
    if workflow_allowed:
        filtered = [
            document
            for document in filtered
            if _document_matches_workflow_status(document, workflow_allowed)
        ]
    return sorted(filtered, key=_document_sort_key, reverse=True)


def _review_payload_with_fallback_score(
    kb_dir: Path,
    review: dict[str, Any],
    *,
    review_summary_path: str | None,
    review_summary_exists: bool,
) -> dict[str, Any]:
    payload = dict(review)
    if payload.get("summary_score") is not None or not review_summary_exists or not review_summary_path:
        return payload
    storage_rel = review_summary_storage_relative_path(review_summary_path)
    if not storage_rel:
        return payload
    root = (Path(kb_dir) / ".openkb").resolve()
    summary_path = (root / storage_rel).resolve()
    if not summary_path.is_relative_to(root):
        return payload
    try:
        summary_text = summary_path.read_text(encoding="utf-8")
    except OSError:
        return payload

    from openkb.workflows.summary_pipeline import _fallback_summary_scorecard

    scorecard = _fallback_summary_scorecard(summary_text)
    payload["summary_score"] = scorecard["total_score"]
    payload["summary_score_source"] = "heuristic"
    payload["summary_scorecard"] = scorecard
    return payload


def _document_matches_query(document: dict[str, Any], needle: str) -> bool:
    haystack = [
        document.get("name"),
        document.get("stem"),
        document.get("type"),
        document.get("source_kind"),
        document.get("raw_path"),
        document.get("source_path"),
    ]
    return any(needle in str(value or "").casefold() for value in haystack)


def _document_matches_workflow_status(document: dict[str, Any], allowed: set[str]) -> bool:
    workflow_state = document.get("workflow_state") if isinstance(document.get("workflow_state"), dict) else {}
    execution = document.get("execution") if isinstance(document.get("execution"), dict) else {}
    values = {str(value or "").casefold() for value in workflow_state.values()}
    if "failed" in allowed and ("failed" in values or str(execution.get("last_error") or "").strip()):
        return True
    if "new" in allowed:
        if (
            "failed" not in values
            and str(workflow_state.get("ingest_state") or "").casefold() == "imported"
            and str(workflow_state.get("summary_state") or "").casefold() == "not_started"
            and str(workflow_state.get("review_state") or "").casefold() == "unreviewed"
            and str(workflow_state.get("promotion_state") or "").casefold() == "not_selected"
        ):
            return True
    return bool(values & allowed)


def _document_sort_key(document: dict[str, Any]) -> tuple[str, str, str, str]:
    execution = document.get("execution") if isinstance(document.get("execution"), dict) else {}
    ingested_at = str(document.get("ingested_at") or "").strip()
    updated_at = str(execution.get("updated_at") or "").strip()
    return (ingested_at, updated_at, str(document.get("name") or "").casefold(), str(document.get("hash") or ""))


def _split_filter_values(raw_value: str) -> set[str]:
    return {
        item.strip().casefold()
        for item in str(raw_value or "").split(",")
        if item.strip()
    }


def _ingested_date_from_iso(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def get_source_document_data(kb_dir: Path, selector: str) -> dict[str, Any]:
    """Return one indexed document with generated wiki page relations."""
    kb_dir = require_kb_dir(kb_dir)
    from openkb.source_relations import get_source_document_detail

    document = get_source_document_detail(kb_dir, selector)
    document["type"] = _display_type(str(document.get("type", "unknown")))
    return document


def delete_source_document_data(kb_dir: Path, selector: str) -> dict[str, Any]:
    """Delete one indexed document and clean its generated wiki relations."""
    kb_dir = require_kb_dir(kb_dir)
    from openkb.document_ledger import delete_document_ledger_record
    from openkb.source_relations import delete_source_document

    try:
        result = delete_source_document(kb_dir, selector)
    except ValueError as exc:
        if not str(exc).startswith("No indexed source document matches:"):
            raise
        result = _delete_ledger_only_source_document_data(kb_dir, selector)
    else:
        delete_document_ledger_record(kb_dir, str(result["document"].get("hash") or ""))
    result["document"]["type"] = _display_type(str(result["document"].get("type", "unknown")))
    commit_kb_changes(kb_dir, f"Delete source {result['document'].get('name', selector)}")
    return result


def _delete_ledger_only_source_document_data(kb_dir: Path, selector: str) -> dict[str, Any]:
    from openkb.document_ledger import delete_document_ledger_record, list_document_ledger_records

    file_hash, record = _resolve_ledger_source_record(list_document_ledger_records(kb_dir), selector)
    stem = Path(str(record.get("stem") or Path(str(record.get("name") or "")).stem).strip()).name
    name = str(record.get("name") or stem or selector).strip()
    raw_path = normalize_kb_relative_path(record.get("raw_path"))
    source_path = normalize_kb_relative_path(record.get("source_path"))
    page_count = record.get("page_count")
    ingested_at = record.get("ingested_at")

    removed_pages: list[str] = []
    removed_files: list[str] = []

    if stem:
        summary_removed = _remove_kb_relative_path(kb_dir, f"wiki/summaries/{stem}.md")
        if summary_removed:
            removed_pages.append(_wiki_relative(summary_removed))

    raw_candidates: list[str] = []
    if raw_path:
        raw_candidates.append(raw_path)
    if name:
        raw_candidates.append(formal_raw_relative_path(name))
    for candidate in raw_candidates:
        removed = _remove_kb_relative_path(kb_dir, candidate)
        if removed and removed not in removed_files:
            removed_files.append(removed)

    source_candidates: list[str] = []
    if source_path:
        source_candidates.append(source_path)
    if stem:
        source_candidates.extend(
            (
                f"wiki/sources/{stem}.md",
                f"wiki/sources/{stem}.json",
            )
        )
    for candidate in source_candidates:
        removed = _remove_kb_relative_path(kb_dir, candidate)
        if removed and removed not in removed_files:
            removed_files.append(removed)

    images_dir = source_images_dir_for_source_path(source_path) if source_path else None
    if images_dir:
        removed = _remove_kb_relative_path(kb_dir, images_dir)
        if removed and removed not in removed_files:
            removed_files.append(removed)
    elif stem:
        for candidate in (
            f"wiki/sources/images/{stem}",
        ):
            removed = _remove_kb_relative_path(kb_dir, candidate)
            if removed and removed not in removed_files:
                removed_files.append(removed)

    delete_document_ledger_record(kb_dir, file_hash)
    return {
        "document": {
            "hash": file_hash,
            "name": name,
            "stem": stem,
            "type": str(record.get("source_kind") or "unknown"),
            "pages": page_count or "",
            "ingested_at": ingested_at,
            "ingested_date": _ingested_date_from_iso(ingested_at),
            "raw_path": raw_path,
            "raw_exists": bool(raw_path and (kb_dir / raw_path).exists()),
            "source_path": source_path or (f"sources/{stem}.md" if stem else None),
            "source_summary": f"summaries/{stem}.md" if stem else None,
            "summary_exists": False,
            "review_summary_path": str(record.get("review_summary_path") or "") or None,
            "review_summary_exists": bool(
                review_summary_storage_relative_path(record.get("review_summary_path"))
                and (
                    kb_dir
                    / ".openkb"
                    / review_summary_storage_relative_path(record.get("review_summary_path"))
                ).exists()
            ),
            "related_count": 0,
            "related_pages": {
                "summaries": [],
                "companies": [],
                "industries": [],
                "concepts": [],
            },
        },
        "removed_pages": removed_pages,
        "updated_pages": [],
        "removed_files": removed_files,
    }


def _resolve_ledger_source_record(
    records: dict[str, dict[str, Any]],
    selector: str,
) -> tuple[str, dict[str, Any]]:
    needle = str(selector or "").strip()
    if not needle:
        raise ValueError("Source document selector is required.")
    needle_fold = needle.casefold()
    matches: list[tuple[str, dict[str, Any]]] = []
    for file_hash, record in records.items():
        candidates = [
            file_hash,
            record.get("file_hash"),
            record.get("name"),
            record.get("stem"),
            record.get("raw_path"),
            Path(str(record.get("raw_path") or "")).name,
        ]
        if file_hash.startswith(needle) or any(str(value or "").casefold() == needle_fold for value in candidates):
            matches.append((file_hash, record))
    if not matches:
        raise ValueError(f"No indexed source document matches: {selector}")
    if len(matches) > 1:
        names = ", ".join(sorted(str(record.get("name") or file_hash) for file_hash, record in matches))
        raise ValueError(f"Ambiguous source document selector {selector!r}: {names}")
    return matches[0]


def _remove_kb_relative_path(kb_dir: Path, relative_path: str) -> str | None:
    raw = str(relative_path or "").strip().replace("\\", "/").lstrip("/")
    if not raw:
        return None
    root = kb_dir.resolve()
    full_path = (root / raw).resolve()
    if not full_path.is_relative_to(root):
        raise PathSecurityError("Path escapes knowledge base root.")
    if not full_path.exists():
        return None
    rel = full_path.relative_to(root).as_posix()
    if full_path.is_dir():
        shutil.rmtree(full_path)
        return f"{rel}/"
    full_path.unlink()
    return rel


def _wiki_relative(relative_path: str) -> str:
    return relative_path.removeprefix("wiki/")


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
        parts = relative.split("/")
        if parts and parts[0] in LEGACY_WIKI_DIRS:
            continue
        entries.append(
            {
                "path": relative,
                "name": path.name,
                "directory": "/".join(parts[:-1]),
                "depth": len(parts) - 1,
                "extension": path.suffix.lower(),
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


def read_review_summary_file(kb_dir: Path, relative_path: str) -> dict[str, str]:
    """Read a file from `.openkb/review_summaries/` after path validation."""
    kb_dir = require_kb_dir(kb_dir)
    raw = str(relative_path or "").strip().replace("\\", "/").lstrip("/")
    if not raw.startswith("review_summaries/"):
        raise PathSecurityError("Review summary path must be under .openkb/review_summaries.")
    root = (kb_dir / ".openkb").resolve()
    full_path = (root / raw).resolve()
    if not full_path.is_relative_to(root):
        raise PathSecurityError("Path escapes .openkb root.")
    if not full_path.exists() or not full_path.is_file():
        raise FileNotFoundError(relative_path)
    return {
        "path": full_path.relative_to(root).as_posix(),
        "content": full_path.read_text(encoding="utf-8"),
    }


def resolve_raw_source_file(kb_dir: Path, relative_path: str) -> Path:
    """Resolve a raw source file path after constraining it to raw storage roots."""
    kb_dir = require_kb_dir(kb_dir)
    raw = normalize_kb_relative_path(relative_path)
    if not raw.startswith(("raw/", ".openkb/raw/")):
        raise PathSecurityError("Raw source path must be under raw/ or .openkb/raw/.")

    full_path = (kb_dir / raw).resolve()
    allowed_roots = [
        (kb_dir / "raw").resolve(),
        (kb_dir / ".openkb" / "raw").resolve(),
    ]
    if not any(full_path.is_relative_to(root) for root in allowed_roots):
        raise PathSecurityError("Path escapes raw source storage.")
    if not full_path.exists() or not full_path.is_file():
        raise FileNotFoundError(relative_path)
    return full_path


def write_wiki_file(kb_dir: Path, relative_path: str, content: str) -> dict[str, str]:
    """Write a file under `wiki/` after path validation."""
    full_path = _resolve_wiki_path(kb_dir, relative_path)
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    relative = full_path.relative_to((Path(kb_dir).resolve() / "wiki").resolve()).as_posix()
    commit_kb_changes(kb_dir, f"Edit wiki {relative}")
    return {"path": relative}


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
        if action not in {"create", "manual-review"}:
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        approved_item = {
            "name": name,
            "title": str(item.get("title") or name.replace("_", " ")).strip(),
            "action": action,
        }
        for key in ("path", "type", "source_section", "reason", "preview"):
            if item.get(key):
                approved_item[key] = str(item[key])
        approved.append(approved_item)
    return approved


def apply_lint_fix_candidates(kb_dir: Path, candidates: Any) -> dict[str, Any]:
    """Create approved lint draft pages while preserving existing pages."""
    kb_dir = require_kb_dir(kb_dir)
    approved = _approved_lint_fix_candidates(candidates)
    create_candidates = [item for item in approved if item.get("action") == "create"]
    reviewed = [item for item in approved if item.get("action") == "manual-review"]
    from openkb.agent.linter import apply_lint_fix_candidates

    created = apply_lint_fix_candidates(kb_dir / "wiki", create_candidates)
    commit_kb_changes(kb_dir, "Apply lint fixes")
    return {"approved": approved, "created": created, "reviewed": reviewed}


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


def _read_env_values(kb_dir: Path) -> dict[str, str]:
    env_path = kb_dir / ".env"
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _write_env_values(kb_dir: Path, values: dict[str, str]) -> None:
    env_path = kb_dir / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    updated: list[str] = []
    remaining = dict(values)
    for line in lines:
        if "=" not in line or line.strip().startswith("#"):
            updated.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            updated.append(f"{key}={remaining.pop(key)}")
        else:
            updated.append(line)
    for key, value in remaining.items():
        updated.append(f"{key}={value}")
    env_path.write_text("\n".join(updated).rstrip("\n") + "\n", encoding="utf-8")
    os.chmod(env_path, 0o600)


def _write_api_key(kb_dir: Path, api_key: str) -> None:
    _write_env_values(kb_dir, {"LLM_API_KEY": api_key})


def _paddleocr_token(kb_dir: Path, env_values: dict[str, str] | None = None) -> str:
    env_values = env_values if env_values is not None else _read_env_values(kb_dir)
    token = env_values.get(_PADDLEOCR_TOKEN_ENV, "").strip()
    if token:
        return token
    return (os.environ.get(_PADDLEOCR_TOKEN_ENV) or "").strip()


def _pageindex_local_root(kb_dir: Path) -> Path:
    return kb_dir / ".openkb" / "pageindex-local"


def _pageindex_local_runtime_fields(kb_dir: Path, *, include_version: bool = False) -> dict[str, str]:
    from openkb.pageindex_local.runtime import read_pageindex_local_manifest

    manifest = read_pageindex_local_manifest(_pageindex_local_root(kb_dir)) or {}
    keys = _PAGEINDEX_LOCAL_RUNTIME_FIELD_MAP.items()
    if not include_version:
        keys = [(field, manifest_key) for field, manifest_key in keys if field != "pageindex_local_version"]
    return {
        field: str(manifest.get(manifest_key) or "").strip()
        for field, manifest_key in keys
    }


def _update_pageindex_local_runtime(kb_dir: Path, values: dict[str, Any]) -> None:
    from openkb.pageindex_local.runtime import read_pageindex_local_manifest, write_pageindex_local_manifest

    relevant = {
        field: str(values.get(field) or "").strip()
        for field in _PAGEINDEX_LOCAL_RUNTIME_FIELD_MAP
        if field in values
    }
    if not relevant:
        return

    root = _pageindex_local_root(kb_dir)
    manifest = read_pageindex_local_manifest(root) or {}
    for field, manifest_key in _PAGEINDEX_LOCAL_RUNTIME_FIELD_MAP.items():
        if field in relevant:
            value = relevant[field]
            if manifest_key == "version" and not value:
                manifest.pop(manifest_key, None)
            else:
                manifest[manifest_key] = value
    write_pageindex_local_manifest(manifest, root)


def _profile_id_from_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "profile"


def _unique_profile_id(base: str, existing: set[str]) -> str:
    candidate = _profile_id_from_name(base)
    if candidate not in existing:
        return candidate
    index = 2
    while f"{candidate}-{index}" in existing:
        index += 1
    return f"{candidate}-{index}"


def _profile_env_key(profile_id: str) -> str:
    env_id = re.sub(r"[^A-Za-z0-9]+", "_", profile_id).strip("_").upper() or "PROFILE"
    return f"OPENKB_LLM_PROFILE_{env_id}_API_KEY"


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _model_rows(value: Any, fallback_model: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("model") or "").strip()
                weight = max(int(item.get("weight") or 100), 1)
            else:
                name = str(item or "").strip()
                weight = 100
            if name:
                rows.append({"name": name, "weight": weight})
    elif isinstance(value, str):
        for item in _string_list(value):
            rows.append({"name": item, "weight": 100})
    if not rows and fallback_model:
        rows.append({"name": fallback_model, "weight": 100})
    return rows


def _normalize_profile(raw: dict[str, Any], fallback_id: str, config: dict[str, Any]) -> dict[str, Any]:
    profile_id = str(raw.get("id") or fallback_id or _DEFAULT_PROFILE_ID).strip() or _DEFAULT_PROFILE_ID
    model = str(raw.get("model") or config.get("model") or DEFAULT_CONFIG["model"]).strip()
    probe_models = _string_list(raw.get("probe_models")) or [model]
    models = _model_rows(raw.get("models"), model)
    if not models:
        models = [{"name": item, "weight": 100} for item in probe_models]
    provider = str(raw.get("provider") or "").strip().lower() or "generic"
    reasoning_effort = str(raw.get("reasoning_effort") or "").strip().lower()
    return {
        "id": profile_id,
        "name": str(raw.get("name") or ("Default" if profile_id == _DEFAULT_PROFILE_ID else profile_id)).strip(),
        "model": model,
        "wire_api": str(raw.get("wire_api") or config.get("wire_api") or DEFAULT_CONFIG["wire_api"]).strip().lower(),
        "base_url": str(raw.get("base_url") or config.get("base_url") or DEFAULT_CONFIG.get("base_url", "")).strip().rstrip("/"),
        "provider": provider,
        "reasoning_effort": reasoning_effort,
        "thinking_enabled": bool(raw.get("thinking_enabled", False)),
        "api_key_env": str(raw.get("api_key_env") or _profile_env_key(profile_id)).strip(),
        "enabled": bool(raw.get("enabled", True)),
        "tags": _string_list(raw.get("tags")),
        "features": _string_list(raw.get("features")),
        "probe_models": probe_models,
        "models": models,
        "priority": int(raw.get("priority") or 50),
    }


def _normalize_profiles(config: dict[str, Any]) -> tuple[list[dict[str, str]], str]:
    raw_profiles = config.get("llm_profiles")
    profiles: list[dict[str, str]] = []
    seen: set[str] = set()

    if isinstance(raw_profiles, list):
        for raw in raw_profiles:
            if not isinstance(raw, dict):
                continue
            profile = _normalize_profile(raw, str(raw.get("id") or ""), config)
            if profile["id"] in seen:
                continue
            profiles.append(profile)
            seen.add(profile["id"])
    elif isinstance(raw_profiles, dict):
        for profile_id, raw in raw_profiles.items():
            if not isinstance(raw, dict):
                continue
            profile = _normalize_profile(raw, str(profile_id), config)
            if profile["id"] in seen:
                continue
            profiles.append(profile)
            seen.add(profile["id"])

    if not profiles:
        profiles.append(
            _normalize_profile(
                {
                    "id": _DEFAULT_PROFILE_ID,
                    "name": "Default",
                    "model": config.get("model", DEFAULT_CONFIG["model"]),
                    "wire_api": config.get("wire_api", DEFAULT_CONFIG["wire_api"]),
                    "base_url": config.get("base_url", DEFAULT_CONFIG.get("base_url", "")),
                },
                _DEFAULT_PROFILE_ID,
                config,
            )
        )

    active_id = _configured_active_profile_id(
        profiles,
        str(config.get("active_llm_profile") or profiles[0]["id"]).strip() or profiles[0]["id"],
    )
    return profiles, active_id


def _find_profile(profiles: list[dict[str, str]], profile_id: str) -> dict[str, str] | None:
    return next((profile for profile in profiles if profile["id"] == profile_id), None)


def _configured_active_profile_id(profiles: list[dict[str, Any]], preferred_id: str) -> str:
    if not profiles:
        return _DEFAULT_PROFILE_ID
    preferred_id = str(preferred_id or "").strip()
    if preferred_id and _find_profile(profiles, preferred_id) is not None:
        return preferred_id
    return profiles[0]["id"]


def _enabled_active_profile_id(profiles: list[dict[str, Any]], preferred_id: str) -> str:
    if not profiles:
        return _DEFAULT_PROFILE_ID
    preferred_id = str(preferred_id or "").strip()
    preferred = _find_profile(profiles, preferred_id) if preferred_id else None
    if preferred is not None and preferred.get("enabled", True):
        return preferred["id"]
    enabled = next((profile for profile in profiles if profile.get("enabled", True)), None)
    if enabled is not None:
        return enabled["id"]
    if preferred is not None:
        return preferred["id"]
    return profiles[0]["id"]


def _profile_has_api_key(
    kb_dir: Path,
    profile: dict[str, str],
    active_id: str,
    env_values: dict[str, str] | None = None,
) -> bool:
    return bool(_profile_api_key(kb_dir, profile, active_id, env_values))


def _profile_public_payload(
    kb_dir: Path,
    profile: dict[str, Any],
    active_id: str,
    env_values: dict[str, str] | None = None,
) -> dict[str, Any]:
    env_values = env_values if env_values is not None else _read_env_values(kb_dir)
    models = list(profile.get("models") or [{"name": profile["model"], "weight": 100}])
    probe_models = list(profile.get("probe_models") or [item["name"] for item in models])
    return {
        "id": profile["id"],
        "name": profile["name"],
        "model": profile["model"],
        "wire_api": profile["wire_api"],
        "base_url": profile["base_url"],
        "provider": str(profile.get("provider") or "generic"),
        "reasoning_effort": str(profile.get("reasoning_effort") or ""),
        "thinking_enabled": bool(profile.get("thinking_enabled", False)),
        "enabled": bool(profile.get("enabled", True)),
        "tags": list(profile.get("tags") or []),
        "features": list(profile.get("features") or []),
        "probe_models": probe_models,
        "models": models,
        "priority": int(profile.get("priority") or 50),
        "api_key": _profile_api_key(kb_dir, profile, active_id, env_values),
        "api_key_configured": _profile_has_api_key(kb_dir, profile, active_id, env_values),
        "is_active": profile["id"] == active_id,
    }


def _profile_api_key(
    kb_dir: Path,
    profile: dict[str, str],
    active_id: str,
    env_values: dict[str, str] | None = None,
) -> str:
    env_values = env_values if env_values is not None else _read_env_values(kb_dir)
    env_key = profile.get("api_key_env") or _profile_env_key(profile["id"])
    api_key = env_values.get(env_key, "").strip()
    if api_key:
        return api_key
    if profile["id"] == active_id:
        for key in ("LLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
            api_key = env_values.get(key, "").strip()
            if api_key:
                return api_key
    api_key = (os.environ.get(env_key) or "").strip()
    if api_key:
        return api_key
    if profile["id"] == active_id:
        for key in ("LLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
            api_key = (os.environ.get(key) or "").strip()
            if api_key:
                return api_key
    return ""


def _public_profiles(kb_dir: Path, profiles: list[dict[str, str]], active_id: str) -> list[dict[str, Any]]:
    env_values = _read_env_values(kb_dir)
    return [_profile_public_payload(kb_dir, profile, active_id, env_values) for profile in profiles]


def _persist_profiles(config: dict[str, Any], profiles: list[dict[str, str]], active_id: str) -> None:
    if profiles:
        active_profile = _find_profile(profiles, active_id) or profiles[0]
    else:
        active_profile = _normalize_profile(config, _DEFAULT_PROFILE_ID, config)
        profiles = [active_profile]
    config["active_llm_profile"] = active_profile["id"]
    config["llm_profiles"] = [
        {
            "id": profile["id"],
            "name": profile["name"],
            "model": profile["model"],
            "wire_api": profile["wire_api"],
            "base_url": profile["base_url"],
            "provider": str(profile.get("provider") or "generic"),
            "reasoning_effort": str(profile.get("reasoning_effort") or ""),
            "thinking_enabled": bool(profile.get("thinking_enabled", False)),
            "api_key_env": profile["api_key_env"],
            "enabled": bool(profile.get("enabled", True)),
            "tags": list(profile.get("tags") or []),
            "features": list(profile.get("features") or []),
            "probe_models": list(profile.get("probe_models") or [profile["model"]]),
            "models": list(profile.get("models") or [{"name": profile["model"], "weight": 100}]),
            "priority": int(profile.get("priority") or 50),
        }
        for profile in profiles
    ]
    config["model"] = active_profile["model"]
    config["wire_api"] = active_profile["wire_api"]
    config["base_url"] = active_profile["base_url"]


def _profile_updates_from_payload(profile: dict[str, Any], updates: dict[str, Any]) -> None:
    if updates.get("name") is not None or updates.get("profile_name") is not None:
        name = str(updates.get("name") if updates.get("name") is not None else updates.get("profile_name") or "").strip()
        if name:
            profile["name"] = name
    for key in ("model", "wire_api", "base_url", "provider", "reasoning_effort"):
        if key not in updates:
            continue
        value = updates[key]
        if key == "base_url":
            value = str(value or "").strip().rstrip("/")
        elif key in {"wire_api", "provider", "reasoning_effort"}:
            value = str(value or "").strip().lower()
        else:
            value = str(value or "").strip()
        profile[key] = value
    for key in ("tags", "features", "probe_models"):
        if key in updates:
            profile[key] = _string_list(updates[key])
    if "enabled" in updates:
        profile["enabled"] = bool(updates["enabled"])
    if "priority" in updates:
        profile["priority"] = max(int(updates.get("priority") or 0), 0)
    if "models" in updates:
        models = _model_rows(updates.get("models"), str(profile.get("model") or ""))
        if models:
            profile["models"] = models
            profile["probe_models"] = [item["name"] for item in models]
            profile["model"] = models[0]["name"]


def save_model_pool_profile(kb_dir: Path, payload: dict[str, Any], profile_id: str | None = None) -> dict[str, Any]:
    kb_dir = require_kb_dir(kb_dir)
    config_path = kb_dir / ".openkb" / "config.yaml"
    config = load_config(config_path)
    profiles, active_id = _normalize_profiles(config)
    existing = {profile["id"]: profile for profile in profiles}

    if profile_id:
        target = existing.get(str(profile_id).strip())
        if target is None:
            raise ClientError(f"Unknown LLM profile: {profile_id}")
    else:
        profile_name = str(payload.get("name") or payload.get("profile_name") or payload.get("model") or "New Profile").strip()
        new_id = _unique_profile_id(str(payload.get("id") or payload.get("profile_id") or profile_name), set(existing))
        target = _normalize_profile(
            {
                "id": new_id,
                "name": profile_name or new_id,
                "model": str(payload.get("model") or DEFAULT_CONFIG["model"]).strip(),
                "wire_api": str(payload.get("wire_api") or DEFAULT_CONFIG["wire_api"]).strip().lower(),
                "base_url": str(payload.get("base_url") or "").strip().rstrip("/"),
                "provider": str(payload.get("provider") or "generic").strip().lower(),
                "reasoning_effort": str(payload.get("reasoning_effort") or "").strip().lower(),
                "thinking_enabled": bool(payload.get("thinking_enabled", False)),
                "api_key_env": _profile_env_key(new_id),
                "models": payload.get("models"),
                "enabled": payload.get("enabled", True),
            },
            new_id,
            config,
        )
        profiles.append(target)

    _profile_updates_from_payload(target, payload)
    if not target.get("models"):
        target["models"] = _model_rows(None, str(target.get("model") or DEFAULT_CONFIG["model"]))
    if not target.get("probe_models"):
        target["probe_models"] = [item["name"] for item in target["models"]]

    api_key = str(payload.get("api_key") or "").strip()
    if api_key:
        _write_profile_api_key(kb_dir, target, api_key)

    active_id = _configured_active_profile_id(profiles, active_id)
    _persist_profiles(config, profiles, active_id)
    save_config(config_path, config)
    commit_kb_paths(kb_dir, f"Update model pool profile {target['id']}", (".openkb/config.yaml",))
    return {"config": get_config_data(kb_dir), "model_pool": get_model_pool_data(kb_dir)}


def delete_model_pool_profile(kb_dir: Path, profile_id: str) -> dict[str, Any]:
    kb_dir = require_kb_dir(kb_dir)
    profile_id = str(profile_id).strip()
    config_path = kb_dir / ".openkb" / "config.yaml"
    config = load_config(config_path)
    profiles, active_id = _normalize_profiles(config)
    remaining = [profile for profile in profiles if profile["id"] != profile_id]
    if len(remaining) == len(profiles):
        raise ClientError(f"Unknown LLM profile: {profile_id}")
    next_active = active_id if active_id != profile_id and any(profile["id"] == active_id for profile in remaining) else (remaining[0]["id"] if remaining else _DEFAULT_PROFILE_ID)
    next_active = _enabled_active_profile_id(remaining, next_active)
    _persist_profiles(config, remaining, next_active)
    save_config(config_path, config)

    status = _load_model_pool_status(kb_dir)
    if isinstance(status.get("profiles"), dict):
        status["profiles"].pop(profile_id, None)
    if isinstance(status.get("routes"), dict):
        for route_key in list(status["routes"]):
            if str(route_key).startswith(f"{profile_id}:"):
                status["routes"].pop(route_key, None)
    _save_model_pool_status(kb_dir, status)
    commit_kb_paths(kb_dir, f"Delete model pool profile {profile_id}", (".openkb/config.yaml",))
    return {"config": get_config_data(kb_dir), "model_pool": get_model_pool_data(kb_dir)}


def _ensure_profile_key_from_legacy(kb_dir: Path, profile: dict[str, str]) -> None:
    env_values = _read_env_values(kb_dir)
    env_key = profile.get("api_key_env") or _profile_env_key(profile["id"])
    legacy_key = env_values.get("LLM_API_KEY", "").strip()
    if legacy_key and not env_values.get(env_key):
        _write_env_values(kb_dir, {env_key: legacy_key})


def _write_profile_api_key(kb_dir: Path, profile: dict[str, str], api_key: str) -> None:
    env_key = profile.get("api_key_env") or _profile_env_key(profile["id"])
    _write_env_values(kb_dir, {env_key: api_key, "LLM_API_KEY": api_key})


def _sync_legacy_key_from_profile(kb_dir: Path, profile: dict[str, str]) -> None:
    env_values = _read_env_values(kb_dir)
    env_key = profile.get("api_key_env") or _profile_env_key(profile["id"])
    profile_key = env_values.get(env_key, "").strip()
    if profile_key:
        _write_env_values(kb_dir, {"LLM_API_KEY": profile_key})


def get_config_data(kb_dir: Path) -> dict[str, Any]:
    """Return client configuration data including stored LLM profile keys."""
    kb_dir = require_kb_dir(kb_dir)
    config = load_config(kb_dir / ".openkb" / "config.yaml")
    profiles, active_id = _normalize_profiles(config)
    active_profile = _find_profile(profiles, active_id) or profiles[0]
    env_values = _read_env_values(kb_dir)
    runtime_fields = _pageindex_local_runtime_fields(kb_dir)
    ingest_gate = config.get("ingest_gate") if isinstance(config.get("ingest_gate"), dict) else {}
    return {
        "model": active_profile.get("model", config.get("model", DEFAULT_CONFIG["model"])),
        "language": config.get("language", DEFAULT_CONFIG["language"]),
        "pageindex_threshold": config.get("pageindex_threshold", DEFAULT_CONFIG["pageindex_threshold"]),
        "compile_max_concurrency": int(config.get("compile_max_concurrency", DEFAULT_CONFIG["compile_max_concurrency"])),
        "ingest_gate_enabled": bool(ingest_gate.get("enabled", DEFAULT_CONFIG["ingest_gate"]["enabled"])),
        "ingest_gate_pass_threshold": int(ingest_gate.get("pass_threshold", DEFAULT_CONFIG["ingest_gate"]["pass_threshold"])),
        "ingest_gate_hold_threshold": int(ingest_gate.get("hold_threshold", DEFAULT_CONFIG["ingest_gate"]["hold_threshold"])),
        "ingest_gate_hard_reject_enabled": bool(ingest_gate.get("hard_reject_enabled", DEFAULT_CONFIG["ingest_gate"]["hard_reject_enabled"])),
        "ingest_gate_log_all_decisions": bool(ingest_gate.get("log_all_decisions", DEFAULT_CONFIG["ingest_gate"]["log_all_decisions"])),
        "ingest_gate_allow_force_pass": bool(ingest_gate.get("allow_force_pass", DEFAULT_CONFIG["ingest_gate"]["allow_force_pass"])),
        "ingest_gate_allow_force_reject": bool(ingest_gate.get("allow_force_reject", DEFAULT_CONFIG["ingest_gate"]["allow_force_reject"])),
        "ocr_enabled": bool(config.get("ocr_enabled", DEFAULT_CONFIG["ocr_enabled"])),
        "ocr_detection_mode": str(config.get("ocr_detection_mode", DEFAULT_CONFIG["ocr_detection_mode"])),
        "ocr_default_model": str(config.get("ocr_default_model", DEFAULT_CONFIG["ocr_default_model"])),
        "ocr_chunk_pages": int(config.get("ocr_chunk_pages", DEFAULT_CONFIG["ocr_chunk_pages"])),
        "ocr_auto_recommend": bool(config.get("ocr_auto_recommend", DEFAULT_CONFIG["ocr_auto_recommend"])),
        "paddleocr_token": _paddleocr_token(kb_dir, env_values),
        "pageindex_local_enabled": bool(config.get("pageindex_local_enabled", DEFAULT_CONFIG["pageindex_local_enabled"])),
        "pageindex_local_model": str(config.get("pageindex_local_model", DEFAULT_CONFIG["pageindex_local_model"])),
        "pageindex_local_installation_state": str(
            config.get(
                "pageindex_local_installation_state",
                DEFAULT_CONFIG["pageindex_local_installation_state"],
            )
        ),
        **runtime_fields,
        "wire_api": active_profile.get("wire_api", config.get("wire_api", DEFAULT_CONFIG["wire_api"])),
        "base_url": active_profile.get("base_url", config.get("base_url", DEFAULT_CONFIG.get("base_url", ""))) or "",
        "api_key": _profile_api_key(kb_dir, active_profile, active_id, env_values),
        "api_key_configured": _profile_has_api_key(kb_dir, active_profile, active_id, env_values),
        "active_profile": active_id,
        "profiles": _public_profiles(kb_dir, profiles, active_id),
    }


def get_ingest_gate_data(kb_dir: Path, *, limit: int = 250) -> dict[str, Any]:
    """Return ingest gate configuration plus recent scoring decisions."""
    kb_dir = require_kb_dir(kb_dir)
    config = load_config(kb_dir / ".openkb" / "config.yaml")
    ingest_gate = config.get("ingest_gate") if isinstance(config.get("ingest_gate"), dict) else {}
    language = str(config.get("language", DEFAULT_CONFIG["language"]) or DEFAULT_CONFIG["language"])
    history_path = kb_dir / ".openkb" / "ingest_gate_history.jsonl"
    all_decisions: list[dict[str, Any]] = []

    if history_path.exists():
        for line_number, line in enumerate(history_path.read_text(encoding="utf-8").splitlines(), 1):
            raw = line.strip()
            if not raw:
                continue
            try:
                decision = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(decision, dict):
                continue
            item = dict(decision)
            item["id"] = str(item.get("id") or line_number)
            item["line_number"] = line_number
            all_decisions.append(item)

    newest_first = list(reversed(all_decisions))
    limited = newest_first[: max(int(limit), 1)]
    scores = [
        int(item["total_score"])
        for item in all_decisions
        if isinstance(item.get("total_score"), int)
    ]
    summary = {
        "total": len(all_decisions),
        "pass": 0,
        "hold": 0,
        "reject": 0,
        "force_pass": 0,
        "force_reject": 0,
        "average_score": round(sum(scores) / len(scores), 1) if scores else None,
        "latest_at": newest_first[0].get("timestamp") if newest_first else "",
    }
    for item in all_decisions:
        decision = str(item.get("final_decision") or item.get("raw_decision") or "").lower()
        if decision == "pass":
            summary["pass"] += 1
        elif decision == "hold":
            summary["hold"] += 1
        elif decision == "reject":
            summary["reject"] += 1
        elif decision == "force_pass":
            summary["force_pass"] += 1
        elif decision == "force_reject":
            summary["force_reject"] += 1

    log_page = "explorations/资料准入评分台账.md" if language.lower().startswith("zh") else "explorations/ingest_gate.md"
    return {
        "config": {
            "enabled": bool(ingest_gate.get("enabled", DEFAULT_CONFIG["ingest_gate"]["enabled"])),
            "pass_threshold": int(ingest_gate.get("pass_threshold", DEFAULT_CONFIG["ingest_gate"]["pass_threshold"])),
            "hold_threshold": int(ingest_gate.get("hold_threshold", DEFAULT_CONFIG["ingest_gate"]["hold_threshold"])),
            "hard_reject_enabled": bool(
                ingest_gate.get(
                    "hard_reject_enabled",
                    DEFAULT_CONFIG["ingest_gate"]["hard_reject_enabled"],
                )
            ),
            "log_all_decisions": bool(
                ingest_gate.get(
                    "log_all_decisions",
                    DEFAULT_CONFIG["ingest_gate"]["log_all_decisions"],
                )
            ),
            "allow_force_pass": bool(
                ingest_gate.get(
                    "allow_force_pass",
                    DEFAULT_CONFIG["ingest_gate"]["allow_force_pass"],
                )
            ),
            "allow_force_reject": bool(
                ingest_gate.get(
                    "allow_force_reject",
                    DEFAULT_CONFIG["ingest_gate"]["allow_force_reject"],
                )
            ),
        },
        "log_page": log_page,
        "history_exists": history_path.exists(),
        "summary": summary,
        "decisions": limited,
    }


def _model_pool_status_path(kb_dir: Path) -> Path:
    return kb_dir / ".openkb" / "model-pool" / "status.json"


def _iso_to_epoch(value: str) -> float | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _status_is_due(status: dict[str, Any], interval_seconds: int, *, now: float) -> bool:
    last_checked = _iso_to_epoch(str(status.get("last_checked_at") or ""))
    if last_checked is None:
        return True
    return last_checked + max(int(interval_seconds), 1) <= now


def _load_model_pool_status(kb_dir: Path) -> dict[str, Any]:
    path = _model_pool_status_path(kb_dir)
    if not path.exists():
        return {"profiles": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return {"profiles": {}}
    if not isinstance(data, dict):
        return {"profiles": {}}
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        data["profiles"] = {}
    return data


def _save_model_pool_status(kb_dir: Path, status: dict[str, Any]) -> None:
    path = _model_pool_status_path(kb_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")


def _derive_profile_status_from_routes(
    profile: dict[str, Any],
    profile_status: dict[str, Any] | None,
    route_statuses: list[dict[str, Any]],
) -> dict[str, Any]:
    derived = dict(profile_status or {})
    if profile.get("enabled", True) is False or not route_statuses:
        return derived

    available_models: list[str] = []
    failed_models: dict[str, str] = {}
    latencies: list[int] = []
    last_checked_values: list[tuple[float, str]] = []
    consecutive_failures = 0
    failure_count = 0

    for route_status in route_statuses:
        model = str(route_status.get("model") or "").strip()
        health = str(route_status.get("health") or "unknown")
        checked_at = str(route_status.get("last_checked_at") or "")
        checked_epoch = _iso_to_epoch(checked_at)
        if checked_epoch is not None:
            last_checked_values.append((checked_epoch, checked_at))
        if health == "healthy":
            if model:
                available_models.append(model)
            latency = route_status.get("latency_ms")
            if isinstance(latency, int):
                latencies.append(latency)
        if health in {"offline", "degraded"}:
            route_failures = int(route_status.get("consecutive_failures") or 0)
            route_failure_count = int(route_status.get("failure_count") or 0)
            consecutive_failures = max(consecutive_failures, route_failures)
            failure_count += route_failure_count
            if route_failures > 0 or route_failure_count > 0 or route_status.get("last_error"):
                failed_models[model] = str(route_status.get("last_error") or "route unavailable")

    if not available_models and not failed_models:
        return derived

    if available_models and failed_models:
        health = "degraded"
    elif failed_models:
        health = "offline"
    else:
        health = "healthy"

    derived["health"] = health
    derived["available_models"] = available_models
    derived["failed_models"] = failed_models
    derived["consecutive_failures"] = 0 if available_models else consecutive_failures
    derived["last_error"] = "; ".join(f"{model}: {error}" for model, error in failed_models.items())
    if latencies:
        derived["latency_ms"] = min(latencies)
    elif failed_models:
        derived["latency_ms"] = None
    if last_checked_values:
        derived["last_checked_at"] = max(last_checked_values, key=lambda item: item[0])[1]
    if failure_count and not derived.get("probe_source"):
        derived["probe_source"] = "runtime"
    return derived


def _profile_model_pool_payload(
    kb_dir: Path,
    profile: dict[str, Any],
    active_id: str,
    status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile_status = dict(status or {})
    health = str(profile_status.get("health") or ("unknown" if profile.get("enabled", True) else "disabled"))
    return {
        "id": profile["id"],
        "name": profile["name"],
        "model": profile["model"],
        "wire_api": profile["wire_api"],
        "base_url": profile["base_url"],
        "provider": str(profile.get("provider") or "generic"),
        "reasoning_effort": str(profile.get("reasoning_effort") or ""),
        "thinking_enabled": bool(profile.get("thinking_enabled", False)),
        "enabled": bool(profile.get("enabled", True)),
        "tags": list(profile.get("tags") or []),
        "features": list(profile.get("features") or []),
        "probe_models": list(profile.get("probe_models") or [profile["model"]]),
        "priority": int(profile.get("priority") or 50),
        "api_key": _profile_api_key(kb_dir, profile, active_id),
        "api_key_configured": _profile_has_api_key(kb_dir, profile, active_id),
        "is_active": profile["id"] == active_id,
        "health": health,
        "probing": bool(profile_status.get("probing", False)),
        "probe_source": str(profile_status.get("probe_source") or ""),
        "last_probe_started_at": str(profile_status.get("last_probe_started_at") or ""),
        "last_checked_at": str(profile_status.get("last_checked_at") or ""),
        "latency_ms": profile_status.get("latency_ms"),
        "consecutive_failures": int(profile_status.get("consecutive_failures") or 0),
        "available_models": list(profile_status.get("available_models") or []),
        "failed_models": dict(profile_status.get("failed_models") or {}),
        "last_error": str(profile_status.get("last_error") or ""),
    }


def _model_pool_summary(profiles: list[dict[str, Any]]) -> dict[str, int]:
    summary = {
        "total": len(profiles),
        "healthy": 0,
        "degraded": 0,
        "offline": 0,
        "disabled": 0,
        "unknown": 0,
    }
    for profile in profiles:
        health = str(profile.get("health") or "unknown")
        if health in summary:
            summary[health] += 1
        else:
            summary["unknown"] += 1
    return summary


def get_model_pool_data(kb_dir: Path) -> dict[str, Any]:
    from openkb.model_pool import configured_routes

    kb_dir = require_kb_dir(kb_dir)
    config = load_config(kb_dir / ".openkb" / "config.yaml")
    profiles, active_id = _normalize_profiles(config)
    status = _load_model_pool_status(kb_dir)
    raw_profile_status = status.get("profiles", {})
    raw_route_status = status.get("routes", {})
    pool_config = config.get("model_pool") if isinstance(config.get("model_pool"), dict) else {}
    routes_by_profile: dict[str, list[dict[str, Any]]] = {}
    route_status_by_profile: dict[str, list[dict[str, Any]]] = {}
    for route in configured_routes(kb_dir):
        route_status = (raw_route_status.get(route.route_id, {}) or {}) if isinstance(raw_route_status, dict) else {}
        route_status_by_profile.setdefault(route.profile_id, []).append(route_status)
        routes_by_profile.setdefault(route.profile_id, []).append(
            {
                "id": route.route_id,
                "profile_id": route.profile_id,
                "model": route.model,
                "weight": route.weight,
                "health": route.health,
                "latency_ms": route.latency_ms,
                "base_url": route.base_url,
                "wire_api": route.wire_api,
            }
        )
    cards = []
    for profile in profiles:
        profile_status = raw_profile_status.get(profile["id"]) if isinstance(raw_profile_status, dict) else None
        derived_status = _derive_profile_status_from_routes(
            profile,
            profile_status if isinstance(profile_status, dict) else None,
            route_status_by_profile.get(profile["id"], []),
        )
        cards.append(_profile_model_pool_payload(kb_dir, profile, active_id, derived_status))
    for card in cards:
        card["routes"] = routes_by_profile.get(card["id"], [])
    return {
        "enabled": bool(pool_config.get("enabled", False)),
        "strategy": str(pool_config.get("strategy") or "weighted_round_robin"),
        "probe_interval_seconds": int(pool_config.get("probe_interval_seconds") or 600),
        "failure_threshold": int(pool_config.get("failure_threshold") or 3),
        "timeout_seconds": int(pool_config.get("timeout_seconds") or 12),
        "active_profile": active_id,
        "summary": _model_pool_summary(cards),
        "profiles": cards,
    }


def get_model_pool_profile(kb_dir: Path, profile_id: str) -> dict[str, Any]:
    from openkb.model_pool import configured_routes

    kb_dir = require_kb_dir(kb_dir)
    config = load_config(kb_dir / ".openkb" / "config.yaml")
    profiles, active_id = _normalize_profiles(config)
    profile = _find_profile(profiles, str(profile_id).strip())
    if profile is None:
        raise ClientError(f"Unknown LLM profile: {profile_id}")
    status = _load_model_pool_status(kb_dir)
    raw_profile_status = status.get("profiles", {})
    raw_route_status = status.get("routes", {})
    profile_status = raw_profile_status.get(profile["id"]) if isinstance(raw_profile_status, dict) else None
    route_statuses = []
    for route in configured_routes(kb_dir):
        if route.profile_id != profile["id"]:
            continue
        route_statuses.append((raw_route_status.get(route.route_id, {}) or {}) if isinstance(raw_route_status, dict) else {})
    payload = _profile_model_pool_payload(
        kb_dir,
        profile,
        active_id,
        _derive_profile_status_from_routes(
            profile,
            profile_status if isinstance(profile_status, dict) else None,
            route_statuses,
        ),
    )
    payload["routes"] = [
        {
            "id": route.route_id,
            "profile_id": route.profile_id,
            "model": route.model,
            "weight": route.weight,
            "health": route.health,
            "latency_ms": route.latency_ms,
            "base_url": route.base_url,
            "wire_api": route.wire_api,
            "last_error": (_load_model_pool_status(kb_dir).get("routes", {}).get(route.route_id, {}) or {}).get("last_error", ""),
        }
        for route in configured_routes(kb_dir)
        if route.profile_id == profile["id"]
    ]
    return payload


def save_model_pool_profile_status(kb_dir: Path, profile_id: str, status_update: dict[str, Any]) -> dict[str, Any]:
    kb_dir = require_kb_dir(kb_dir)
    status = _load_model_pool_status(kb_dir)
    profiles = status.setdefault("profiles", {})
    current = dict(profiles.get(profile_id) or {})
    current.update(status_update)
    profiles[profile_id] = current
    _save_model_pool_status(kb_dir, status)
    return get_model_pool_profile(kb_dir, profile_id)


def due_model_pool_probe_profile_ids(kb_dir: Path, *, now: float | None = None) -> list[str]:
    from openkb.model_pool import configured_routes

    kb_dir = require_kb_dir(kb_dir)
    config = load_config(kb_dir / ".openkb" / "config.yaml")
    pool_config = config.get("model_pool") if isinstance(config.get("model_pool"), dict) else {}
    if not pool_config.get("enabled", False):
        return []
    profiles, _active_id = _normalize_profiles(config)
    enabled_profile_ids = {profile["id"] for profile in profiles if profile.get("enabled", True)}
    if not enabled_profile_ids:
        return []

    status = _load_model_pool_status(kb_dir)
    raw_profiles = status.get("profiles", {})
    raw_routes = status.get("routes", {})
    if not isinstance(raw_profiles, dict):
        raw_profiles = {}
    if not isinstance(raw_routes, dict):
        raw_routes = {}

    interval_seconds = int(pool_config.get("probe_interval_seconds") or DEFAULT_CONFIG["model_pool"]["probe_interval_seconds"])
    timestamp = time.time() if now is None else float(now)
    due_ids: set[str] = set()
    for route in configured_routes(kb_dir):
        if route.profile_id not in enabled_profile_ids:
            continue
        route_status = raw_routes.get(route.route_id, {}) or {}
        if str(route_status.get("health") or "") not in {"offline", "degraded"}:
            continue
        if int(route_status.get("consecutive_failures") or 0) <= 0 and int(route_status.get("failure_count") or 0) <= 0:
            continue
        profile_status = raw_profiles.get(route.profile_id, {}) or {}
        if profile_status.get("probing"):
            continue
        if not _status_is_due(route_status, interval_seconds, now=timestamp):
            continue
        due_ids.add(route.profile_id)
    return sorted(due_ids)


def list_ocr_cache_entries(kb_dir: Path) -> dict[str, Any]:
    """Return non-secret OCR cache metadata for the KB."""
    kb_dir = require_kb_dir(kb_dir)
    cache_root = kb_dir / ".openkb" / "ocr" / "cache"
    entries: list[dict[str, Any]] = []
    if cache_root.exists():
        for entry_dir in sorted(path for path in cache_root.iterdir() if path.is_dir()):
            manifest_path = entry_dir / "manifest.json"
            manifest: dict[str, Any] = {}
            if manifest_path.exists():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8") or "{}")
                except json.JSONDecodeError:
                    manifest = {"status": "invalid_manifest"}
            file_hash = str(manifest.get("file_hash") or entry_dir.name)
            entries.append(
                {
                    "file_hash": file_hash,
                    "status": str(manifest.get("status") or "unknown"),
                    "doc_name": str(manifest.get("doc_name") or ""),
                    "page_count": int(manifest.get("page_count") or 0),
                    "ocr_model": str(manifest.get("ocr_model") or ""),
                    "has_pages": (entry_dir / "normalized" / "pages.json").exists(),
                    "has_pageindex_input": (entry_dir / "normalized" / "pageindex_input.md").exists(),
                }
            )
    return {"entries": entries}


def invalidate_ocr_cache_entry(kb_dir: Path, file_hash: str) -> dict[str, Any]:
    """Mark an OCR cache entry invalidated without deleting artifacts."""
    kb_dir = require_kb_dir(kb_dir)
    file_hash = str(file_hash).strip()
    if not file_hash:
        raise ClientError("OCR cache hash is required.")
    manifest_path = kb_dir / ".openkb" / "ocr" / "cache" / file_hash / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"OCR cache entry not found: {file_hash}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8") or "{}")
    manifest["file_hash"] = str(manifest.get("file_hash") or file_hash)
    manifest["status"] = "invalidated"
    manifest["invalidated_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"entry": _ocr_cache_entry_payload(kb_dir, file_hash, manifest)}


def _ocr_cache_entry_payload(kb_dir: Path, file_hash: str, manifest: dict[str, Any]) -> dict[str, Any]:
    entry_dir = kb_dir / ".openkb" / "ocr" / "cache" / file_hash
    payload = {
        "file_hash": str(manifest.get("file_hash") or file_hash),
        "status": str(manifest.get("status") or "unknown"),
        "doc_name": str(manifest.get("doc_name") or ""),
        "page_count": int(manifest.get("page_count") or 0),
        "ocr_model": str(manifest.get("ocr_model") or ""),
        "has_pages": (entry_dir / "normalized" / "pages.json").exists(),
        "has_pageindex_input": (entry_dir / "normalized" / "pageindex_input.md").exists(),
    }
    if manifest.get("invalidated_at"):
        payload["invalidated_at"] = str(manifest["invalidated_at"])
    return payload


def source_path_for_ocr_cache_entry(kb_dir: Path, file_hash: str) -> Path:
    """Return the raw source path for a cached OCR entry."""
    kb_dir = require_kb_dir(kb_dir)
    file_hash = str(file_hash).strip()
    hashes = _read_hashes(kb_dir)
    meta = hashes.get(file_hash)
    if not meta:
        raise FileNotFoundError(f"Source document not found for OCR cache entry: {file_hash}")
    raw_rel = normalize_kb_relative_path(meta.get("raw_path")) or formal_raw_relative_path(str(meta.get("name") or ""))
    raw_path = kb_dir / raw_rel
    if not raw_path.exists():
        raise FileNotFoundError(f"Raw source file not found: {raw_path}")
    return raw_path


def get_pageindex_local_status(kb_dir: Path) -> dict[str, Any]:
    """Return local PageIndex configuration and runtime readiness."""
    kb_dir = require_kb_dir(kb_dir)
    from openkb.pageindex_local.runtime import read_pageindex_local_manifest, runtime_is_ready

    config = load_config(kb_dir / ".openkb" / "config.yaml")
    root = kb_dir / ".openkb" / "pageindex-local"
    manifest = read_pageindex_local_manifest(root)
    return {
        "enabled": bool(config.get("pageindex_local_enabled", DEFAULT_CONFIG["pageindex_local_enabled"])),
        "ready": runtime_is_ready(root),
        "installation_state": str(
            config.get(
                "pageindex_local_installation_state",
                DEFAULT_CONFIG["pageindex_local_installation_state"],
            )
        ),
        "root": str(root),
        "manifest": manifest,
    }


def get_llm_usage_data(
    kb_dir: Path,
    *,
    q: str = "",
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    """Return paginated LLM usage rows without payload blobs."""
    kb_dir = require_kb_dir(kb_dir)
    from openkb.llm_usage import list_usage

    return list_usage(kb_dir, q=q, page=page, page_size=page_size)


def export_llm_usage_data(kb_dir: Path, *, q: str = "") -> dict[str, Any]:
    """Return exportable LLM usage rows including payload blobs."""
    kb_dir = require_kb_dir(kb_dir)
    from openkb.llm_usage import export_usage

    items = export_usage(kb_dir, q=q)
    return {"items": items, "total": len(items)}


def export_config_data(kb_dir: Path) -> dict[str, Any]:
    """Export portable LLM profile settings, including stored API keys."""
    kb_dir = require_kb_dir(kb_dir)
    config = load_config(kb_dir / ".openkb" / "config.yaml")
    profiles, active_id = _normalize_profiles(config)
    env_values = _read_env_values(kb_dir)
    runtime_fields = _pageindex_local_runtime_fields(kb_dir, include_version=True)
    ingest_gate = config.get("ingest_gate") if isinstance(config.get("ingest_gate"), dict) else {}
    return {
        "format": _CONFIG_EXPORT_FORMAT,
        "active_profile": active_id,
        "settings": {
            "language": config.get("language", DEFAULT_CONFIG["language"]),
            "pageindex_threshold": int(config.get("pageindex_threshold", DEFAULT_CONFIG["pageindex_threshold"])),
            "compile_max_concurrency": int(config.get("compile_max_concurrency", DEFAULT_CONFIG["compile_max_concurrency"])),
            "ingest_gate_enabled": bool(ingest_gate.get("enabled", DEFAULT_CONFIG["ingest_gate"]["enabled"])),
            "ingest_gate_pass_threshold": int(ingest_gate.get("pass_threshold", DEFAULT_CONFIG["ingest_gate"]["pass_threshold"])),
            "ingest_gate_hold_threshold": int(ingest_gate.get("hold_threshold", DEFAULT_CONFIG["ingest_gate"]["hold_threshold"])),
            "ingest_gate_hard_reject_enabled": bool(ingest_gate.get("hard_reject_enabled", DEFAULT_CONFIG["ingest_gate"]["hard_reject_enabled"])),
            "ingest_gate_log_all_decisions": bool(ingest_gate.get("log_all_decisions", DEFAULT_CONFIG["ingest_gate"]["log_all_decisions"])),
            "ingest_gate_allow_force_pass": bool(ingest_gate.get("allow_force_pass", DEFAULT_CONFIG["ingest_gate"]["allow_force_pass"])),
            "ingest_gate_allow_force_reject": bool(ingest_gate.get("allow_force_reject", DEFAULT_CONFIG["ingest_gate"]["allow_force_reject"])),
            "ocr_enabled": bool(config.get("ocr_enabled", DEFAULT_CONFIG["ocr_enabled"])),
            "ocr_detection_mode": str(config.get("ocr_detection_mode", DEFAULT_CONFIG["ocr_detection_mode"])),
            "ocr_default_model": str(config.get("ocr_default_model", DEFAULT_CONFIG["ocr_default_model"])),
            "ocr_chunk_pages": int(config.get("ocr_chunk_pages", DEFAULT_CONFIG["ocr_chunk_pages"])),
            "ocr_auto_recommend": bool(config.get("ocr_auto_recommend", DEFAULT_CONFIG["ocr_auto_recommend"])),
            "paddleocr_token": _paddleocr_token(kb_dir, env_values),
            "pageindex_local_enabled": bool(
                config.get("pageindex_local_enabled", DEFAULT_CONFIG["pageindex_local_enabled"])
            ),
            "pageindex_local_model": str(
                config.get("pageindex_local_model", DEFAULT_CONFIG["pageindex_local_model"])
            ),
            "pageindex_local_installation_state": str(
                config.get(
                    "pageindex_local_installation_state",
                    DEFAULT_CONFIG["pageindex_local_installation_state"],
                )
            ),
            "model_pool_enabled": bool(model_pool_config(config).get("enabled", DEFAULT_CONFIG["model_pool"]["enabled"])),
            "model_pool_strategy": str(model_pool_config(config).get("strategy", DEFAULT_CONFIG["model_pool"]["strategy"])),
            "model_pool_probe_interval_seconds": int(
                model_pool_config(config).get(
                    "probe_interval_seconds",
                    DEFAULT_CONFIG["model_pool"]["probe_interval_seconds"],
                )
            ),
            "model_pool_failure_threshold": int(
                model_pool_config(config).get(
                    "failure_threshold",
                    DEFAULT_CONFIG["model_pool"]["failure_threshold"],
                )
            ),
            "model_pool_timeout_seconds": int(
                model_pool_config(config).get(
                    "timeout_seconds",
                    DEFAULT_CONFIG["model_pool"]["timeout_seconds"],
                )
            ),
            **runtime_fields,
        },
        "profiles": [
            {
                "id": profile["id"],
                "name": profile["name"],
                "model": profile["model"],
                "wire_api": profile["wire_api"],
                "base_url": profile["base_url"],
                "provider": str(profile.get("provider") or "generic"),
                "reasoning_effort": str(profile.get("reasoning_effort") or ""),
                "thinking_enabled": bool(profile.get("thinking_enabled", False)),
                "enabled": bool(profile.get("enabled", True)),
                "tags": list(profile.get("tags") or []),
                "features": list(profile.get("features") or []),
                "probe_models": list(profile.get("probe_models") or [profile["model"]]),
                "models": list(profile.get("models") or [{"name": profile["model"], "weight": 100}]),
                "priority": int(profile.get("priority") or 50),
                "api_key": _profile_api_key(kb_dir, profile, active_id, env_values),
            }
            for profile in profiles
        ],
    }


def import_config_data(kb_dir: Path, imported: dict[str, Any]) -> dict[str, Any]:
    """Import LLM profile settings and stored API keys."""
    kb_dir = require_kb_dir(kb_dir)
    if not isinstance(imported, dict):
        raise ClientError("Imported config must be a JSON object.")

    raw_profiles = imported.get("profiles")
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise ClientError("Imported config must include at least one profile.")

    config_path = kb_dir / ".openkb" / "config.yaml"
    config = load_config(config_path)
    existing_profiles, _existing_active = _normalize_profiles(config)
    existing_by_id = {profile["id"]: profile for profile in existing_profiles}

    profiles: list[dict[str, str]] = []
    imported_api_keys: dict[str, str] = {}
    seen: set[str] = set()
    for index, raw in enumerate(raw_profiles, 1):
        if not isinstance(raw, dict):
            continue
        profile = _normalize_profile(raw, str(raw.get("id") or f"profile-{index}"), config)
        if profile["id"] in seen:
            continue
        existing = existing_by_id.get(profile["id"])
        profile["api_key_env"] = (
            existing.get("api_key_env")
            if existing
            else _profile_env_key(profile["id"])
        )
        profiles.append(profile)
        api_key = str(raw.get("api_key") or "").strip()
        if api_key:
            imported_api_keys[profile["id"]] = api_key
        seen.add(profile["id"])

    if not profiles:
        raise ClientError("Imported config does not contain any valid profiles.")

    settings = imported.get("settings") if isinstance(imported.get("settings"), dict) else {}
    if "language" in settings:
        config["language"] = str(settings["language"] or DEFAULT_CONFIG["language"]).strip() or DEFAULT_CONFIG["language"]
    if "pageindex_threshold" in settings:
        config["pageindex_threshold"] = max(int(settings["pageindex_threshold"]), 1)
    if "compile_max_concurrency" in settings:
        config["compile_max_concurrency"] = max(int(settings["compile_max_concurrency"]), 1)
    ingest_gate = config.get("ingest_gate") if isinstance(config.get("ingest_gate"), dict) else dict(DEFAULT_CONFIG["ingest_gate"])
    if "ingest_gate_enabled" in settings:
        ingest_gate["enabled"] = bool(settings["ingest_gate_enabled"])
    if "ingest_gate_pass_threshold" in settings:
        ingest_gate["pass_threshold"] = max(int(settings["ingest_gate_pass_threshold"]), 0)
    if "ingest_gate_hold_threshold" in settings:
        ingest_gate["hold_threshold"] = max(int(settings["ingest_gate_hold_threshold"]), 0)
    if "ingest_gate_hard_reject_enabled" in settings:
        ingest_gate["hard_reject_enabled"] = bool(settings["ingest_gate_hard_reject_enabled"])
    if "ingest_gate_log_all_decisions" in settings:
        ingest_gate["log_all_decisions"] = bool(settings["ingest_gate_log_all_decisions"])
    if "ingest_gate_allow_force_pass" in settings:
        ingest_gate["allow_force_pass"] = bool(settings["ingest_gate_allow_force_pass"])
    if "ingest_gate_allow_force_reject" in settings:
        ingest_gate["allow_force_reject"] = bool(settings["ingest_gate_allow_force_reject"])
    config["ingest_gate"] = ingest_gate
    if "ocr_enabled" in settings:
        config["ocr_enabled"] = bool(settings["ocr_enabled"])
    if "ocr_detection_mode" in settings:
        config["ocr_detection_mode"] = (
            str(settings["ocr_detection_mode"] or DEFAULT_CONFIG["ocr_detection_mode"]).strip()
            or DEFAULT_CONFIG["ocr_detection_mode"]
        )
    if "ocr_default_model" in settings:
        config["ocr_default_model"] = (
            str(settings["ocr_default_model"] or DEFAULT_CONFIG["ocr_default_model"]).strip()
            or DEFAULT_CONFIG["ocr_default_model"]
        )
    if "ocr_chunk_pages" in settings:
        config["ocr_chunk_pages"] = max(int(settings["ocr_chunk_pages"]), 1)
    if "ocr_auto_recommend" in settings:
        config["ocr_auto_recommend"] = bool(settings["ocr_auto_recommend"])
    if "paddleocr_token" in settings:
        _write_env_values(kb_dir, {_PADDLEOCR_TOKEN_ENV: str(settings["paddleocr_token"] or "").strip()})
    if "pageindex_local_enabled" in settings:
        config["pageindex_local_enabled"] = bool(settings["pageindex_local_enabled"])
    if "pageindex_local_model" in settings:
        config["pageindex_local_model"] = str(settings["pageindex_local_model"] or "").strip()
    if "pageindex_local_installation_state" in settings:
        config["pageindex_local_installation_state"] = (
            str(
                settings["pageindex_local_installation_state"]
                or DEFAULT_CONFIG["pageindex_local_installation_state"]
            ).strip()
            or DEFAULT_CONFIG["pageindex_local_installation_state"]
        )
    pool = model_pool_config(config)
    if "model_pool_enabled" in settings:
        pool["enabled"] = bool(settings["model_pool_enabled"])
    if "model_pool_strategy" in settings:
        pool["strategy"] = str(settings["model_pool_strategy"] or DEFAULT_CONFIG["model_pool"]["strategy"]).strip() or DEFAULT_CONFIG["model_pool"]["strategy"]
    if "model_pool_probe_interval_seconds" in settings:
        pool["probe_interval_seconds"] = max(int(settings["model_pool_probe_interval_seconds"]), 1)
    if "model_pool_failure_threshold" in settings:
        pool["failure_threshold"] = max(int(settings["model_pool_failure_threshold"]), 1)
    if "model_pool_timeout_seconds" in settings:
        pool["timeout_seconds"] = max(int(settings["model_pool_timeout_seconds"]), 1)
    config["model_pool"] = pool
    _update_pageindex_local_runtime(kb_dir, settings)

    active_id = str(imported.get("active_profile") or profiles[0]["id"]).strip()
    if active_id not in {profile["id"] for profile in profiles}:
        active_id = profiles[0]["id"]

    _persist_profiles(config, profiles, active_id)
    save_config(config_path, config)
    env_updates = {
        profile["api_key_env"]: imported_api_keys[profile["id"]]
        for profile in profiles
        if profile["id"] in imported_api_keys
    }
    if active_id in imported_api_keys:
        env_updates["LLM_API_KEY"] = imported_api_keys[active_id]
    if env_updates:
        _write_env_values(kb_dir, env_updates)
    else:
        _sync_legacy_key_from_profile(kb_dir, _find_profile(profiles, active_id) or profiles[0])
    commit_kb_changes(kb_dir, "Import knowledge base config")
    return get_config_data(kb_dir)


def update_config_data(kb_dir: Path, updates: dict[str, Any]) -> dict[str, Any]:
    """Persist allowed config fields and return the public config view."""
    kb_dir = require_kb_dir(kb_dir)
    config_path = kb_dir / ".openkb" / "config.yaml"
    config = load_config(config_path)
    profiles, active_id = _normalize_profiles(config)
    profile_by_id = {profile["id"]: profile for profile in profiles}
    create_profile = bool(updates.get("create_profile"))
    requested_active = str(updates.get("active_profile") or "").strip()

    target_profile: dict[str, str]
    if create_profile:
        current_profile = profile_by_id.get(active_id)
        if current_profile:
            _ensure_profile_key_from_legacy(kb_dir, current_profile)
        profile_name = str(updates.get("profile_name") or updates.get("model") or "New Profile").strip()
        profile_id = _unique_profile_id(str(updates.get("profile_id") or profile_name), set(profile_by_id))
        target_profile = {
            "id": profile_id,
            "name": profile_name or profile_id,
            "model": str(updates.get("model") or DEFAULT_CONFIG["model"]).strip(),
            "wire_api": str(updates.get("wire_api") or DEFAULT_CONFIG["wire_api"]).strip().lower(),
            "base_url": str(updates.get("base_url") or "").strip().rstrip("/"),
            "provider": str(updates.get("provider") or "generic").strip().lower(),
            "reasoning_effort": str(updates.get("reasoning_effort") or "").strip().lower(),
            "thinking_enabled": bool(updates.get("thinking_enabled", False)),
            "api_key_env": _profile_env_key(profile_id),
        }
        profiles.append(target_profile)
        active_id = target_profile["id"]
    else:
        target_id = str(updates.get("profile_id") or requested_active or active_id).strip() or active_id
        target_profile = profile_by_id.get(target_id)
        if target_profile is None:
            raise ClientError(f"Unknown LLM profile: {target_id}")
        if requested_active:
            active_id = target_profile["id"]

    if not create_profile and updates.get("profile_name") is not None:
        profile_name = str(updates.get("profile_name") or "").strip()
        if profile_name:
            target_profile["name"] = profile_name

    for key, value in updates.items():
        if key in _PROFILE_CONFIG_KEYS:
            if key == "base_url":
                value = str(value or "").strip().rstrip("/")
            elif key == "wire_api":
                value = str(value or "").strip().lower()
            else:
                value = str(value or "").strip()
            target_profile[key] = value
        elif key in _PROFILE_LIST_KEYS:
            values = _string_list(value)
            if key == "probe_models" and not values:
                values = [str(target_profile.get("model") or DEFAULT_CONFIG["model"])]
            target_profile[key] = values
        elif key in _PROFILE_BOOL_KEYS:
            target_profile[key] = bool(value)
        elif key in _PROFILE_INT_KEYS:
            target_profile[key] = max(int(value or 0), 0)
        elif key in _GENERAL_CONFIG_KEYS:
            if key in {"pageindex_threshold", "compile_max_concurrency", "ocr_chunk_pages"}:
                value = max(int(value), 1)
            elif key in {"ingest_gate_pass_threshold", "ingest_gate_hold_threshold"}:
                value = max(int(value), 0)
            elif key in {"ocr_enabled", "ocr_auto_recommend", "pageindex_local_enabled"}:
                value = bool(value)
            elif key in {
                "ingest_gate_enabled",
                "ingest_gate_hard_reject_enabled",
                "ingest_gate_log_all_decisions",
                "ingest_gate_allow_force_pass",
                "ingest_gate_allow_force_reject",
            }:
                value = bool(value)
            else:
                value = str(value or "").strip() if key not in {"language"} else value
            if key.startswith("ingest_gate_"):
                gate = config.get("ingest_gate") if isinstance(config.get("ingest_gate"), dict) else dict(DEFAULT_CONFIG["ingest_gate"])
                gate_key = key.removeprefix("ingest_gate_")
                gate[gate_key] = value
                config["ingest_gate"] = gate
            else:
                config[key] = value
        elif key in _MODEL_POOL_CONFIG_KEYS:
            pool = model_pool_config(config)
            if key == "model_pool_enabled":
                pool["enabled"] = bool(value)
            elif key == "model_pool_strategy":
                pool["strategy"] = str(value or DEFAULT_CONFIG["model_pool"]["strategy"]).strip() or DEFAULT_CONFIG["model_pool"]["strategy"]
            elif key == "model_pool_probe_interval_seconds":
                pool["probe_interval_seconds"] = max(int(value), 1)
            elif key == "model_pool_failure_threshold":
                pool["failure_threshold"] = max(int(value), 1)
            elif key == "model_pool_timeout_seconds":
                pool["timeout_seconds"] = max(int(value), 1)
            config["model_pool"] = pool

    api_key = str(updates.get("api_key") or "").strip()
    if api_key:
        _write_profile_api_key(kb_dir, target_profile, api_key)
    else:
        _sync_legacy_key_from_profile(kb_dir, target_profile)

    if updates.get("paddleocr_token") is not None:
        _write_env_values(kb_dir, {_PADDLEOCR_TOKEN_ENV: str(updates.get("paddleocr_token") or "").strip()})
    _update_pageindex_local_runtime(kb_dir, updates)

    active_id = _enabled_active_profile_id(profiles, active_id)
    _persist_profiles(config, profiles, active_id)
    save_config(config_path, config)
    commit_kb_paths(kb_dir, "Update knowledge base config", (".openkb/config.yaml",))
    return get_config_data(kb_dir)


def init_kb(
    kb_dir: Path,
    *,
    model: str,
    language: str = "en",
    pageindex_threshold: int = 20,
    compile_max_concurrency: int = 2,
    ingest_gate_enabled: bool | None = None,
    ingest_gate_pass_threshold: int | None = None,
    ingest_gate_hold_threshold: int | None = None,
    ingest_gate_hard_reject_enabled: bool | None = None,
    ingest_gate_log_all_decisions: bool | None = None,
    ingest_gate_allow_force_pass: bool | None = None,
    ingest_gate_allow_force_reject: bool | None = None,
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
    (kb_dir / "wiki" / "concepts").mkdir(parents=True, exist_ok=True)
    (kb_dir / "wiki" / "explorations").mkdir(parents=True, exist_ok=True)
    (kb_dir / "wiki" / "reports").mkdir(parents=True, exist_ok=True)

    (kb_dir / "wiki" / "AGENTS.md").write_text(AGENTS_MD, encoding="utf-8")
    (kb_dir / "wiki" / "index.md").write_text(
        "# Knowledge Base Index\n\n"
        "## Documents\n\n"
        "## Companies\n\n"
        "## Industries\n\n"
        "## Concepts\n\n"
        "## Explorations\n",
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "log.md").write_text("# Operations Log\n\n", encoding="utf-8")

    openkb_dir.mkdir(parents=True)
    resolved_wire_api = wire_api or ("responses" if model_prefers_responses_api(model) else DEFAULT_CONFIG["wire_api"])
    resolved_base_url = base_url.strip().rstrip("/")
    ingest_gate = dict(DEFAULT_CONFIG["ingest_gate"])
    if ingest_gate_enabled is not None:
        ingest_gate["enabled"] = bool(ingest_gate_enabled)
    if ingest_gate_pass_threshold is not None:
        ingest_gate["pass_threshold"] = max(int(ingest_gate_pass_threshold), 0)
    if ingest_gate_hold_threshold is not None:
        ingest_gate["hold_threshold"] = max(int(ingest_gate_hold_threshold), 0)
    if ingest_gate_hard_reject_enabled is not None:
        ingest_gate["hard_reject_enabled"] = bool(ingest_gate_hard_reject_enabled)
    if ingest_gate_log_all_decisions is not None:
        ingest_gate["log_all_decisions"] = bool(ingest_gate_log_all_decisions)
    if ingest_gate_allow_force_pass is not None:
        ingest_gate["allow_force_pass"] = bool(ingest_gate_allow_force_pass)
    if ingest_gate_allow_force_reject is not None:
        ingest_gate["allow_force_reject"] = bool(ingest_gate_allow_force_reject)

    config = {
        "active_llm_profile": _DEFAULT_PROFILE_ID,
        "llm_profiles": [
            {
                "id": _DEFAULT_PROFILE_ID,
                "name": "Default",
                "model": model,
                "wire_api": resolved_wire_api,
                "base_url": resolved_base_url,
                "api_key_env": _profile_env_key(_DEFAULT_PROFILE_ID),
            }
        ],
        "model": model,
        "language": language,
        "pageindex_threshold": int(pageindex_threshold),
        "compile_max_concurrency": max(int(compile_max_concurrency), 1),
        "ingest_gate": ingest_gate,
        "ocr_enabled": DEFAULT_CONFIG["ocr_enabled"],
        "ocr_detection_mode": DEFAULT_CONFIG["ocr_detection_mode"],
        "ocr_default_model": DEFAULT_CONFIG["ocr_default_model"],
        "ocr_chunk_pages": DEFAULT_CONFIG["ocr_chunk_pages"],
        "ocr_auto_recommend": DEFAULT_CONFIG["ocr_auto_recommend"],
        "pageindex_local_enabled": DEFAULT_CONFIG["pageindex_local_enabled"],
        "pageindex_local_model": DEFAULT_CONFIG["pageindex_local_model"],
        "pageindex_local_installation_state": DEFAULT_CONFIG["pageindex_local_installation_state"],
        "model_pool": dict(DEFAULT_CONFIG["model_pool"]),
        "wire_api": resolved_wire_api,
        "base_url": resolved_base_url,
    }
    save_config(openkb_dir / "config.yaml", config)
    (openkb_dir / "hashes.json").write_text("{}", encoding="utf-8")

    if api_key:
        _write_profile_api_key(kb_dir, config["llm_profiles"][0], api_key)

    ensure_kb_git(kb_dir, initial_commit_message="Initialize OpenKB knowledge base")

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
