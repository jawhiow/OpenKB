from __future__ import annotations

from pathlib import Path

import pytest

from openkb.pageindex_local.runner import build_pageindex_local_command
from openkb.pageindex_local.runtime import (
    pageindex_local_install_root,
    pageindex_local_manifest_path,
    read_pageindex_local_manifest,
    runtime_is_ready,
    write_pageindex_local_manifest,
)


def test_pageindex_local_runtime_paths_use_cache_root(tmp_path):
    root = tmp_path / "pageindex-local"

    assert pageindex_local_install_root(root) == root
    assert pageindex_local_manifest_path(root) == root / "installation.json"


def test_write_and_read_pageindex_local_manifest_roundtrip(tmp_path):
    root = tmp_path / "pageindex-local"
    runtime_dir = root / "runtime"
    runtime_dir.mkdir(parents=True)
    python_path = runtime_dir / "python.exe"
    script_path = runtime_dir / "run_pageindex.py"
    python_path.write_text("", encoding="utf-8")
    script_path.write_text("", encoding="utf-8")

    manifest = {
        "repo_dir": str(runtime_dir),
        "python_path": str(python_path),
        "script_path": str(script_path),
        "version": "test-version",
    }

    manifest_path = write_pageindex_local_manifest(manifest, root)

    assert manifest_path == root / "installation.json"
    assert read_pageindex_local_manifest(root) == manifest
    assert runtime_is_ready(root) is True


def test_runtime_is_not_ready_when_required_files_are_missing(tmp_path):
    root = tmp_path / "pageindex-local"
    manifest = {
        "repo_dir": str(root / "runtime"),
        "python_path": str(root / "runtime" / "python.exe"),
        "script_path": str(root / "runtime" / "run_pageindex.py"),
        "version": "test-version",
    }

    write_pageindex_local_manifest(manifest, root)

    assert runtime_is_ready(root) is False


def test_build_pageindex_local_command_uses_manifest_runtime(tmp_path):
    root = tmp_path / "pageindex-local"
    runtime_dir = root / "runtime"
    runtime_dir.mkdir(parents=True)
    python_path = runtime_dir / "python.exe"
    script_path = runtime_dir / "run_pageindex.py"
    input_path = tmp_path / "ocr-input.md"
    output_path = tmp_path / "tree.json"
    python_path.write_text("", encoding="utf-8")
    script_path.write_text("", encoding="utf-8")
    input_path.write_text("## Page 1\n\nOCR text", encoding="utf-8")

    write_pageindex_local_manifest(
        {
            "repo_dir": str(runtime_dir),
            "python_path": str(python_path),
            "script_path": str(script_path),
            "version": "test-version",
        },
        root,
    )

    command = build_pageindex_local_command(
        input_path,
        output_path,
        root=root,
        model="gpt-5.4",
    )

    assert command == [
        str(python_path),
        str(script_path),
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--model",
        "gpt-5.4",
    ]


def test_build_pageindex_local_command_rejects_missing_runtime(tmp_path):
    with pytest.raises(RuntimeError, match="Local PageIndex runtime is not ready"):
        build_pageindex_local_command(
            tmp_path / "input.md",
            tmp_path / "tree.json",
            root=tmp_path / "missing",
        )
