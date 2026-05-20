"""Persistent workflow ledger for staged document ingest.

The existing ``.openkb/hashes.json`` remains the canonical de-dup registry.
This module adds a separate per-document ledger for staged workflow state such
as source readiness, summary review, and promotion decisions.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


DOCUMENT_LEDGER_VERSION = 1

WORKFLOW_STATE_DEFAULTS = {
    "ingest_state": "imported",
    "ocr_state": "not_needed",
    "source_state": "queued",
    "summary_state": "not_started",
    "review_state": "unreviewed",
    "promotion_state": "not_selected",
}

REVIEW_DEFAULTS = {
    "ingest_score": None,
    "summary_score": None,
    "promotion_score": None,
    "summary_score_source": "",
    "summary_scorecard": None,
    "summary_scorecard_v1": None,
    "scorecard_version": "",
    "review_notes": "",
    "recommended_ingest_mode": "",
    "approved_by": "",
    "approved_at": None,
}

EXECUTION_DEFAULTS = {
    "last_error": "",
    "retry_count": 0,
    "updated_at": None,
}

_TOP_LEVEL_DEFAULTS = {
    "name": "",
    "stem": "",
    "raw_path": "",
    "source_path": "",
    "review_summary_path": "",
    "ingested_at": None,
    "source_kind": "",
    "page_count": None,
    "scan_detected": False,
}

_WORKFLOW_KEYS = tuple(WORKFLOW_STATE_DEFAULTS)
_REVIEW_KEYS = tuple(REVIEW_DEFAULTS)
_EXECUTION_KEYS = tuple(EXECUTION_DEFAULTS)
_TOP_LEVEL_KEYS = tuple(_TOP_LEVEL_DEFAULTS)

_PAGE_JSON_SOURCE_KINDS = {"page_json", "pageindex_cloud", "local_long_json"}


def document_ledger_path(kb_dir: Path) -> Path:
    """Return the KB-local document ledger path."""
    return Path(kb_dir) / ".openkb" / "document_ledger.json"


def empty_document_ledger() -> dict[str, Any]:
    """Return an empty normalized ledger payload."""
    return {"version": DOCUMENT_LEDGER_VERSION, "documents": {}}


def load_document_ledger(kb_dir: Path) -> dict[str, Any]:
    """Load and normalize the document ledger."""
    path = document_ledger_path(kb_dir)
    if not path.exists():
        return empty_document_ledger()
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return empty_document_ledger()

    documents_raw = payload.get("documents") if isinstance(payload, Mapping) else {}
    documents: dict[str, dict[str, Any]] = {}
    if isinstance(documents_raw, Mapping):
        for file_hash, record in documents_raw.items():
            normalized_hash = str(file_hash or "").strip()
            if not normalized_hash:
                continue
            documents[normalized_hash] = build_document_ledger_record(normalized_hash, stored=record)
    return {
        "version": DOCUMENT_LEDGER_VERSION,
        "documents": documents,
    }


def save_document_ledger(kb_dir: Path, ledger: Mapping[str, Any]) -> dict[str, Any]:
    """Persist and return a normalized document ledger payload."""
    documents_raw = ledger.get("documents") if isinstance(ledger, Mapping) else {}
    normalized = empty_document_ledger()
    if isinstance(documents_raw, Mapping):
        for file_hash, record in documents_raw.items():
            normalized_hash = str(file_hash or "").strip()
            if not normalized_hash:
                continue
            normalized["documents"][normalized_hash] = build_document_ledger_record(normalized_hash, stored=record)
    path = document_ledger_path(kb_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return normalized


def list_document_ledger_records(kb_dir: Path) -> dict[str, dict[str, Any]]:
    """Return normalized ledger records keyed by file hash."""
    return load_document_ledger(kb_dir)["documents"]


def list_effective_document_ledger_records(kb_dir: Path) -> dict[str, dict[str, Any]]:
    """Return ledger records merged with inferred defaults from indexed docs."""
    from openkb.source_relations import get_source_documents

    stored_records = list_document_ledger_records(kb_dir)
    effective_records: dict[str, dict[str, Any]] = {}

    for document in get_source_documents(kb_dir):
        file_hash = str(document.get("hash") or "").strip()
        if not file_hash:
            continue
        defaults = infer_document_ledger_defaults(document)
        effective_records[file_hash] = build_document_ledger_record(
            file_hash,
            stored=stored_records.get(file_hash),
            defaults=defaults,
        )
        _repair_effective_record_from_defaults(effective_records[file_hash], defaults)

    for file_hash, record in stored_records.items():
        effective_records.setdefault(
            file_hash,
            build_document_ledger_record(file_hash, stored=record),
        )

    return effective_records


def _repair_effective_record_from_defaults(record: dict[str, Any], defaults: Mapping[str, Any]) -> None:
    """Let durable wiki artifacts override stale pre-ledger workflow placeholders."""
    for key in _TOP_LEVEL_KEYS:
        if key == "scan_detected":
            continue
        default_value = defaults.get(key)
        if key == "page_count":
            if record.get(key) is None and default_value is not None:
                record[key] = _normalized_optional_int(default_value)
            continue
        if key == "ingested_at":
            if record.get(key) is None and default_value is not None:
                record[key] = _normalized_optional_text(default_value)
            continue
        if not str(record.get(key) or "").strip() and str(default_value or "").strip():
            record[key] = str(default_value).strip()

    default_workflow = defaults.get("workflow_state")
    if not isinstance(default_workflow, Mapping):
        return
    workflow = record.get("workflow_state")
    if not isinstance(workflow, dict):
        return
    _promote_if_stale(workflow, "source_state", default_workflow, stale_values={"queued"})
    _promote_if_stale(workflow, "summary_state", default_workflow, stale_values={"not_started"})
    _promote_if_stale(workflow, "review_state", default_workflow, stale_values={"unreviewed"})
    _promote_if_stale(workflow, "promotion_state", default_workflow, stale_values={"not_selected"})


def _promote_if_stale(
    workflow: dict[str, Any],
    key: str,
    defaults: Mapping[str, Any],
    *,
    stale_values: set[str],
) -> None:
    default_value = str(defaults.get(key) or "").strip()
    current_value = str(workflow.get(key) or "").strip()
    if default_value and current_value in stale_values and default_value != current_value:
        workflow[key] = default_value


def get_document_ledger_record(
    kb_dir: Path,
    file_hash: str,
    *,
    defaults: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return one normalized ledger record, or None when absent."""
    normalized_hash = str(file_hash or "").strip()
    if not normalized_hash:
        return None
    stored = list_document_ledger_records(kb_dir).get(normalized_hash)
    if stored is None:
        return None
    return build_document_ledger_record(normalized_hash, stored=stored, defaults=defaults)


def upsert_document_ledger_record(
    kb_dir: Path,
    file_hash: str,
    updates: Mapping[str, Any],
    *,
    defaults: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or update one ledger record and persist the result."""
    normalized_hash = str(file_hash or "").strip()
    if not normalized_hash:
        raise ValueError("file_hash is required")
    ledger = load_document_ledger(kb_dir)
    current = ledger["documents"].get(normalized_hash)
    record = build_document_ledger_record(normalized_hash, stored=current, defaults=defaults)
    _overlay_document_record(record, updates)
    ledger["documents"][normalized_hash] = record
    save_document_ledger(kb_dir, ledger)
    return record


def delete_document_ledger_record(kb_dir: Path, file_hash: str) -> dict[str, Any] | None:
    """Remove one ledger record and return the normalized record when present."""
    normalized_hash = str(file_hash or "").strip()
    if not normalized_hash:
        return None
    ledger = load_document_ledger(kb_dir)
    record = ledger["documents"].pop(normalized_hash, None)
    if record is None:
        return None
    save_document_ledger(kb_dir, ledger)
    return build_document_ledger_record(normalized_hash, stored=record)


def update_document_workflow_state(
    kb_dir: Path,
    file_hash: str,
    updates: Mapping[str, Any],
    *,
    defaults: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Update only the workflow-state block for one ledger record."""
    return upsert_document_ledger_record(
        kb_dir,
        file_hash,
        {"workflow_state": dict(updates)},
        defaults=defaults,
    )


def select_document_ledger_records(
    kb_dir: Path,
    *,
    file_hashes: Iterable[str] | None = None,
    ingest_state: str | Iterable[str] | None = None,
    ocr_state: str | Iterable[str] | None = None,
    source_state: str | Iterable[str] | None = None,
    summary_state: str | Iterable[str] | None = None,
    review_state: str | Iterable[str] | None = None,
    promotion_state: str | Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Return normalized ledger records filtered by workflow state."""
    records = list(list_effective_document_ledger_records(kb_dir).values())
    allowed_hashes = {str(item).strip() for item in file_hashes or [] if str(item).strip()}
    if allowed_hashes:
        records = [record for record in records if record["file_hash"] in allowed_hashes]

    for field_name, criterion in (
        ("ingest_state", ingest_state),
        ("ocr_state", ocr_state),
        ("source_state", source_state),
        ("summary_state", summary_state),
        ("review_state", review_state),
        ("promotion_state", promotion_state),
    ):
        allowed = _normalized_allowed_values(criterion)
        if allowed is None:
            continue
        records = [
            record
            for record in records
            if str(record["workflow_state"].get(field_name) or "").strip() in allowed
        ]

    return sorted(records, key=lambda item: (item.get("name") or "", item["file_hash"]))


def backfill_document_ledger(kb_dir: Path) -> dict[str, Any]:
    """Persist the effective ledger view for compatibility with existing KBs."""
    existing_records = list_document_ledger_records(kb_dir)
    effective_records = list_effective_document_ledger_records(kb_dir)

    added = 0
    updated = 0
    unchanged = 0
    for file_hash, record in effective_records.items():
        current = existing_records.get(file_hash)
        if current is None:
            added += 1
        elif current == record:
            unchanged += 1
        else:
            updated += 1

    save_document_ledger(
        kb_dir,
        {
            "version": DOCUMENT_LEDGER_VERSION,
            "documents": effective_records,
        },
    )
    return {
        "added": added,
        "updated": updated,
        "unchanged": unchanged,
        "total": len(effective_records),
    }


def build_document_ledger_record(
    file_hash: str,
    *,
    stored: Mapping[str, Any] | None = None,
    defaults: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one normalized ledger record with defaults and stored values merged."""
    normalized_hash = str(file_hash or "").strip()
    if not normalized_hash:
        raise ValueError("file_hash is required")
    record = {
        "file_hash": normalized_hash,
        **_TOP_LEVEL_DEFAULTS,
        "workflow_state": dict(WORKFLOW_STATE_DEFAULTS),
        "review": dict(REVIEW_DEFAULTS),
        "execution": dict(EXECUTION_DEFAULTS),
    }
    if defaults is not None:
        _overlay_document_record(record, defaults)
    if stored is not None:
        _overlay_document_record(record, stored)
    return record


def infer_document_ledger_defaults(document: Mapping[str, Any]) -> dict[str, Any]:
    """Derive a normalized default ledger view from an indexed document payload."""
    raw_type = str(document.get("type") or "").strip().lower()
    source_path = str(document.get("source_path") or "").strip()
    summary_exists = bool(document.get("summary_exists", False))
    review_summary_path = str(document.get("review_summary_path") or "").strip()
    review_summary_exists = bool(document.get("review_summary_exists", False))
    has_review_summary = summary_exists or review_summary_exists
    related_pages = document.get("related_pages") if isinstance(document.get("related_pages"), Mapping) else {}
    has_generated_pages = any(related_pages.get(group) for group in ("companies", "industries", "concepts"))
    source_kind = _infer_source_kind(raw_type, source_path)
    source_ready = bool(source_path) or summary_exists or source_kind in _PAGE_JSON_SOURCE_KINDS
    review_state = "approved" if summary_exists else "unreviewed"

    return {
        "name": str(document.get("name") or "").strip(),
        "stem": str(document.get("stem") or "").strip(),
        "raw_path": str(document.get("raw_path") or "").strip(),
        "source_path": source_path,
        "review_summary_path": review_summary_path if review_summary_exists else "",
        "ingested_at": _normalized_optional_text(document.get("ingested_at")),
        "source_kind": source_kind,
        "page_count": _normalized_optional_int(document.get("pages")),
        "scan_detected": False,
        "workflow_state": {
            "ingest_state": "imported",
            "ocr_state": "not_needed",
            "source_state": "ready" if source_ready else "queued",
            "summary_state": "ready" if has_review_summary else "not_started",
            "review_state": review_state,
            "promotion_state": "promoted" if has_generated_pages else "not_selected",
        },
    }


def _overlay_document_record(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    for key in _TOP_LEVEL_KEYS:
        if key not in source:
            continue
        if key == "page_count":
            target[key] = _normalized_optional_int(source.get(key))
        elif key == "scan_detected":
            target[key] = bool(source.get(key, False))
        elif key == "ingested_at":
            target[key] = _normalized_optional_text(source.get(key))
        else:
            target[key] = str(source.get(key) or "").strip()

    workflow_state = source.get("workflow_state")
    if isinstance(workflow_state, Mapping):
        for key in _WORKFLOW_KEYS:
            if key in workflow_state:
                value = str(workflow_state.get(key) or "").strip()
                if value:
                    target["workflow_state"][key] = value

    review = source.get("review")
    if isinstance(review, Mapping):
        for key in _REVIEW_KEYS:
            if key not in review:
                continue
            if key.endswith("_score"):
                target["review"][key] = _normalized_optional_int(review.get(key))
            elif key in ("summary_scorecard", "summary_scorecard_v1"):
                target["review"][key] = _normalized_summary_scorecard(review.get(key))
            elif key == "approved_at":
                target["review"][key] = _normalized_optional_text(review.get(key))
            else:
                target["review"][key] = str(review.get(key) or "").strip()

    execution = source.get("execution")
    if isinstance(execution, Mapping):
        for key in _EXECUTION_KEYS:
            if key not in execution:
                continue
            if key == "retry_count":
                target["execution"][key] = _normalized_optional_int(execution.get(key)) or 0
            elif key == "updated_at":
                target["execution"][key] = _normalized_optional_text(execution.get(key))
            else:
                target["execution"][key] = str(execution.get(key) or "").strip()


def _normalized_allowed_values(value: str | Iterable[str] | None) -> set[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return {normalized} if normalized else set()
    return {str(item).strip() for item in value if str(item).strip()}


def _normalized_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalized_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalized_summary_scorecard(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    dimensions_raw = value.get("dimensions") if isinstance(value.get("dimensions"), Mapping) else {}
    dimensions: dict[str, dict[str, Any]] = {}
    for name, raw_dimension in dimensions_raw.items():
        key = str(name or "").strip()
        if not key or not isinstance(raw_dimension, Mapping):
            continue
        dimensions[key] = {
            "label": str(raw_dimension.get("label") or key).strip() or key,
            "score": _normalized_optional_int(raw_dimension.get("score")),
            "max": _normalized_optional_int(raw_dimension.get("max")),
            "reason": str(raw_dimension.get("reason") or "").strip(),
        }

    normalized = {
        "version": str(value.get("version") or "").strip(),
        "method": str(value.get("method") or "").strip(),
        "overall_assessment": str(value.get("overall_assessment") or "").strip(),
        "total_score": _normalized_optional_int(value.get("total_score")),
        "dimensions": dimensions,
    }
    if (
        normalized["version"]
        or normalized["method"]
        or normalized["overall_assessment"]
        or normalized["total_score"] is not None
        or dimensions
    ):
        return normalized
    return None


def _infer_source_kind(raw_type: str, source_path: str) -> str:
    if source_path.endswith(".md"):
        return "markdown"
    if source_path.endswith(".json"):
        return "page_json"
    if raw_type == "local_long_pdf":
        return "local_long_json"
    if raw_type == "long_pdf":
        return "pageindex_cloud"
    if raw_type:
        return "markdown"
    return ""
