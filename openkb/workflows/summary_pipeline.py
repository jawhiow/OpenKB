"""Summary-review workflow for staged OpenKB documents."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from openkb.agent.compiler import (
    _LOCAL_LONG_DOC_SUMMARY_USER,
    _SUMMARY_USER,
    _SYSTEM_TEMPLATE,
    _build_local_long_doc_context,
    _llm_call,
    _parse_json,
    _write_summary,
)
from openkb.config import DEFAULT_CONFIG, load_config
from openkb.document_ledger import (
    build_document_ledger_record,
    get_document_ledger_record,
    infer_document_ledger_defaults,
    select_document_ledger_records,
    update_document_workflow_state,
    upsert_document_ledger_record,
)
from openkb.schema import get_agents_md
from openkb.source_relations import resolve_source_document


def summarize_document_source(
    kb_dir: Path,
    selector: str,
    *,
    model: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Generate a single-document summary without promoting wiki concepts."""
    kb_dir = Path(kb_dir)
    document = resolve_source_document(kb_dir, selector)
    file_hash = str(document["hash"])
    stem = str(document["stem"])
    ledger_record = get_document_ledger_record(kb_dir, file_hash)
    if ledger_record is None:
        ledger_record = build_document_ledger_record(
            file_hash,
            defaults=infer_document_ledger_defaults(document),
        )
    workflow_state = ledger_record.get("workflow_state", {})
    if workflow_state.get("summary_state") == "ready" and not force:
        return {
            "file_hash": file_hash,
            "name": document["name"],
            "skipped": True,
            "summary_path": f"summaries/{stem}.md",
        }
    if workflow_state.get("source_state") not in {"ready", None, ""} and not force:
        raise RuntimeError(f"Source is not ready for summarization: {document['name']}")

    update_document_workflow_state(kb_dir, file_hash, {"summary_state": "running"})
    try:
        source_rel = str(document.get("source_path") or "").strip()
        if not source_rel:
            raise RuntimeError(f"No source artifact found for {document['name']}")
        source_path = kb_dir / "wiki" / source_rel
        if not source_path.exists():
            raise RuntimeError(f"Source artifact is missing: {source_rel}")
        selected_model = model or _default_model(kb_dir)
        summary = _generate_summary_only(kb_dir, stem, source_path, selected_model)
        doc_type = _summary_doc_type(source_path)
        _write_summary(kb_dir / "wiki", stem, summary, doc_type=doc_type)
    except Exception as exc:
        _mark_summary_failed(kb_dir, file_hash, exc)
        raise

    summary_rel = f"summaries/{stem}.md"
    upsert_document_ledger_record(
        kb_dir,
        file_hash,
        {
            "workflow_state": {
                "summary_state": "ready",
                "review_state": "unreviewed",
                "promotion_state": "not_selected",
            },
            "execution": {
                "last_error": "",
                "updated_at": _now_iso(),
            },
        },
    )
    return {
        "file_hash": file_hash,
        "name": document["name"],
        "skipped": False,
        "summary_path": summary_rel,
    }


def summarize_documents(
    kb_dir: Path,
    *,
    file_hashes: list[str] | None = None,
    model: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Batch-generate summaries for selected or source-ready documents."""
    selected_hashes = _selected_summary_hashes(kb_dir, file_hashes=file_hashes)
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    generated = 0
    skipped = 0
    for file_hash in selected_hashes:
        try:
            result = summarize_document_source(kb_dir, file_hash, model=model, force=force)
        except Exception as exc:
            failures.append({"file_hash": file_hash, "error": str(exc)})
            continue
        results.append(result)
        if result.get("skipped"):
            skipped += 1
        else:
            generated += 1
    return {
        "generated": generated,
        "skipped": skipped,
        "failed": len(failures),
        "total": len(selected_hashes),
        "failures": failures,
        "documents": results,
    }


def update_summary_review(
    kb_dir: Path,
    file_hash: str,
    *,
    review_state: str,
    summary_score: int | None = None,
    review_notes: str = "",
    approved_by: str = "",
) -> dict[str, Any]:
    """Update review metadata for one generated summary."""
    updates: dict[str, Any] = {
        "workflow_state": {"review_state": review_state},
        "review": {"review_notes": review_notes},
        "execution": {"updated_at": _now_iso()},
    }
    if summary_score is not None:
        updates["review"]["summary_score"] = summary_score
    if approved_by:
        updates["review"]["approved_by"] = approved_by
    if review_state == "approved":
        updates["review"]["approved_at"] = _now_iso()
    return upsert_document_ledger_record(kb_dir, file_hash, updates)


def update_summary_reviews(
    kb_dir: Path,
    reviews: list[dict[str, Any]],
) -> dict[str, Any]:
    """Batch-update summary review metadata."""
    updated: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for review in reviews:
        file_hash = str(review.get("file_hash") or "").strip()
        if not file_hash:
            failures.append({"file_hash": "", "error": "file_hash is required"})
            continue
        try:
            record = update_summary_review(
                kb_dir,
                file_hash,
                review_state=str(review.get("review_state") or "scored").strip(),
                summary_score=_optional_int(review.get("summary_score")),
                review_notes=str(review.get("review_notes") or "").strip(),
                approved_by=str(review.get("approved_by") or "").strip(),
            )
        except Exception as exc:
            failures.append({"file_hash": file_hash, "error": str(exc)})
            continue
        updated.append(record)
    return {
        "updated": len(updated),
        "failed": len(failures),
        "total": len(reviews),
        "failures": failures,
        "documents": updated,
    }


def _generate_summary_only(kb_dir: Path, doc_name: str, source_path: Path, model: str) -> str:
    wiki_dir = kb_dir / "wiki"
    config = load_config(kb_dir / ".openkb" / "config.yaml")
    language = str(config.get("language") or DEFAULT_CONFIG["language"])
    system_msg = {
        "role": "system",
        "content": _SYSTEM_TEMPLATE.format(schema_md=get_agents_md(wiki_dir), language=language),
    }
    if source_path.suffix.lower() == ".json":
        content = _build_local_long_doc_context(source_path)
        doc_msg = {
            "role": "user",
            "content": _LOCAL_LONG_DOC_SUMMARY_USER.format(doc_name=doc_name, content=content),
        }
    else:
        content = source_path.read_text(encoding="utf-8")
        doc_msg = {
            "role": "user",
            "content": _SUMMARY_USER.format(doc_name=doc_name, content=content),
        }

    raw = _llm_call(model, [system_msg, doc_msg], "summary-only")
    try:
        parsed = _parse_json(raw)
        if isinstance(parsed, dict):
            return str(parsed.get("content") or raw)
    except (json.JSONDecodeError, ValueError):
        pass
    return raw


def _selected_summary_hashes(kb_dir: Path, *, file_hashes: list[str] | None) -> list[str]:
    if file_hashes:
        return [str(file_hash).strip() for file_hash in file_hashes if str(file_hash).strip()]
    return [
        record["file_hash"]
        for record in select_document_ledger_records(
            kb_dir,
            source_state="ready",
            summary_state=["not_started", "failed"],
        )
    ]


def _mark_summary_failed(kb_dir: Path, file_hash: str, error: Exception) -> None:
    existing = get_document_ledger_record(kb_dir, file_hash)
    retry_count = int((existing or {}).get("execution", {}).get("retry_count") or 0) + 1
    upsert_document_ledger_record(
        kb_dir,
        file_hash,
        {
            "workflow_state": {"summary_state": "failed"},
            "execution": {
                "last_error": str(error),
                "retry_count": retry_count,
                "updated_at": _now_iso(),
            },
        },
    )


def _summary_doc_type(source_path: Path) -> str:
    return "pageindex" if source_path.suffix.lower() == ".json" else "short"


def _default_model(kb_dir: Path) -> str:
    config = load_config(kb_dir / ".openkb" / "config.yaml")
    return str(config.get("model") or DEFAULT_CONFIG["model"])


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
