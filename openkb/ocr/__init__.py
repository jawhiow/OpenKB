from __future__ import annotations

from .cache import (
    ocr_cache_entry_dir,
    ocr_cache_manifest_path,
    ocr_cache_pageindex_input_path,
    ocr_cache_pages_path,
    ocr_cache_root,
    read_ocr_manifest,
    write_ocr_manifest,
)
from .client import download_ocr_jsonl, run_ocr_chunks, submit_ocr_job, wait_for_ocr_job
from .chunking import build_page_chunks
from .detect import is_probably_scanned_pdf
from .normalize import normalize_ocr_payloads
from .pipeline import prepare_ocr_artifacts

__all__ = [
    "build_page_chunks",
    "download_ocr_jsonl",
    "is_probably_scanned_pdf",
    "normalize_ocr_payloads",
    "ocr_cache_entry_dir",
    "ocr_cache_manifest_path",
    "ocr_cache_pageindex_input_path",
    "ocr_cache_pages_path",
    "ocr_cache_root",
    "prepare_ocr_artifacts",
    "read_ocr_manifest",
    "run_ocr_chunks",
    "submit_ocr_job",
    "write_ocr_manifest",
    "wait_for_ocr_job",
]
