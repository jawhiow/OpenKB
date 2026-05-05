from __future__ import annotations

import subprocess
from pathlib import Path

from openkb.pageindex_local.runtime import write_pageindex_local_manifest


def record_existing_runtime(root: Path, *, repo_dir: Path, python_path: Path, script_path: Path, version: str = "") -> Path:
    """Record an already-provisioned local PageIndex runtime."""
    return write_pageindex_local_manifest(
        {
            "repo_dir": str(Path(repo_dir)),
            "python_path": str(Path(python_path)),
            "script_path": str(Path(script_path)),
            "version": version,
        },
        root,
    )


def run_setup_script(script_path: Path, root: Path) -> subprocess.CompletedProcess[str]:
    """Run a repository setup script for the local PageIndex runtime."""
    return subprocess.run(
        [str(script_path), str(root)],
        check=True,
        text=True,
        capture_output=True,
    )

