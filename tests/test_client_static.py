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


def test_client_fix_plan_renders_report_reason_and_preview_content():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "function fixKey" in script
    assert "source_section" in script
    assert "fix-reason" in script
    assert "fix-preview" in script
    assert "auto_applicable" in script


def test_client_fix_plan_allows_manual_review_approval_and_result_display():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert "function isManualReviewFix" in script
    assert "function isSelectableFix" in script
    assert "approved: Boolean(isSelectableFix(item) && state.selectedFixes[fixKey(item)])" in script
    assert "state.lastFixApply?.reviewed?.length" in script
    assert "Approved review -" in script
    assert "reviewed-fix" in script
    assert "renderCreatedFixes" not in script
    assert ".badge.info" in styles
    assert ".reviewed-fix" in styles
