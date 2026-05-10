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
    assert 'data-view="usage"' in html


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


def test_client_job_details_preserves_log_scroll_during_refresh():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "function jobLogScrollSnapshot" in script
    assert "function restoreJobLogScroll" in script
    assert "const logScroll = jobLogScrollSnapshot();" in script
    assert "restoreJobLogScroll(logScroll);" in script
    assert "log.scrollTop = snapshot.top;" in script


def test_client_ask_persists_and_reopens_chat_sessions():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

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
    assert "session-workspace" in script
    assert "sessionThread" in script
    assert "function renderMarkdownSafe" in script
    assert 'https://cdn.jsdelivr.net/npm/marked@13.0.2/marked.min.js' not in html
    assert 'https://cdn.jsdelivr.net/npm/dompurify@3.1.6/dist/purify.min.js' not in html
    assert 'body.sessions-mode .session-workspace {' in styles
    assert 'grid-template-columns: 248px minmax(0, 1fr) 280px;' in styles
    assert 'session-list-time' in script
    assert 'session-list-preview' not in script
    assert 'session-list-meta' not in script
    assert 'sessionPreview' not in script


def test_client_ask_streams_answers_and_renders_references():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "async function streamQuery" in script
    assert '"/api/query/stream"' in script
    assert "response.body.getReader()" in script
    assert "function handleQueryStreamEvent" in script
    assert 'case "delta"' in script
    assert 'case "done"' in script
    assert "function renderQueryReferences" in script
    assert "state.activeQueryReferences" in script
    assert "Referenced files" in script
    assert 'state.streamingAssistantText = next;' in script
    assert 'if (state.view === "sessions") renderSessionThread();' in script


def test_client_uses_local_safe_markdown_renderer_for_chat_messages():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "function renderMarkdownSafe" in script
    assert "function renderMarkdown" in script
    assert 'renderMarkdownSafe(message.content || "")' in script


def test_client_startup_defers_noncritical_knowledge_data():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "async function loadStartupKnowledgeData" in script
    assert "async function loadDeferredKnowledgeData" in script
    assert "await loadStartupKnowledgeData();" in script
    assert "loadDeferredKnowledgeData().then" in script


def test_client_input_handlers_preserve_ime_composition():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "function isComposingInput" in script
    assert "event?.isComposing" in script
    assert "event?.keyCode === 229" in script
    assert script.count("if (isComposingInput(event)) return;") >= 4
    assert script.count('&& !isComposingInput(event)') >= 2


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


def test_client_renders_llm_usage_page_and_export_action():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'usage: "LLM Usage"' in script
    assert "function renderLlmUsage" in script
    assert '"/api/llm-usage"' in script
    assert '"/api/llm-usage/export"' in script
    assert 'data-action="usage-page"' in script
    assert 'class="llm-usage-toolbar"' in script
    assert 'class="llm-usage-summary"' in script


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
    assert "data-source-date" in script
    assert "data-source-clear-date" in script
    assert "function todaySourceDate" in script
    assert "function sourceDateValue" in script
    assert "No sources ingested on this date" in script
    assert "Show all dates" in script
    assert "data-source-select" in script
    assert "data-source-open-page" in script
    assert "data-delete-source" in script
    assert "async function deleteSourceDocument" in script
    assert "DELETE" in script
    assert "delete_source" in script
    assert ".sources-layout" in styles
    assert ".source-browser-controls" in styles
    assert ".source-browser-meta" in styles
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


def test_client_settings_uses_model_pool_probe_instead_of_test_llm_button():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'id="testLlmBtn"' not in script
    assert 'addEventListener("click", testLlm);' not in script
    assert 'data-action="model-probe"' in script
    assert 'id="probeAllModelPoolBtn"' in script
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


def test_client_wiki_markdown_preview_supports_tables():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert "function splitMarkdownTableRow" in script
    assert "function isMarkdownTableDivider" in script
    assert "function renderMarkdownTable" in script
    assert "<thead><tr>" in script
    assert "<tbody>" in script
    assert ".wiki-preview-pane table" in styles
    assert ".wiki-preview-pane th," in styles
    assert ".wiki-preview-pane td" in styles


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


def test_client_settings_uses_model_pool_profile_dialogs():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert "renderModelProfileDialog" in script
    assert "openModelProfileDialog" in script
    assert "saveModelPoolProfile" in script
    assert "deleteModelPoolProfile" in script
    assert 'onclick="event.stopPropagation()"' not in script
    assert 'id="modelProfileNameInput"' in script
    assert 'id="modelProfileModelsInput"' in script
    assert 'id="toggleApiKeyBtn"' in script
    assert '"/api/model-pool/profiles"' in script
    assert '`/api/model-pool/profiles/${encodeURIComponent(profileId)}`' in script
    assert "models: parseModelRows" in script
    assert "data-action=\"model-delete\"" in script
    assert "data-action=\"model-profile-close\"" in script
    assert "function toggleApiKeyVisibility" in script
    assert 'apiKeyInput.type === "password" ? "text" : "password"' in script
    assert "function renderProfileList" not in script
    assert 'data-action="model-active"' not in script
    assert "Set Active" not in script
    assert "Active profile" not in script
    assert "renderModelProfileEditor" not in script
    assert ".model-profile-dialog" in styles
    assert ".model-dialog-backdrop" in styles


def test_client_settings_supports_deepseek_profile_fields_and_manual_probe_only():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'id="modelProfileProviderInput"' in script
    assert 'value="deepseek"' in script
    assert 'id="modelProfileReasoningEffortInput"' in script
    assert 'id="modelProfileThinkingEnabledInput"' in script
    assert 'provider: $("#modelProfileProviderInput")?.value || "generic"' in script
    assert 'reasoning_effort: $("#modelProfileReasoningEffortInput")?.value || ""' in script
    assert 'thinking_enabled: $("#modelProfileThinkingEnabledInput") ? $("#modelProfileThinkingEnabledInput").checked : false' in script
    assert "function autoProbeModelPool" not in script
    assert "setInterval(autoProbeModelPool, 60000);" not in script


def test_client_settings_restores_general_without_llm_profile_editor():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert '["general", "General"]' in script
    assert "function renderGeneralSettings" in script
    assert 'id="saveSettingsBtn"' in script
    assert 'id="languageInput"' in script
    assert 'id="compileConcurrencyInput"' in script
    assert 'id="ocrEnabledInput"' in script
    assert 'id="pageindexLocalRepoDirInput"' in script
    assert 'id="paddleocrTokenInput"' in script
    assert 'id="profileNameInput"' not in script
    assert 'id="saveProfileBtn"' not in script
    assert 'id="saveNewProfileBtn"' not in script
    assert "LLM Profiles" not in script


def test_client_settings_renders_model_pool_cards_and_probe_actions():
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert "modelPool: null" in script
    assert 'settingsTab: "model-pool"' in script
    assert "function loadModelPool" in script
    assert "function renderModelPool" in script
    assert "function renderModelPoolCard" in script
    assert "function probeModelPoolProfile" in script
    assert "function probeAllModelPool" in script
    assert '"/api/model-pool"' in script
    assert '`/api/model-pool/profiles/${encodeURIComponent(profileId)}/probe`' in script
    assert "trackJob(result.job, \"Model pool probe queued\")" not in script
    assert "state.modelPool = result.model_pool || state.modelPool" in script
    assert '["general", "General"]' in script
    assert 'data-model-pool-search' in script
    assert 'data-model-health-filter' in script
    assert 'id="modelPoolEnabledInput"' in script
    assert 'id="saveModelPoolSettingsBtn"' in script
    assert "saveModelPoolSettings" in script
    assert 'class="model-pool-grid"' in script
    assert ".settings-tabs" in styles
    assert ".model-pool-toolbar" in styles
    assert ".model-pool-grid" in styles
    assert ".model-pool-card" in styles
    assert "profile.routes" in script
    assert ".model-health-dot" in styles


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

