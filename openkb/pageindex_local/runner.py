from __future__ import annotations

import subprocess
from pathlib import Path

from openkb.pageindex_local.runtime import read_pageindex_local_manifest, runtime_is_ready


def build_pageindex_local_command(
    input_path: Path,
    output_path: Path,
    *,
    root: Path,
    model: str | None = None,
) -> list[str]:
    """Build the command used to run the configured local PageIndex runtime."""
    if not runtime_is_ready(root):
        raise RuntimeError("Local PageIndex runtime is not ready")
    manifest = read_pageindex_local_manifest(root) or {}
    command = [
        str(manifest["python_path"]),
        str(manifest["script_path"]),
        "--input",
        str(input_path),
        "--output",
        str(output_path),
    ]
    model = str(model or "").strip()
    if model:
        command.extend(["--model", model])
    return command


def run_pageindex_local(
    input_path: Path,
    output_path: Path,
    *,
    root: Path,
    model: str | None = None,
    timeout: int = 3600,
) -> subprocess.CompletedProcess[str]:
    """Run local PageIndex against OCR Markdown and write a tree JSON file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = build_pageindex_local_command(input_path, output_path, root=root, model=model)
    return subprocess.run(
        command,
        check=True,
        text=True,
        capture_output=True,
        timeout=timeout,
    )

