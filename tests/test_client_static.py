from __future__ import annotations

from pathlib import Path


STATIC_DIR = Path(__file__).resolve().parents[1] / "openkb" / "client" / "static"


def test_client_shell_exposes_job_details_panel():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="jobDetails"' in html
    assert 'id="jobLogList"' in html
    assert 'id="toastHost"' in html


def test_client_script_renders_job_progress_logs_and_busy_buttons():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "function setButtonBusy" in script
    assert "function notify" in script
    assert "function selectJob" in script
    assert "function renderJobDetails" in script
    assert "progress-bar" in script
    assert "job-log-list" in script


def test_client_api_error_path_reads_response_body_once():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "const raw = await response.text();" in script
    assert "const body = await response.json();" not in script


def test_client_settings_include_test_llm_button_and_handler():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'id="testLlmBtn"' in script
    assert 'addEventListener("click", testLlm);' in script
    assert 'async function testLlm(event)' in script
    assert '"/api/config/test-llm"' in script
