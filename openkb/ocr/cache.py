from __future__ import annotations

from pathlib import Path

from openkb.state import (
    ocr_cache_entry_dir as _ocr_cache_entry_dir,
    ocr_cache_manifest_path as _ocr_cache_manifest_path,
    ocr_cache_pageindex_input_path as _ocr_cache_pageindex_input_path,
    ocr_cache_pages_path as _ocr_cache_pages_path,
    ocr_cache_root as _ocr_cache_root,
    read_ocr_manifest as _read_ocr_manifest,
    write_ocr_manifest as _write_ocr_manifest,
)


def ocr_cache_root(kb_dir: Path) -> Path:
    return _ocr_cache_root(kb_dir)


def ocr_cache_entry_dir(kb_dir: Path, file_hash: str) -> Path:
    return _ocr_cache_entry_dir(kb_dir, file_hash)


def ocr_cache_manifest_path(kb_dir: Path, file_hash: str) -> Path:
    return _ocr_cache_manifest_path(kb_dir, file_hash)


def ocr_cache_pages_path(kb_dir: Path, file_hash: str) -> Path:
    return _ocr_cache_pages_path(kb_dir, file_hash)


def ocr_cache_pageindex_input_path(kb_dir: Path, file_hash: str) -> Path:
    return _ocr_cache_pageindex_input_path(kb_dir, file_hash)


def read_ocr_manifest(kb_dir: Path, file_hash: str) -> dict | None:
    return _read_ocr_manifest(kb_dir, file_hash)


def write_ocr_manifest(kb_dir: Path, file_hash: str, manifest: dict) -> Path:
    return _write_ocr_manifest(kb_dir, file_hash, manifest)
