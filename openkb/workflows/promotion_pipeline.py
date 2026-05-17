"""Promotion workflow for approved summaries."""
from __future__ import annotations

import asyncio
import json
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from openkb.agent.compiler import (
    _SUMMARY_USER,
    _SYSTEM_TEMPLATE,
    _compile_concepts,
    _write_summary,
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
from openkb.source_relations import (
    formal_raw_full_path,
    formal_raw_relative_path,
    formal_source_full_path,
    formal_source_images_full_dir,
    formal_source_relative_path,
    normalize_kb_relative_path,
    resolve_kb_relative_path,
    resolve_source_artifact_path,
    resolve_source_document,
    source_images_dir_for_source_path,
)
from openkb.state import HashRegistry
from openkb.workflows.summary_pipeline import read_review_summary


PromotionProgressCallback = Callable[[dict[str, Any]], None]
_PROMOTION_WRITE_LOCKS: dict[str, threading.RLock] = {}
_PROMOTION_WRITE_LOCKS_GUARD = threading.Lock()


def _promotion_write_lock(kb_dir: Path) -> threading.RLock:
    key = str(Path(kb_dir).resolve())
    with _PROMOTION_WRITE_LOCKS_GUARD:
        lock = _PROMOTION_WRITE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PROMOTION_WRITE_LOCKS[key] = lock
        return lock


def promote_summary_document(
    kb_dir: Path,
    selector: str,
    *,
    model: str | None = None,
    force: bool = False,
    max_concurrency: int | None = None,
    write_lock: Any | None = None,
) -> dict[str, Any]:
    """Promote one approved summary into downstream wiki synthesis."""
    kb_dir = Path(kb_dir)
    lock = write_lock or _promotion_write_lock(kb_dir)
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
        return {
            "file_hash": file_hash,
            "name": document["name"],
            "skipped": True,
            "skip_reason": "already_promoted",
            "promotion_state": "promoted",
        }

    with lock:
        update_document_workflow_state(kb_dir, file_hash, {"promotion_state": "running"})
    try:
        artifacts = _run_promotion_compile(
            kb_dir,
            document,
            ledger_record=ledger_record,
            model=model,
            max_concurrency=max_concurrency,
            write_lock=lock,
        )
    except Exception as exc:
        with lock:
            _mark_promotion_failed(kb_dir, file_hash, exc)
        raise

    with lock:
        upsert_document_ledger_record(
            kb_dir,
            file_hash,
            {
                "workflow_state": {"promotion_state": "promoted"},
                "execution": {"last_error": "", "updated_at": _now_iso()},
            },
        )
    return {
        "file_hash": file_hash,
        "name": document["name"],
        "skipped": False,
        "promotion_state": "promoted",
        **artifacts,
    }


def promote_summary_documents(
    kb_dir: Path,
    *,
    file_hashes: list[str] | None = None,
    model: str | None = None,
    force: bool = False,
    max_workers: int = 1,
    write_lock: Any | None = None,
    progress_callback: PromotionProgressCallback | None = None,
) -> dict[str, Any]:
    """Batch-promote approved summaries."""
    del max_workers
    selected_hashes = _selected_promotion_hashes(kb_dir, file_hashes=file_hashes)
    lock = write_lock or _promotion_write_lock(Path(kb_dir))
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    promoted = 0
    skipped = 0
    total = len(selected_hashes)
    completed = 0

    def emit(event: str, **payload: Any) -> None:
        if progress_callback is not None:
            progress_callback({"event": event, "completed": completed, "total": total, **payload})

    emit("selected")
    for index, file_hash in enumerate(selected_hashes, 1):
        emit("start", index=index, file_hash=file_hash)
        try:
            result = promote_summary_document(kb_dir, file_hash, model=model, force=force, write_lock=lock)
        except Exception as exc:
            completed += 1
            failure = {"file_hash": file_hash, "name": _promotion_document_name(kb_dir, file_hash), "error": str(exc)}
            failures.append(failure)
            emit("failure", index=index, **failure)
            continue
        completed += 1
        results.append(result)
        if result.get("skipped"):
            skipped += 1
            emit("skipped", index=index, **result)
        else:
            promoted += 1
            emit("promoted", index=index, **result)
    return {
        "promoted": promoted,
        "skipped": skipped,
        "failed": len(failures),
        "total": total,
        "failures": failures,
        "documents": results,
    }


def _run_promotion_compile(
    kb_dir: Path,
    document: dict[str, Any],
    *,
    ledger_record: dict[str, Any] | None,
    model: str | None,
    max_concurrency: int | None,
    write_lock: Any | None,
) -> dict[str, str]:
    config = load_config(kb_dir / ".openkb" / "config.yaml")
    selected_model = model or str(config.get("model") or DEFAULT_CONFIG["model"])
    language = str(config.get("language") or DEFAULT_CONFIG["language"])
    wiki_dir = kb_dir / "wiki"
    stem = str(document["stem"])
    source_content = _source_context(kb_dir, document)
    summary_text, _summary_rel = read_review_summary(
        kb_dir,
        stem=stem,
        ingested_at=document.get("ingested_at"),
        review_summary_path=str((ledger_record or {}).get("review_summary_path") or ""),
    )
    doc_type = _doc_type_for_document(document)
    summary_body = summary_text
    if summary_body.startswith("---"):
        end = summary_body.find("---", 3)
        if end != -1:
            summary_body = summary_body[end + 3:].lstrip("\n")
    lock = write_lock or _promotion_write_lock(kb_dir)
    with lock:
        promotion_artifacts = _promote_source_artifacts(kb_dir, document, ledger_record=ledger_record)
        _write_summary(wiki_dir, stem, summary_body, doc_type=doc_type)
        _rewrite_formal_summary_full_text(
            wiki_dir / "summaries" / f"{stem}.md",
            promotion_artifacts["formal_source_rel"],
        )
        summary = (wiki_dir / "summaries" / f"{stem}.md").read_text(encoding="utf-8")
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
            doc_type=doc_type,
            write_lock=lock,
        )
    )
    with lock:
        _update_promoted_document_metadata(
            kb_dir,
            document,
            ledger_record=ledger_record,
            raw_rel=promotion_artifacts["formal_raw_rel"],
            source_rel=promotion_artifacts["formal_source_rel"],
        )
    return {
        "model": selected_model,
        "doc_type": doc_type,
        "summary_path": f"summaries/{stem}.md",
        "source_path": promotion_artifacts["formal_source_rel"],
        "raw_path": promotion_artifacts["formal_raw_rel"],
    }


def _source_context(kb_dir: Path, document: dict[str, Any]) -> str:
    source_rel = normalize_kb_relative_path(document.get("source_path"))
    if source_rel:
        source_path = resolve_source_artifact_path(kb_dir, source_rel)
        if source_path.exists():
            if source_path.suffix.lower() == ".json":
                try:
                    pages = json.loads(source_path.read_text(encoding="utf-8") or "[]")
                except json.JSONDecodeError:
                    return ""
                if isinstance(pages, list):
                    chunks = [
                        str(item.get("content") or "").strip()
                        for item in pages
                        if isinstance(item, dict) and str(item.get("content") or "").strip()
                    ]
                    return "\n\n".join(chunks)
                return ""
            return source_path.read_text(encoding="utf-8")
    return ""


def _promote_source_artifacts(
    kb_dir: Path,
    document: dict[str, Any],
    *,
    ledger_record: dict[str, Any] | None,
) -> dict[str, str]:
    stem = str(document["stem"])
    name = str(document["name"])
    source_rel = normalize_kb_relative_path(document.get("source_path"))
    raw_rel = normalize_kb_relative_path(
        (ledger_record or {}).get("raw_path") or document.get("raw_path")
    )
    if not source_rel:
        raise RuntimeError(f"No source artifact found for {name}")

    source_path = resolve_source_artifact_path(kb_dir, source_rel)
    suffix = Path(source_rel).suffix or ".md"
    if source_path is None or not source_path.exists():
        if source_rel.startswith(("sources/", "wiki/sources/")):
            return {
                "formal_raw_rel": raw_rel or formal_raw_relative_path(name),
                "formal_source_rel": formal_source_relative_path(stem, suffix),
            }
        raise RuntimeError(f"Source artifact is missing: {source_rel}")

    formal_source_path = formal_source_full_path(kb_dir, stem, suffix)
    formal_source_path.parent.mkdir(parents=True, exist_ok=True)
    if source_path.resolve() != formal_source_path.resolve():
        if formal_source_path.exists():
            formal_source_path.unlink()
        source_path.replace(formal_source_path)
    if formal_source_path.suffix.lower() == ".md":
        _rewrite_formal_source_image_refs(formal_source_path, stem)

    images_rel = source_images_dir_for_source_path(source_rel)
    if images_rel:
        images_path = resolve_kb_relative_path(kb_dir, images_rel)
        if images_path is not None and images_path.exists():
            formal_images_path = formal_source_images_full_dir(kb_dir, stem)
            if formal_images_path.exists():
                shutil.rmtree(formal_images_path)
            formal_images_path.parent.mkdir(parents=True, exist_ok=True)
            if images_path.resolve() != formal_images_path.resolve():
                images_path.replace(formal_images_path)

    formal_raw_rel = raw_rel or formal_raw_relative_path(name)
    if raw_rel:
        raw_path = resolve_kb_relative_path(kb_dir, raw_rel)
        if raw_path is not None and raw_path.exists():
            formal_raw_path = formal_raw_full_path(kb_dir, name)
            formal_raw_path.parent.mkdir(parents=True, exist_ok=True)
            if raw_path.resolve() != formal_raw_path.resolve():
                if formal_raw_path.exists():
                    formal_raw_path.unlink()
                raw_path.replace(formal_raw_path)
            formal_raw_rel = formal_raw_relative_path(name)

    return {
        "formal_raw_rel": formal_raw_rel,
        "formal_source_rel": formal_source_relative_path(stem, suffix),
    }


def _rewrite_formal_summary_full_text(summary_path: Path, full_text: str) -> None:
    text = summary_path.read_text(encoding="utf-8")
    normalized = normalize_kb_relative_path(full_text).removeprefix("wiki/")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            frontmatter = text[: end + len("\n---")]
            body = text[end + len("\n---"):].lstrip("\r\n")
            lines = []
            replaced = False
            for line in frontmatter.splitlines():
                if line.startswith("full_text:"):
                    lines.append(f"full_text: {normalized}")
                    replaced = True
                else:
                    lines.append(line)
            if not replaced:
                lines.insert(2, f"full_text: {normalized}")
            summary_path.write_text("\n".join(lines) + "\n\n" + body, encoding="utf-8")


def _rewrite_formal_source_image_refs(source_path: Path, stem: str) -> None:
    text = source_path.read_text(encoding="utf-8")
    updated = text
    staged_prefix = ".openkb/sources/images/"
    if staged_prefix in updated:
        import re

        pattern = re.compile(
            rf"\.openkb/sources/images/\d{{4}}-\d{{2}}-\d{{2}}/{re.escape(stem)}/"
        )
        updated = pattern.sub(f"sources/images/{stem}/", updated)
    if updated != text:
        source_path.write_text(updated, encoding="utf-8")


def _update_promoted_document_metadata(
    kb_dir: Path,
    document: dict[str, Any],
    *,
    ledger_record: dict[str, Any] | None,
    raw_rel: str,
    source_rel: str,
) -> None:
    file_hash = str(document["hash"])
    hashes = HashRegistry(kb_dir / ".openkb" / "hashes.json")
    meta = hashes.get(file_hash) or {}
    updated_meta = dict(meta)
    updated_meta["raw_path"] = normalize_kb_relative_path(raw_rel)
    updated_meta["source_path"] = normalize_kb_relative_path(source_rel)
    hashes.add(file_hash, updated_meta)

    upsert_document_ledger_record(
        kb_dir,
        file_hash,
        {
            "raw_path": normalize_kb_relative_path(raw_rel),
            "source_path": normalize_kb_relative_path(source_rel),
            "workflow_state": {"promotion_state": "promoted"},
            "execution": {"last_error": "", "updated_at": _now_iso()},
        },
    )


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


def _promotion_document_name(kb_dir: Path, file_hash: str) -> str:
    try:
        document = resolve_source_document(kb_dir, file_hash)
    except Exception:
        return ""
    return str(document.get("name") or "")


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
        return "pageindex"
    if raw_type == "long_pdf":
        return "pageindex"
    return "short"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
