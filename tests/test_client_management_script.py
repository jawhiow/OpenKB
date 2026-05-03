from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "openkb-client.ps1"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        for port in range(18000, 24000):
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return int(sock.getsockname()[1])
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_port(port: int, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise AssertionError(f"port {port} did not start listening")


def _run_script(shell: str, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    capture = "--capture" in args
    filtered_args = tuple(arg for arg in args if arg != "--capture")
    stdout = subprocess.PIPE if capture else subprocess.DEVNULL
    stderr = subprocess.PIPE if capture else subprocess.DEVNULL
    return subprocess.run(
        [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            *filtered_args,
        ],
        cwd=REPO_ROOT,
        stdout=stdout,
        stderr=stderr,
        text=True,
        timeout=timeout,
    )


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

    result = _run_script(shell, "--capture", "status", "-Port", "18765", "-StateDir", str(tmp_path), timeout=20)

    assert result.returncode == 3
    assert "OpenKB client is not running" in result.stdout


def test_client_management_script_restart_replaces_unmanaged_openkb_port_owner(tmp_path):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell is not available")

    port = _free_port()
    unmanaged = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "openkb",
            "client",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-browser",
        ],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        _wait_for_port(port, timeout=25)
        result = _run_script(
            shell,
            "restart",
            "-Port",
            str(port),
            "-StateDir",
            str(tmp_path),
            "-Python",
            sys.executable,
            timeout=60,
        )

        assert result.returncode == 0
        assert unmanaged.poll() is not None
        assert (tmp_path / f"client-{port}.pid").exists()
    finally:
        _run_script(shell, "stop", "-Port", str(port), "-StateDir", str(tmp_path), timeout=30)
        if unmanaged.poll() is None:
            unmanaged.terminate()
            try:
                unmanaged.wait(timeout=5)
            except subprocess.TimeoutExpired:
                unmanaged.kill()
