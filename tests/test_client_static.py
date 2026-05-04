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
    assert "async function stopJob" in script
    assert "async function retryJob" in script
    assert "`/api/jobs/${jobId}/stop`" in script
    assert "`/api/jobs/${jobId}/retry`" in script


def test_client_script_summarizes_partial_add_failures():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "job.result?.failed" in script
    assert "`${job.result.added} added, ${job.result.failed} failed`" in script
    assert '" with failures"' in script
    assert 'id="uploadInput" type="file" multiple' in script
    assert 'Array.from(input.files).forEach((file) => form.append("file", file))' in script


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


def test_client_wiki_renders_folder_navigation():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert "function buildWikiDirectory" in script
    assert "function renderWikiDirectory" in script
    assert "folder-row" in script
    assert "file-row" in script
    assert ".wiki-browser" in styles
    assert ".folder-row" in styles
    assert ".file-row.active" in styles


def test_client_settings_support_llm_profile_switching():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert "renderProfileList" in script
    assert 'id="compileConcurrencyInput"' in script
    assert 'id="profileNameInput"' in script
    assert 'id="saveProfileBtn"' in script
    assert 'id="saveNewProfileBtn"' in script
    assert 'id="exportProfilesBtn"' in script
    assert 'id="importProfilesInput"' in script
    assert 'id="toggleApiKeyBtn"' in script
    assert 'value="${escapeHTML(profile.api_key || "")}"' in script
    assert "function toggleApiKeyVisibility" in script
    assert 'apiKeyInput.type === "password" ? "text" : "password"' in script
    assert 'async function exportLlmConfig(event)' in script
    assert 'async function importLlmConfig(event)' in script
    assert '"/api/config/export"' in script
    assert '"/api/config/import"' in script
    assert "compile_max_concurrency" in script
    assert "switchProfile" in script
    assert "create_profile: true" in script
    assert "active_profile" in script
    assert ".profile-list" in styles
    assert ".profile-button.active" in styles


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
