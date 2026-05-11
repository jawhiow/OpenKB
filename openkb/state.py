from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path


_HASH_REGISTRY_LOCK = threading.RLock()


def ocr_cache_root(kb_dir: Path) -> Path:
    """Return the KB-local OCR cache root directory."""
    return Path(kb_dir) / ".openkb" / "ocr" / "cache"


def ocr_cache_entry_dir(kb_dir: Path, file_hash: str) -> Path:
    """Return the OCR cache directory for one source file hash."""
    return ocr_cache_root(kb_dir) / file_hash


def ocr_cache_manifest_path(kb_dir: Path, file_hash: str) -> Path:
    """Return the manifest path for one OCR cache entry."""
    return ocr_cache_entry_dir(kb_dir, file_hash) / "manifest.json"


def ocr_cache_pages_path(kb_dir: Path, file_hash: str) -> Path:
    """Return the normalized page JSON path for one OCR cache entry."""
    return ocr_cache_entry_dir(kb_dir, file_hash) / "normalized" / "pages.json"


def ocr_cache_pageindex_input_path(kb_dir: Path, file_hash: str) -> Path:
    """Return the normalized Markdown path used as local PageIndex input."""
    return ocr_cache_entry_dir(kb_dir, file_hash) / "normalized" / "pageindex_input.md"


def read_ocr_manifest(kb_dir: Path, file_hash: str) -> dict | None:
    """Return the OCR cache manifest for *file_hash*, or None if missing."""
    manifest_path = ocr_cache_manifest_path(kb_dir, file_hash)
    if not manifest_path.exists():
        return None
    with manifest_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_ocr_manifest(kb_dir: Path, file_hash: str, manifest: dict) -> Path:
    """Persist and return the OCR cache manifest path for *file_hash*."""
    manifest_path = ocr_cache_manifest_path(kb_dir, file_hash)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    return manifest_path


class HashRegistry:
    """Persistent registry mapping file SHA-256 hashes to metadata dicts."""

    def __init__(self, path: Path) -> None:
        self._path = path
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                self._data: dict[str, dict] = json.load(fh)
        else:
            self._data = {}

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def is_known(self, file_hash: str) -> bool:
        """Return True if file_hash is already registered."""
        return file_hash in self._data

    def get(self, file_hash: str) -> dict | None:
        """Return metadata for file_hash, or None if not found."""
        return self._data.get(file_hash)

    def all_entries(self) -> dict[str, dict]:
        """Return a shallow copy of all hash -> metadata entries."""
        return dict(self._data)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add(self, file_hash: str, metadata: dict) -> None:
        """Register file_hash with metadata and persist to disk."""
        with _HASH_REGISTRY_LOCK:
            if self._path.exists():
                with self._path.open("r", encoding="utf-8") as fh:
                    self._data = json.load(fh)
            self._data[file_hash] = metadata
            self._persist()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _persist(self) -> None:
        with _HASH_REGISTRY_LOCK:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)

    # ------------------------------------------------------------------
    # Static utility
    # ------------------------------------------------------------------

    @staticmethod
    def hash_file(path: Path) -> str:
        """Return the SHA-256 hex digest (64 chars) of the file at path."""
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
