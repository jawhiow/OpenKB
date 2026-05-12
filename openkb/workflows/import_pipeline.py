"""Import-only workflow for staged document ingest."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from openkb.converter import ConvertResult, convert_document
from openkb.document_ledger import get_document_ledger_record, upsert_document_ledger_record
from openkb.source_relations import current_ingested_at
from openkb.state import HashRegistry


_OCR_STRATEGY_PREFIX = "ocr-"


def import_document_source(
    file_path: Path,
    kb_dir: Path,
    *,
    force: bool = False,
    strategy_override: str | None = None,
    job=None,
) -> dict[str, Any]:
    """Convert one file into raw/source artifacts without compiling wiki pages."""
    try:
        result = convert_document(file_path, kb_dir, force=force, strategy_override=strategy_override, job=job)
    except Exception as exc:
        register_import_failure(file_path, kb_dir, exc, strategy_override=strategy_override)
        raise
    if result.skipped:
        return {
            "name": file_path.name,
            "file_hash": result.file_hash,
            "skipped": True,
            "ledger_record": None,
        }

    ledger_record = register_converted_document(
        file_path,
        kb_dir,
        result,
        summary_state="not_started",
        review_state="unreviewed",
        promotion_state="not_selected",
        source_state="ready" if result.source_path is not None else "queued",
    )
    return {
        "name": file_path.name,
        "file_hash": result.file_hash,
        "skipped": False,
        "raw_path": _relative_to_kb(kb_dir, result.raw_path),
        "source_path": _relative_to_kb(kb_dir, result.source_path),
        "ledger_record": ledger_record,
    }


def register_import_failure(
    file_path: Path,
    kb_dir: Path,
    error: Exception,
    *,
    strategy_override: str | None = None,
) -> dict[str, Any] | None:
    """Persist a failed source-preparation state when the file can be identified."""
    try:
        file_hash = HashRegistry.hash_file(file_path)
    except OSError:
        return None

    registry = HashRegistry(Path(kb_dir) / ".openkb" / "hashes.json")
    if not registry.is_known(file_hash):
        registry.add(
            file_hash,
            {
                "name": file_path.name,
                "type": file_path.suffix.lstrip(".").lower(),
                "ingested_at": current_ingested_at(),
            },
        )

    existing = get_document_ledger_record(kb_dir, file_hash)
    retry_count = int((existing or {}).get("execution", {}).get("retry_count") or 0) + 1
    ocr_state = "failed" if _looks_like_ocr_strategy(strategy_override) else "not_needed"
    return upsert_document_ledger_record(
        kb_dir,
        file_hash,
        {
            "name": file_path.name,
            "stem": file_path.stem,
            "raw_path": f"raw/{file_path.name}",
            "source_kind": _source_kind_from_suffix(file_path),
            "workflow_state": {
                "ingest_state": "imported",
                "ocr_state": ocr_state,
                "source_state": "failed",
                "summary_state": "not_started",
                "review_state": "unreviewed",
                "promotion_state": "not_selected",
            },
            "execution": {
                "last_error": str(error),
                "retry_count": retry_count,
                "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            },
        },
    )


def register_converted_document(
    file_path: Path,
    kb_dir: Path,
    result: ConvertResult,
    *,
    summary_state: str,
    review_state: str,
    promotion_state: str,
    source_state: str = "ready",
) -> dict[str, Any] | None:
    """Register a converted document in hashes and the staged ledger."""
    if not result.file_hash:
        return None

    doc_type = document_type_for_result(file_path, result)
    ingested_at = current_ingested_at()
    metadata: dict[str, Any] = {
        "name": file_path.name,
        "type": doc_type,
        "ingested_at": ingested_at,
    }
    if result.page_count is not None:
        metadata["pages"] = result.page_count

    registry = HashRegistry(Path(kb_dir) / ".openkb" / "hashes.json")
    registry.add(result.file_hash, metadata)

    source_kind = source_kind_for_result(doc_type, result)
    ledger_updates = {
        "name": file_path.name,
        "stem": file_path.stem,
        "raw_path": _relative_to_kb(kb_dir, result.raw_path) or f"raw/{file_path.name}",
        "ingested_at": ingested_at,
        "source_kind": source_kind,
        "page_count": result.page_count,
        "scan_detected": result.scan_detected,
        "workflow_state": {
            "ingest_state": "imported",
            "ocr_state": _ocr_state_for_result(result),
            "source_state": source_state,
            "summary_state": summary_state,
            "review_state": review_state,
            "promotion_state": promotion_state,
        },
        "execution": {
            "last_error": "",
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
    }
    return upsert_document_ledger_record(kb_dir, result.file_hash, ledger_updates)


def document_type_for_result(file_path: Path, result: ConvertResult) -> str:
    """Return the legacy hash-registry document type for a converted result."""
    if result.local_long_doc:
        return "local_long_pdf"
    if result.is_long_doc or result.selected_strategy == "ocr-pageindex-local":
        return "long_pdf"
    return file_path.suffix.lstrip(".").lower()


def source_kind_for_result(doc_type: str, result: ConvertResult) -> str:
    """Return the staged source-kind label for a converted result."""
    if result.source_path is not None:
        suffix = result.source_path.suffix.lower()
        if suffix == ".md":
            return "markdown"
        if suffix == ".json":
            return "local_long_json" if doc_type == "local_long_pdf" else "page_json"
    if doc_type == "local_long_pdf":
        return "local_long_json"
    if doc_type == "long_pdf":
        return "pageindex_cloud"
    return "markdown" if doc_type else ""


def _ocr_state_for_result(result: ConvertResult) -> str:
    if result.selected_strategy.startswith(_OCR_STRATEGY_PREFIX):
        return "ready"
    if result.scan_detected:
        return "queued"
    return "not_needed"


def _looks_like_ocr_strategy(strategy_override: str | None) -> bool:
    return str(strategy_override or "").strip().startswith(_OCR_STRATEGY_PREFIX)


def _source_kind_from_suffix(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    if suffix in {".md", ".markdown", ".txt", ".html", ".htm", ".docx", ".csv", ".pptx", ".xlsx"}:
        return "markdown"
    if suffix == ".pdf":
        return "markdown"
    return ""


def _relative_to_kb(kb_dir: Path, path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.relative_to(kb_dir).as_posix()
    except ValueError:
        return str(path)
