"""Import-only workflow for staged document ingest."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil
from typing import Any

from openkb.converter import ConvertResult, convert_document
from openkb.document_ledger import get_document_ledger_record, upsert_document_ledger_record
from openkb.pdf_strategy import OCR_PAGEINDEX_LOCAL, PAGEINDEX_LOCAL
from openkb.source_relations import (
    current_ingested_at,
    kb_relative_path,
    safe_artifact_stem,
    staged_raw_full_path,
    staged_source_full_path,
    staged_source_images_full_dir,
)
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
    effective_force = force or _is_known_failed_import(file_path, kb_dir)
    try:
        result = convert_document(file_path, kb_dir, force=effective_force, strategy_override=strategy_override, job=job)
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

    ingested_at = current_ingested_at()
    staged_result = _stage_import_artifacts(kb_dir, file_path, result, ingested_at=ingested_at)

    ledger_record = register_converted_document(
        file_path,
        kb_dir,
        staged_result,
        ingested_at=ingested_at,
        summary_state="not_started",
        review_state="unreviewed",
        promotion_state="not_selected",
        source_state="ready" if staged_result.source_path is not None else "queued",
    )
    return {
        "name": file_path.name,
        "file_hash": staged_result.file_hash,
        "skipped": False,
        "raw_path": _relative_to_kb(kb_dir, staged_result.raw_path),
        "source_path": _relative_to_kb(kb_dir, staged_result.source_path),
        "ledger_record": ledger_record,
    }


def _is_known_failed_import(file_path: Path, kb_dir: Path) -> bool:
    """Return True when re-importing a file whose previous source prep failed."""
    try:
        file_hash = HashRegistry.hash_file(file_path)
    except OSError:
        return False
    existing = get_document_ledger_record(kb_dir, file_hash)
    workflow_state = (existing or {}).get("workflow_state") or {}
    return (
        workflow_state.get("source_state") == "failed"
        or workflow_state.get("ocr_state") == "failed"
    )


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

    ingested_at = current_ingested_at()
    failed_name = f"{safe_artifact_stem(file_path.stem, file_hash=file_hash, suffix=file_path.suffix)}{file_path.suffix}"
    failed_raw_path = staged_raw_full_path(kb_dir, failed_name, ingested_at)
    try:
        failed_raw_path.parent.mkdir(parents=True, exist_ok=True)
        if file_path.resolve() != failed_raw_path.resolve():
            shutil.copy2(file_path, failed_raw_path)
    except OSError:
        failed_raw_path = Path(kb_dir) / f".openkb/raw/{datetime.now().astimezone().date().isoformat()}/{failed_name}"

    registry = HashRegistry(Path(kb_dir) / ".openkb" / "hashes.json")
    if not registry.is_known(file_hash):
        registry.add(
            file_hash,
            {
                "name": file_path.name,
                "type": file_path.suffix.lstrip(".").lower(),
                "ingested_at": ingested_at,
                "raw_path": _relative_to_kb(kb_dir, failed_raw_path),
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
            "stem": Path(failed_name).stem,
            "raw_path": _relative_to_kb(kb_dir, failed_raw_path),
            "ingested_at": ingested_at,
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
    ingested_at: str | None = None,
    summary_state: str,
    review_state: str,
    promotion_state: str,
    source_state: str = "ready",
) -> dict[str, Any] | None:
    """Register a converted document in hashes and the staged ledger."""
    if not result.file_hash:
        return None

    doc_type = document_type_for_result(file_path, result)
    ingested_at = ingested_at or current_ingested_at()
    metadata: dict[str, Any] = {
        "name": file_path.name,
        "type": doc_type,
        "ingested_at": ingested_at,
        "raw_path": _relative_to_kb(kb_dir, result.raw_path),
        "source_path": _relative_to_kb(kb_dir, result.source_path),
    }
    if result.page_count is not None:
        metadata["pages"] = result.page_count

    registry = HashRegistry(Path(kb_dir) / ".openkb" / "hashes.json")
    registry.add(result.file_hash, metadata)

    source_kind = source_kind_for_result(doc_type, result)
    artifact_stem = Path(result.source_path).stem if result.source_path is not None else safe_artifact_stem(
        file_path.stem,
        file_hash=result.file_hash,
        suffix=file_path.suffix,
    )
    ledger_updates = {
        "name": file_path.name,
        "stem": artifact_stem,
        "raw_path": _relative_to_kb(kb_dir, result.raw_path) or f"raw/{file_path.name}",
        "source_path": _relative_to_kb(kb_dir, result.source_path) or "",
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
    if result.is_long_doc or result.selected_strategy in {OCR_PAGEINDEX_LOCAL, PAGEINDEX_LOCAL}:
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
        if result.source_path is not None and result.selected_strategy in {OCR_PAGEINDEX_LOCAL, PAGEINDEX_LOCAL}:
            return "page_json"
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


def _stage_import_artifacts(
    kb_dir: Path,
    file_path: Path,
    result: ConvertResult,
    *,
    ingested_at: str,
) -> ConvertResult:
    staged_raw_path: Path | None = None
    staged_source_path: Path | None = None

    if result.raw_path is not None and result.raw_path.exists():
        staged_raw_name = f"{safe_artifact_stem(file_path.stem, file_hash=result.file_hash, suffix=file_path.suffix)}{file_path.suffix}"
        staged_raw_path = staged_raw_full_path(kb_dir, staged_raw_name, ingested_at)
        staged_raw_path.parent.mkdir(parents=True, exist_ok=True)
        if result.raw_path.resolve() != staged_raw_path.resolve():
            result.raw_path.replace(staged_raw_path)
        else:
            staged_raw_path = result.raw_path

    if result.source_path is not None and result.source_path.exists():
        suffix = result.source_path.suffix
        artifact_stem = safe_artifact_stem(
            result.source_path.stem,
            file_hash=result.file_hash,
            suffix=suffix,
        )
        staged_source_path = staged_source_full_path(kb_dir, artifact_stem, suffix, ingested_at)
        staged_source_path.parent.mkdir(parents=True, exist_ok=True)
        source_text = result.source_path.read_text(encoding="utf-8")
        source_text = _rewrite_staged_source_image_refs(source_text, result.source_path.stem, ingested_at, artifact_stem=artifact_stem)
        staged_source_path.write_text(source_text, encoding="utf-8")
        if result.source_path.exists() and result.source_path.resolve() != staged_source_path.resolve():
            result.source_path.unlink()

        legacy_images_dir = kb_dir / "wiki" / "sources" / "images" / result.source_path.stem
        staged_images_dir = staged_source_images_full_dir(kb_dir, artifact_stem, ingested_at)
        if legacy_images_dir.exists():
            staged_images_dir.parent.mkdir(parents=True, exist_ok=True)
            if staged_images_dir.exists():
                for path in legacy_images_dir.iterdir():
                    target = staged_images_dir / path.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    path.replace(target)
                legacy_images_dir.rmdir()
            else:
                legacy_images_dir.replace(staged_images_dir)

    return ConvertResult(
        raw_path=staged_raw_path,
        source_path=staged_source_path,
        is_long_doc=result.is_long_doc,
        local_long_doc=result.local_long_doc,
        scan_detected=result.scan_detected,
        recommended_strategy=result.recommended_strategy,
        selected_strategy=result.selected_strategy,
        pageindex_input_path=result.pageindex_input_path,
        skipped=result.skipped,
        file_hash=result.file_hash,
        page_count=result.page_count,
    )


def _rewrite_staged_source_image_refs(text: str, stem: str, ingested_at: str, *, artifact_stem: str | None = None) -> str:
    date_part = datetime.fromisoformat(ingested_at.replace("Z", "+00:00")).date().isoformat()
    return text.replace(
        f"(sources/images/{stem}/",
        f"(.openkb/sources/images/{date_part}/{artifact_stem or stem}/",
    )
