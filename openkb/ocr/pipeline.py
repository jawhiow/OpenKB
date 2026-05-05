from __future__ import annotations

import json
from pathlib import Path

import pymupdf

from openkb.config import DEFAULT_CONFIG, load_config
from openkb.ocr.chunking import build_page_chunks
from openkb.ocr.client import run_ocr_chunks
from openkb.ocr.normalize import normalize_ocr_payloads
from openkb.state import (
    ocr_cache_entry_dir,
    ocr_cache_pageindex_input_path,
    ocr_cache_pages_path,
    read_ocr_manifest,
    write_ocr_manifest,
)


def _job_log(job, message: str, level: str = "info") -> None:
    add_log = getattr(job, "add_log", None)
    if callable(add_log):
        add_log(message, level=level)


def get_pdf_page_count(pdf_path: Path) -> int:
    with pymupdf.open(str(pdf_path)) as doc:
        return doc.page_count


def _read_env_value(kb_dir: Path, key: str) -> str:
    env_path = Path(kb_dir) / ".env"
    if not env_path.exists():
        return ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        env_key, env_value = line.split("=", 1)
        if env_key.strip() == key:
            return env_value.strip()
    return ""


def _write_pdf_chunk_files(pdf_path: Path, chunk_plan: list[dict[str, int]], chunk_dir: Path) -> list[Path]:
    chunk_dir.mkdir(parents=True, exist_ok=True)
    chunk_files: list[Path] = []
    with pymupdf.open(str(pdf_path)) as src_doc:
        for chunk in chunk_plan:
            dst_doc = pymupdf.open()
            dst_doc.insert_pdf(src_doc, from_page=chunk["start_page"] - 1, to_page=chunk["end_page"] - 1)
            chunk_path = chunk_dir / f"chunk-{chunk['index']}.pdf"
            dst_doc.save(str(chunk_path))
            dst_doc.close()
            chunk_files.append(chunk_path)
    return chunk_files


def prepare_ocr_artifacts(
    pdf_path: Path,
    kb_dir: Path,
    doc_name: str,
    file_hash: str,
    *,
    force: bool = False,
    ocr_model: str | None = None,
    job=None,
) -> dict[str, Path]:
    """Build or reuse OCR artifacts for one PDF."""
    pages_path = ocr_cache_pages_path(kb_dir, file_hash)
    pageindex_input_path = ocr_cache_pageindex_input_path(kb_dir, file_hash)
    manifest = read_ocr_manifest(kb_dir, file_hash)
    if (
        not force
        and manifest
        and manifest.get("status") == "ready"
        and pages_path.exists()
        and pageindex_input_path.exists()
    ):
        _job_log(job, f"OCR cache hit: {doc_name} ({file_hash})")
        return {
            "pages_path": pages_path,
            "pageindex_input_path": pageindex_input_path,
        }

    config = load_config(Path(kb_dir) / ".openkb" / "config.yaml")
    token = _read_env_value(kb_dir, "PADDLEOCR_TOKEN")
    if not token:
        raise RuntimeError("Missing PADDLEOCR_TOKEN in KB-local .env")

    model = ocr_model or str(config.get("ocr_default_model", DEFAULT_CONFIG["ocr_default_model"]))
    chunk_size = max(int(config.get("ocr_chunk_pages", DEFAULT_CONFIG["ocr_chunk_pages"])), 1)
    _job_log(job, f"Preparing OCR artifacts: model={model}, chunk_pages={chunk_size}")
    total_pages = get_pdf_page_count(pdf_path)
    chunk_plan = build_page_chunks(total_pages, chunk_size=chunk_size)
    _job_log(job, f"OCR page plan: {total_pages} page(s) in {len(chunk_plan)} chunk(s)")
    chunk_dir = ocr_cache_entry_dir(kb_dir, file_hash) / "chunks"
    chunk_files = _write_pdf_chunk_files(pdf_path, chunk_plan, chunk_dir)
    _job_log(job, f"OCR chunk files prepared: {len(chunk_files)}")
    _job_log(job, f"Running PaddleOCR: {len(chunk_files)} chunk(s)")
    chunk_results = run_ocr_chunks(
        chunk_files,
        token=token,
        model=model,
        optional_payload={
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useChartRecognition": False,
        },
        job=job,
    )
    payloads = [payload for chunk in chunk_results for payload in chunk.get("payloads", [])]
    pages, pageindex_input = normalize_ocr_payloads(payloads, doc_name=doc_name)
    _job_log(job, f"Normalized OCR output: {len(pages)} page(s)")

    pages_path.parent.mkdir(parents=True, exist_ok=True)
    pages_path.write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding="utf-8")
    pageindex_input_path.parent.mkdir(parents=True, exist_ok=True)
    pageindex_input_path.write_text(pageindex_input, encoding="utf-8")
    write_ocr_manifest(
        kb_dir,
        file_hash,
        {
            "status": "ready",
            "file_hash": file_hash,
            "doc_name": doc_name,
            "page_count": total_pages,
            "ocr_model": model,
            "chunks": chunk_plan,
        },
    )
    _job_log(job, f"OCR artifacts cached: {doc_name} ({file_hash})")
    return {
        "pages_path": pages_path,
        "pageindex_input_path": pageindex_input_path,
    }
