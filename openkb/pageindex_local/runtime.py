from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def pageindex_local_install_root(root: Path) -> Path:
    """Return the local PageIndex installation root."""
    return Path(root)


def pageindex_local_manifest_path(root: Path) -> Path:
    """Return the runtime installation manifest path."""
    return pageindex_local_install_root(root) / "installation.json"


def read_pageindex_local_manifest(root: Path) -> dict[str, Any] | None:
    """Return the local PageIndex manifest, or None when it is missing."""
    manifest_path = pageindex_local_manifest_path(root)
    if not manifest_path.exists():
        return None
    with manifest_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_pageindex_local_manifest(manifest: dict[str, Any], root: Path) -> Path:
    """Persist and return the local PageIndex installation manifest path."""
    manifest_path = pageindex_local_manifest_path(root)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    return manifest_path


def runtime_is_ready(root: Path) -> bool:
    """Return True when the manifest points at the required runtime files."""
    manifest = read_pageindex_local_manifest(root)
    if not manifest:
        return False

    required_keys = ("repo_dir", "python_path", "script_path")
    for key in required_keys:
        value = str(manifest.get(key) or "").strip()
        if not value or not Path(value).exists():
            return False
    return True

