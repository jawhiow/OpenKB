"""Promotion workflow for approved summaries."""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from openkb.agent.compiler import (
    _SUMMARY_USER,
    _SYSTEM_TEMPLATE,
    _compile_concepts,
    get_compile_max_concurrency,
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


def promote_summary_document(
    kb_dir: Path,
    selector: str,
    *,
    model: str | None = None,
    force: bool = False,
    max_concurrency: int | None = None,
) -> dict[str, Any]:
    """Promote one approved summary into downstream wiki synthesis."""
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

    if workflow_state.get("review_state") != "approved" and not force:
        raise RuntimeError(f"Summary is not approved for promotion: {document['name']}")
    if workflow_state.get("promotion_state") == "promoted" and not force:
        return {"file_hash": file_hash, "name": document["name"], "skipped": True}

    summary_path = kb_dir / "wiki" / "summaries" / f"{stem}.md"
    if not summary_path.exists():
        raise RuntimeError(f"Summary page is missing: summaries/{stem}.md")

    update_document_workflow_state(kb_dir, file_hash, {"promotion_state": "running"})
    try:
        _run_promotion_compile(
            kb_dir,
            document,
            summary_path,
            model=model,
            max_concurrency=max_concurrency,
        )
    except Exception as exc:
        _mark_promotion_failed(kb_dir, file_hash, exc)
        raise

    upsert_document_ledger_record(
        kb_dir,
        file_hash,
        {
            "workflow_state": {"promotion_state": "promoted"},
            "execution": {"last_error": "", "updated_at": _now_iso()},
        },
    )
    return {"file_hash": file_hash, "name": document["name"], "skipped": False}


def promote_summary_documents(
    kb_dir: Path,
    *,
    file_hashes: list[str] | None = None,
    model: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Batch-promote approved summaries."""
    selected_hashes = _selected_promotion_hashes(kb_dir, file_hashes=file_hashes)
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    promoted = 0
    skipped = 0
    for file_hash in selected_hashes:
        try:
            result = promote_summary_document(kb_dir, file_hash, model=model, force=force)
        except Exception as exc:
            failures.append({"file_hash": file_hash, "error": str(exc)})
            continue
        results.append(result)
        if result.get("skipped"):
            skipped += 1
        else:
            promoted += 1
    return {
        "promoted": promoted,
        "skipped": skipped,
        "failed": len(failures),
        "total": len(selected_hashes),
        "failures": failures,
        "documents": results,
    }


def _run_promotion_compile(
    kb_dir: Path,
    document: dict[str, Any],
    summary_path: Path,
    *,
    model: str | None,
    max_concurrency: int | None,
) -> None:
    config = load_config(kb_dir / ".openkb" / "config.yaml")
    selected_model = model or str(config.get("model") or DEFAULT_CONFIG["model"])
    language = str(config.get("language") or DEFAULT_CONFIG["language"])
    wiki_dir = kb_dir / "wiki"
    stem = str(document["stem"])
    source_content = _source_context(kb_dir, document)
    summary = summary_path.read_text(encoding="utf-8")
    system_msg = {
        "role": "system",
        "content": _SYSTEM_TEMPLATE.format(schema_md=get_agents_md(wiki_dir), language=language),
    }
    doc_msg = {
        "role": "user",
        "content": _SUMMARY_USER.format(doc_name=stem, content=source_content),
    }
    asyncio.run(
        _compile_concepts(
            wiki_dir,
            kb_dir,
            selected_model,
            system_msg,
            doc_msg,
            summary,
            stem,
            get_compile_max_concurrency(max_concurrency),
            doc_brief="",
            doc_type=_doc_type_for_document(document),
        )
    )


def _source_context(kb_dir: Path, document: dict[str, Any]) -> str:
    source_rel = str(document.get("source_path") or "").strip()
    if source_rel:
        source_path = kb_dir / "wiki" / source_rel
        if source_path.exists():
            return source_path.read_text(encoding="utf-8")
    return ""


def _selected_promotion_hashes(kb_dir: Path, *, file_hashes: list[str] | None) -> list[str]:
    if file_hashes:
        return [str(file_hash).strip() for file_hash in file_hashes if str(file_hash).strip()]
    return [
        record["file_hash"]
        for record in select_document_ledger_records(
            kb_dir,
            review_state="approved",
            promotion_state=["not_selected", "failed"],
        )
    ]


def _mark_promotion_failed(kb_dir: Path, file_hash: str, error: Exception) -> None:
    existing = get_document_ledger_record(kb_dir, file_hash)
    retry_count = int((existing or {}).get("execution", {}).get("retry_count") or 0) + 1
    upsert_document_ledger_record(
        kb_dir,
        file_hash,
        {
            "workflow_state": {"promotion_state": "failed"},
            "execution": {
                "last_error": str(error),
                "retry_count": retry_count,
                "updated_at": _now_iso(),
            },
        },
    )


def _doc_type_for_document(document: dict[str, Any]) -> str:
    raw_type = str(document.get("type") or "").strip()
    if raw_type == "local_long_pdf":
        return "local-long"
    if raw_type == "long_pdf":
        return "pageindex"
    return "short"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
