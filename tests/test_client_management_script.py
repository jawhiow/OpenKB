from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "openkb-client.ps1"


def test_client_management_script_contains_expected_controls():
    assert SCRIPT.exists(), f"missing script: {SCRIPT}"

    text = SCRIPT.read_text(encoding="utf-8")

    assert "ValidateSet(\"start\", \"stop\", \"restart\", \"status\")" in text
    assert "-m" in text
    assert "openkb" in text
    assert "client" in text
    assert "--no-browser" in text
    assert ".openkb-client" in text
    assert "Start-Process" in text
    assert "Stop-Process" in text


def test_client_management_script_status_reports_not_running(tmp_path):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell is not available")

    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "status",
            "-Port",
            "18765",
            "-StateDir",
            str(tmp_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 3
    assert "OpenKB client is not running" in result.stdout
