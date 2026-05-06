from __future__ import annotations

from pathlib import Path


STATIC_DIR = Path(__file__).resolve().parents[1] / "openkb" / "client" / "static"


def test_client_shell_exposes_job_details_panel():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="jobDetails"' in html
    assert 'id="jobLogList"' in html
    assert 'id="toastHost"' in html


def test_client_shell_uses_utility_workbench_layout():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'class="app-shell workbench-shell"' in html
    assert 'class="nav-rail"' in html
    assert 'id="utilityPanel"' in html
    assert 'id="utilityJobsTab"' in html
    assert 'id="utilityAssistantTab"' in html
    assert 'id="jobsPanel"' in html
    assert 'id="assistantPanel"' in html
    assert 'class="job-dock"' not in html


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


def test_client_ask_persists_and_reopens_chat_sessions():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "activeChatSessionId: null" in script
    assert "activeChatSession: null" in script
    assert "function renderChatTranscript" in script
    assert "function setActiveChatSession" in script
    assert "async function openChatSession" in script
    assert "session_id: state.activeChatSessionId" in script
    assert "state.activeChatSessionId = job.result?.session_id" in script
    assert 'data-open-chat="${escapeHTML(session.id)}"' in script
    assert 'data-action="open-chat"' in script
    assert '`/api/chats/${encodeURIComponent(sessionId)}`' in script
    assert "Continue in Assistant" in script


def test_client_script_has_bounded_rendering_and_selective_refresh():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "function paginatedItems" in script
    assert "function paginationState" in script
    assert "function renderPager" in script
    assert "function renderUtilityPanel" in script
    assert "function renderJobsPanel" in script
    assert "function renderMainView" in script
    assert "function handleAppClick" in script
    assert "function handleAppInput" in script
    assert "renderJobsPanel();" in script
    assert "renderJobs();" not in script


def test_client_script_summarizes_partial_add_failures():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "job.result?.failed" in script
    assert "`${job.result.added} added, ${job.result.failed} failed`" in script
    assert '" with failures"' in script
    assert 'id="uploadInput" type="file" multiple' in script
    assert 'Array.from(input.files).forEach((file) => form.append("file", file))' in script


def test_client_sources_view_browses_related_pages_and_deletes_source():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert 'data-view="sources"' in html
    assert 'sources: "Sources"' in script
    assert "function renderSources" in script
    assert "function sourceDocumentList" in script
    assert "function sourceRelationGroups" in script
    assert "function selectSourceDocument" in script
    assert "function openSourceWorkbench" in script
    assert "data-source-search" in script
    assert "data-source-select" in script
    assert "data-source-open-page" in script
    assert "data-delete-source" in script
    assert "async function deleteSourceDocument" in script
    assert "DELETE" in script
    assert "delete_source" in script
    assert ".sources-layout" in styles
    assert ".source-list-item.active" in styles
    assert ".relation-row" in styles


def test_client_documents_table_links_to_source_workbench_without_inline_relations():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert "related_count" in script
    assert "function sourceRelatedCount" in script
    assert "data-source-focus" in script
    assert "data-delete-source" in script
    assert ".source-actions" in styles
    assert "function documentRelatedMarkup" not in script
    assert ".doc-relations" not in styles


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


def test_client_wiki_renders_directory_scoped_file_navigation():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert "function wikiDirectories" in script
    assert "function filteredWikiDirectoryFiles" in script
    assert "function renderWikiFileList" in script
    assert 'id="wikiDirectorySelect"' in script
    assert 'data-action="wiki-directory"' in script
    assert 'data-action="wiki-search"' in script
    assert 'data-action="wiki-select"' in script
    assert "file-row" in script
    assert ".wiki-directory-toolbar" in styles
    assert ".wiki-browser" in styles
    assert ".wiki-search-row" in styles
    assert ".file-row.active" in styles


def test_client_wiki_uses_lazy_file_loading_and_markdown_mode_tabs():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert "wikiFileCache" in script
    assert "wikiDrafts" in script
    assert "wikiSearch" in script
    assert 'wikiMode: "preview"' in script
    assert "function renderMarkdown" in script
    assert "function wikiDisplayContent" in script
    assert "function filteredWikiDirectoryFiles" in script
    assert "function selectWikiFile" in script
    assert "function ensureWikiFileLoaded" in script
    assert "const matches = filteredWikiDirectoryFiles();" in script
    assert "state.selectedWikiPath = matches[0].path;" in script
    assert 'data-action="wiki-mode"' in script
    assert 'id="wikiPreviewPane"' in script
    assert 'id="wikiSourcePane"' in script
    assert "/api/wiki/file" in script
    assert "method: \"PUT\"" in script
    assert ".wiki-mode-tabs" in styles
    assert ".wiki-preview-pane" in styles
    assert ".wiki-source-pane" in styles
    assert "grid-template-rows: auto minmax(220px, 1fr) minmax(180px, 0.7fr)" not in styles
    assert "loadWikiFile();" not in script


def test_client_styles_define_workbench_table_and_utility_primitives():
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert ".workbench-shell" in styles
    assert ".nav-rail" in styles
    assert ".utility-panel" in styles
    assert ".utility-tabs" in styles
    assert ".data-table-shell" in styles
    assert ".data-grid-table" in styles
    assert ".table-pager" in styles
    assert ".wiki-search-row" in styles
    assert ".job-filter-bar" in styles


def test_client_ocr_layout_keeps_actions_visible_and_runtime_compact():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert 'class="ocr-workbench"' in script
    assert 'class="section ocr-cache-panel"' in script
    assert 'class="ocr-runtime-strip"' in script
    assert ".ocr-workbench" in styles
    assert ".ocr-cache-panel" in styles
    assert ".ocr-runtime-strip" in styles
    assert ".ocr-table th:last-child" in styles
    assert "position: sticky" in styles
    assert "right: 0" in styles
    assert "width: 300px" in styles
    assert ".ocr-table .source-actions button" in styles
    assert "min-width: 76px" in styles


def test_client_jobs_panel_uses_compact_details_without_fixed_empty_space():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert 'id="jobDetails" class="job-details compact"' in html
    assert 'details.className = "job-details compact"' in script
    assert ".job-details.compact" in styles
    assert ".job-log-list" in styles
    assert "max-height: min(280px, 34vh)" in styles
    assert "min-height: 150px" not in styles


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
    assert '"openkb-settings-config.json"' in script
    assert "compile_max_concurrency" in script
    assert "switchProfile" in script
    assert "create_profile: true" in script
    assert "active_profile" in script
    assert ".profile-list" in styles
    assert ".profile-button.active" in styles


def test_client_settings_exposes_general_save_button():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'id="saveSettingsBtn"' in script
    assert '$("#saveSettingsBtn").addEventListener("click", saveSettings);' in script
    assert "async function saveSettings(event)" in script
    assert "language: $(\"#languageInput\").value.trim()" in script
    assert "compile_max_concurrency: Number($(\"#compileConcurrencyInput\").value || 2)" in script


def test_client_settings_include_ocr_and_pageindex_local_controls():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'id="ocrEnabledInput"' in script
    assert 'id="ocrDetectionModeInput"' in script
    assert 'id="ocrDefaultModelInput"' in script
    assert 'id="ocrChunkPagesInput"' in script
    assert 'id="ocrAutoRecommendInput"' in script
    assert 'id="paddleocrTokenInput"' in script
    assert 'id="pageindexLocalEnabledInput"' in script
    assert 'id="pageindexLocalModelInput"' in script
    assert 'id="pageindexLocalInstallationStateInput"' in script
    assert 'id="pageindexLocalRepoDirInput"' in script
    assert 'id="pageindexLocalPythonPathInput"' in script
    assert 'id="pageindexLocalScriptPathInput"' in script
    assert 'value="${escapeHTML(cfg.paddleocr_token || "")}"' in script
    assert 'ocr_enabled: $("#ocrEnabledInput").checked' in script
    assert 'ocr_detection_mode: $("#ocrDetectionModeInput").value' in script
    assert 'ocr_default_model: $("#ocrDefaultModelInput").value' in script
    assert 'ocr_chunk_pages: Number($("#ocrChunkPagesInput").value || 100)' in script
    assert 'ocr_auto_recommend: $("#ocrAutoRecommendInput").checked' in script
    assert 'paddleocr_token: $("#paddleocrTokenInput").value' in script
    assert 'pageindex_local_enabled: $("#pageindexLocalEnabledInput").checked' in script
    assert 'pageindex_local_model: $("#pageindexLocalModelInput").value.trim()' in script
    assert 'pageindex_local_installation_state: $("#pageindexLocalInstallationStateInput").value' in script
    assert 'pageindex_local_repo_dir: $("#pageindexLocalRepoDirInput").value.trim()' in script
    assert 'pageindex_local_python_path: $("#pageindexLocalPythonPathInput").value.trim()' in script
    assert 'pageindex_local_script_path: $("#pageindexLocalScriptPathInput").value.trim()' in script


def test_client_renders_ocr_page_and_import_strategy_controls():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'data-view="ocr"' in html
    assert 'ocr: "OCR"' in script
    assert "function renderOcr" in script
    assert "function loadOcrData" in script
    assert '"/api/ocr/cache"' in script
    assert '"/api/pageindex-local/status"' in script
    assert 'id="importStrategyInput"' in script
    assert 'strategy_override: importStrategy()' in script
    assert 'searchParams.set("strategy_override", importStrategy())' in script
    assert "async function invalidateOcrCache" in script
    assert "async function rerunOcrCache" in script
    assert "async function retryOcrCache" in script
    assert "data-ocr-invalidate" in script
    assert "data-ocr-rerun" in script
    assert "data-ocr-retry" in script


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
