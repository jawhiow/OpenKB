const state = {
  view: "overview",
  kbDir: null,
  status: null,
  documents: null,
  llmUsage: null,
  ocrCache: null,
  modelPool: null,
  pageindexLocalStatus: null,
  modelPoolSearch: "",
  modelPoolHealthFilter: "all",
  modelProfileDialog: null,
  llmUsageSearch: "",
  sourceSearch: "",
  selectedSourceHash: null,
  wikiTree: [],
  wikiOpenDirs: {},
  selectedWikiPath: "index.md",
  wikiDirectory: "",
  wikiSearch: "",
  wikiFileCache: {},
  wikiDrafts: {},
  wikiLoadingPath: null,
  selectedReport: null,
  reportPreview: { path: null, content: "" },
  reportPreviewLoading: null,
  fixPlan: null,
  selectedFixes: {},
  lastFixApply: null,
  chats: [],
  jobs: [],
  selectedJobId: null,
  config: null,
  queryJobId: null,
  activeChatSessionId: null,
  activeChatSession: null,
  activeQueryReferences: [],
  jobStatuses: {},
  loadingAll: false,
  ui: {
    utilityTab: "jobs",
    utilityCollapsed: false,
    jobFilter: "all",
    wikiMode: "preview",
    settingsTab: "model-pool",
    pagination: {},
  },
};

const $ = (selector) => document.querySelector(selector);
const mainView = $("#mainView");
const viewTitle = $("#viewTitle h2");
const viewMeta = $("#viewMeta");
let lastModelPoolAutoProbeAt = 0;

const viewLabels = {
  overview: "Overview",
  documents: "Documents",
  sources: "Sources",
  ocr: "OCR",
  wiki: "Wiki",
  sessions: "Sessions",
  usage: "LLM Usage",
  reports: "Quality",
  settings: "Settings",
};

const jobLabels = {
  add: "Add",
  delete_source: "Delete",
  lint: "Lint",
  lint_fix_plan: "Fix Plan",
  lint_fix_apply: "Apply Fixes",
  model_pool_probe: "Model Probe",
  query: "Query",
};

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function badge(status) {
  const cls = status === "succeeded" ? "good" : status === "failed" || status === "stopped" ? "bad" : status === "running" || status === "stopping" ? "warn" : "muted";
  return `<span class="badge ${cls}">${escapeHTML(status)}</span>`;
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatDateTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function poolProfileById(profileId) {
  return state.modelPool?.profiles?.find((profile) => profile.id === profileId) || state.config?.profiles?.find((profile) => profile.id === profileId) || null;
}

function jobProgress(job) {
  const current = Math.max(Number(job.progress?.current || 0), 0);
  const total = Math.max(Number(job.progress?.total || 0), 0);
  const pct = total ? Math.min(100, Math.round((current / total) * 100)) : job.status === "succeeded" ? 100 : 0;
  return { current, total, pct };
}

function progressMarkup(job) {
  const progress = jobProgress(job);
  const indeterminate = ["running", "stopping"].includes(job.status) && !progress.total;
  const width = indeterminate ? 38 : progress.pct;
  const label = progress.total ? `${progress.current}/${progress.total}` : indeterminate ? job.status : `${progress.pct}%`;
  return `
    <div class="progress-line" title="${escapeHTML(label)}">
      <div class="progress" aria-label="Job progress">
        <span class="progress-bar${indeterminate ? " indeterminate" : ""}" style="width: ${width}%"></span>
      </div>
      <span class="progress-label">${escapeHTML(label)}</span>
    </div>
  `;
}

function lastLogMessage(job) {
  const logs = job.logs || [];
  return logs.length ? logs[logs.length - 1].message : "";
}

function resultSummary(job) {
  if (job.error) return job.error;
  if (job.result?.added !== undefined && job.result?.failed) return `${job.result.added} added, ${job.result.failed} failed`;
  if (job.result?.added !== undefined) return `${job.result.added} file(s) added`;
  if (job.result?.candidates) return `${job.result.candidates.length} fix candidate(s)`;
  if (job.result?.created) return `${job.result.created.length} draft page(s) created`;
  if (job.result?.report) return job.result.report;
  if (job.result?.answer) return "Answer ready";
  return job.message || lastLogMessage(job) || job.id;
}

function notify(message, type = "info") {
  const host = $("#toastHost");
  if (!host || !message) return;
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.textContent = message;
  host.appendChild(toast);
  window.setTimeout(() => {
    toast.classList.add("leaving");
    window.setTimeout(() => toast.remove(), 180);
  }, 3600);
}

function setButtonBusy(button, busy, label = "Working...") {
  if (!button) return;
  if (busy) {
    if (!button.dataset.idleLabel) button.dataset.idleLabel = button.textContent;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    button.classList.add("is-busy");
    button.textContent = label;
    return;
  }
  button.disabled = false;
  button.removeAttribute("aria-busy");
  button.classList.remove("is-busy");
  if (button.dataset.idleLabel) {
    button.textContent = button.dataset.idleLabel;
    delete button.dataset.idleLabel;
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: options.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    ...options,
  });
  const raw = await response.text();
  let body = null;
  if (raw) {
    try {
      body = JSON.parse(raw);
    } catch (_) {
      body = null;
    }
  }
  if (!response.ok) {
    let message = response.statusText;
    if (body && typeof body === "object") {
      message = body.detail || message;
    } else if (raw) {
      message = raw;
    }
    throw new Error(message);
  }
  return body;
}

function withKb(path) {
  if (!state.kbDir) return path;
  const url = new URL(path, window.location.origin);
  url.searchParams.set("kb_dir", state.kbDir);
  return `${url.pathname}${url.search}`;
}

function setError(message) {
  $("#healthBadge").textContent = "Error";
  $("#healthBadge").className = "badge bad";
  viewMeta.textContent = message;
  notify(message, "error");
}

async function loadKnowledgeData() {
  if (!state.kbDir) return;
  const [status, documents, tree, chats, config, ocrCache, pageindexLocalStatus, modelPool] = await Promise.all([
    api(withKb("/api/status")),
    api(withKb("/api/documents")),
    api(withKb("/api/wiki/tree")),
    api(withKb("/api/chats")),
    api(withKb("/api/config")),
    api(withKb("/api/ocr/cache")),
    api(withKb("/api/pageindex-local/status")),
    api(withKb("/api/model-pool")),
  ]);
  state.status = status;
  state.documents = documents;
  state.wikiTree = tree.files || [];
  state.chats = chats.sessions || [];
  state.config = config;
  state.ocrCache = ocrCache;
  state.pageindexLocalStatus = pageindexLocalStatus;
  state.modelPool = modelPool;
}

async function loadModelPool() {
  if (!state.kbDir) return;
  state.modelPool = await api(withKb("/api/model-pool"));
}

async function autoProbeModelPool() {
  if (!state.kbDir || !state.modelPool?.profiles?.length) return;
  const interval = Math.max(Number(state.modelPool.probe_interval_seconds || 600), 60) * 1000;
  if (Date.now() - lastModelPoolAutoProbeAt < interval) return;
  lastModelPoolAutoProbeAt = Date.now();
  try {
    await api("/api/model-pool/probe", {
      method: "POST",
      body: JSON.stringify({ kb_dir: state.kbDir }),
    });
  } catch (_) {
    // Automatic probes stay quiet; explicit probes surface errors via notifications.
  }
}

async function loadOcrData() {
  if (!state.kbDir) return;
  const [ocrCache, pageindexLocalStatus] = await Promise.all([
    api(withKb("/api/ocr/cache")),
    api(withKb("/api/pageindex-local/status")),
  ]);
  state.ocrCache = ocrCache;
  state.pageindexLocalStatus = pageindexLocalStatus;
}

async function loadLlmUsage(options = {}) {
  if (!state.kbDir) return;
  const pageState = state.ui.pagination["usage"] || {};
  const page = Number(options.page || pageState.page || 1);
  const pageSize = Number(options.pageSize || pageState.pageSize || 50);
  const url = new URL(withKb("/api/llm-usage"), window.location.origin);
  url.searchParams.set("q", state.llmUsageSearch || "");
  url.searchParams.set("page", String(page));
  url.searchParams.set("page_size", String(pageSize));
  state.llmUsage = await api(`${url.pathname}${url.search}`);
  state.ui.pagination["usage"] = {
    ...(state.ui.pagination["usage"] || {}),
    page: state.llmUsage.page || page,
    pageSize: state.llmUsage.page_size || pageSize,
  };
}

async function refreshKnowledgeData() {
  if (!state.kbDir) return;
  try {
    await loadKnowledgeData();
    render();
  } catch (error) {
    setError(error.message);
  }
}

async function loadAll(event) {
  const button = event?.currentTarget;
  setButtonBusy(button, true, "Refreshing...");
  state.loadingAll = true;
  try {
    const health = await api("/api/health");
    state.kbDir = health.default_kb;
    $("#healthBadge").textContent = "Ready";
    $("#healthBadge").className = "badge good";
    $("#kbLabel").textContent = state.kbDir || "No KB selected";

    if (state.kbDir) {
      await loadKnowledgeData();
      if (state.view === "usage") {
        await loadLlmUsage();
      }
    } else {
      state.status = null;
      state.documents = null;
      state.llmUsage = null;
      state.ocrCache = null;
      state.modelPool = null;
      state.pageindexLocalStatus = null;
      state.wikiTree = [];
      state.chats = [];
      state.config = null;
    }
    await loadJobs();
    render();
  } catch (error) {
    setError(error.message);
    render();
  } finally {
    state.loadingAll = false;
    setButtonBusy(button, false);
  }
}

async function loadJobs() {
  try {
    const previousStatuses = { ...state.jobStatuses };
    const jobs = await api("/api/jobs");
    state.jobs = jobs.jobs || [];
    state.jobStatuses = Object.fromEntries(state.jobs.map((job) => [job.id, job.status]));
    if (state.selectedJobId && !state.jobs.some((job) => job.id === state.selectedJobId)) {
      state.selectedJobId = state.jobs[0]?.id || null;
    }
    if (!state.selectedJobId && state.jobs.length) {
      state.selectedJobId = state.jobs[0].id;
    }
    renderJobsPanel();
    handleJobTransitions(previousStatuses);
    handleQueryJob();
  } catch (_) {
    state.jobs = [];
    renderJobsPanel();
  }
}

function handleJobTransitions(previousStatuses) {
  let shouldRefresh = false;
  state.jobs.forEach((job) => {
    const previous = previousStatuses[job.id];
    if (!["running", "stopping"].includes(previous) || ["running", "stopping"].includes(job.status)) return;
    if (job.status === "succeeded") {
      const hasFailures = Number(job.result?.failed || 0) > 0;
      notify(`${jobLabels[job.type] || job.type} finished${hasFailures ? " with failures" : ""}`, hasFailures ? "warning" : "success");
      if (job.type === "lint_fix_plan") {
        captureFixPlan(job.result);
      }
      if (job.type === "lint_fix_apply") {
        state.lastFixApply = job.result || null;
      }
      shouldRefresh = shouldRefresh || ["add", "delete_source", "lint", "query", "lint_fix_apply", "model_pool_probe"].includes(job.type);
    } else if (job.status === "failed") {
      notify(job.error || `${jobLabels[job.type] || job.type} failed`, "error");
    } else if (job.status === "stopped") {
      notify(`${jobLabels[job.type] || job.type} stopped`, "warning");
    }
  });
  if (shouldRefresh && !state.loadingAll) {
    if (state.view === "usage") {
      loadLlmUsage().then(() => {
        if (state.view === "usage") renderLlmUsage();
      }).catch(() => {});
    }
    refreshKnowledgeData();
  }
}

function paginationState(key, total = 0, pageSize = 50) {
  const current = state.ui.pagination[key] || {};
  const size = Math.max(Number(current.pageSize || pageSize), 1);
  const pages = Math.max(Math.ceil(total / size), 1);
  const page = Math.min(Math.max(Number(current.page || 1), 1), pages);
  state.ui.pagination[key] = { ...current, page, pageSize: size };
  return { key, page, pageSize: size, pages, total };
}

function paginatedItems(items, key, pageSize = 50) {
  const list = Array.isArray(items) ? items : [];
  const meta = paginationState(key, list.length, pageSize);
  const start = (meta.page - 1) * meta.pageSize;
  const end = Math.min(start + meta.pageSize, list.length);
  return {
    items: list.slice(start, end),
    meta: {
      ...meta,
      start: list.length ? start + 1 : 0,
      end,
    },
  };
}

function setPage(key, page) {
  const current = state.ui.pagination[key] || {};
  state.ui.pagination[key] = { ...current, page: Number(page) || 1 };
}

function resetPage(key) {
  setPage(key, 1);
}

function renderPager(meta, actionPrefix) {
  if (!meta || meta.total <= meta.pageSize) return "";
  return `
    <div class="table-pager">
      <span>Showing ${escapeHTML(meta.start)}-${escapeHTML(meta.end)} of ${escapeHTML(meta.total)}</span>
      <span class="table-pager-actions">
        <button type="button" data-action="${escapeHTML(actionPrefix)}-page" data-page="${escapeHTML(meta.page - 1)}" ${meta.page <= 1 ? "disabled" : ""}>Prev</button>
        <span>Page ${escapeHTML(meta.page)} / ${escapeHTML(meta.pages)}</span>
        <button type="button" data-action="${escapeHTML(actionPrefix)}-page" data-page="${escapeHTML(meta.page + 1)}" ${meta.page >= meta.pages ? "disabled" : ""}>Next</button>
      </span>
    </div>
  `;
}

function renderUsagePager(meta) {
  if (!meta || meta.total <= meta.pageSize) return "";
  return `
    <div class="table-pager">
      <span>Showing ${escapeHTML(meta.start)}-${escapeHTML(meta.end)} of ${escapeHTML(meta.total)}</span>
      <span class="table-pager-actions">
        <button type="button" data-action="usage-page" data-page="${escapeHTML(meta.page - 1)}" ${meta.page <= 1 ? "disabled" : ""}>Prev</button>
        <span>Page ${escapeHTML(meta.page)} / ${escapeHTML(meta.pages)}</span>
        <button type="button" data-action="usage-page" data-page="${escapeHTML(meta.page + 1)}" ${meta.page >= meta.pages ? "disabled" : ""}>Next</button>
      </span>
    </div>
  `;
}

function syncFixPlanFromJobs() {
  if (state.fixPlan?.candidates?.length || !state.selectedReport) return;
  const job = state.jobs.find(
    (item) =>
      item.type === "lint_fix_plan" &&
      item.status === "succeeded" &&
      item.result?.report === state.selectedReport,
  );
  if (job?.result?.candidates) {
    setFixPlanState(job.result);
  }
}

function renderChatTranscript(session = state.activeChatSession) {
  if (!session) return "";
  const turns = session.user_turns || [];
  const answers = session.assistant_texts || [];
  const lines = [];
  if (session.title || session.id) {
    lines.push(`Session ${session.id}${session.title ? ` - ${session.title}` : ""}`);
    lines.push("");
  }
  turns.forEach((question, index) => {
    lines.push(`You: ${question}`);
    if (answers[index]) {
      lines.push("");
      lines.push(`OpenKB: ${answers[index]}`);
    }
    lines.push("");
  });
  return lines.join("\n").trim();
}

function referenceLabel(reference) {
  if (!reference) return "";
  if (reference.type === "source_pages") {
    return `${reference.path} pages ${reference.pages}`;
  }
  return reference.path || "";
}

function renderQueryReferences(references = state.activeQueryReferences) {
  const items = Array.isArray(references) ? references : [];
  if (!items.length) return "";
  return [
    "",
    "Referenced files",
    ...items.map((reference) => `- ${referenceLabel(reference)}`),
  ].join("\n");
}

function renderAssistantAnswer(text = "", references = state.activeQueryReferences) {
  const parts = [text || renderChatTranscript(), renderQueryReferences(references)].filter(Boolean);
  $("#answerBox").textContent = parts.join("\n");
}

function setActiveChatSession(session) {
  state.activeChatSession = session || null;
  state.activeChatSessionId = session?.id || null;
  if ($("#answerBox")) renderAssistantAnswer();
}

async function openChatSession(sessionId) {
  if (!sessionId) return;
  try {
    const session = await api(withKb(`/api/chats/${encodeURIComponent(sessionId)}`));
    setActiveChatSession(session);
    state.ui.utilityTab = "assistant";
    renderUtilityPanel();
    switchView("sessions");
    notify("Continue in Assistant", "success");
  } catch (error) {
    setError(error.message);
  }
}

function handleQueryJob() {
  if (!state.queryJobId) return;
  const job = state.jobs.find((item) => item.id === state.queryJobId);
  if (!job) return;
  if (job.status === "succeeded") {
    state.activeChatSessionId = job.result?.session_id || state.activeChatSessionId;
    state.activeChatSession = job.result?.session || state.activeChatSession;
    state.activeQueryReferences = job.result?.references || [];
    renderAssistantAnswer(renderChatTranscript() || job.result?.answer || "");
    state.queryJobId = null;
    loadKnowledgeData().then(() => {
      if (state.view === "sessions") renderSessions();
    }).catch(() => {});
  } else if (job.status === "failed" || job.status === "stopped") {
    $("#answerBox").textContent = job.error || "Query failed";
    state.queryJobId = null;
  } else {
    renderQueryProgress(job);
  }
}

function renderQueryProgress(job) {
  const progress = jobProgress(job);
  const logs = (job.logs || []).slice(-5).map((entry) => `${formatTime(entry.time)} ${entry.message}`);
  const lines = [
    "Running query...",
    job.message,
    progress.total ? `${progress.current}/${progress.total}` : "",
    ...logs,
  ].filter(Boolean);
  $("#answerBox").textContent = lines.join("\n");
}

function selectJob(jobId) {
  state.selectedJobId = jobId;
  renderJobsPanel();
}

function trackJob(job, message) {
  if (!job) return;
  state.jobs = [job, ...state.jobs.filter((item) => item.id !== job.id)];
  state.jobStatuses[job.id] = job.status;
  state.selectedJobId = job.id;
  state.ui.utilityTab = "jobs";
  renderUtilityPanel();
  notify(message || `${jobLabels[job.type] || job.type} queued`, "info");
}

async function stopJob(jobId) {
  if (!jobId) return;
  try {
    const job = await api(`/api/jobs/${jobId}/stop`, { method: "POST" });
    state.jobs = [job, ...state.jobs.filter((item) => item.id !== job.id)];
    state.jobStatuses[job.id] = job.status;
    state.selectedJobId = job.id;
    renderJobsPanel();
    notify("Stop requested", "warning");
  } catch (error) {
    notify(error.message, "error");
  }
}

async function retryJob(jobId) {
  if (!jobId) return;
  try {
    const result = await api(`/api/jobs/${jobId}/retry`, { method: "POST" });
    trackJob(result.job, "Retry queued");
  } catch (error) {
    notify(error.message, "error");
  }
}

function filteredJobs() {
  const filter = state.ui.jobFilter || "active";
  if (filter === "all") return state.jobs;
  if (filter === "active") return state.jobs.filter((job) => ["running", "stopping"].includes(job.status));
  if (filter === "failed") return state.jobs.filter((job) => ["failed", "stopped"].includes(job.status));
  if (filter === "succeeded") return state.jobs.filter((job) => job.status === "succeeded");
  return state.jobs;
}

function jobFilterButton(filter, label) {
  const active = (state.ui.jobFilter || "active") === filter ? " active" : "";
  const count =
    filter === "all"
      ? state.jobs.length
      : filter === "active"
        ? state.jobs.filter((job) => ["running", "stopping"].includes(job.status)).length
        : filter === "failed"
          ? state.jobs.filter((job) => ["failed", "stopped"].includes(job.status)).length
          : state.jobs.filter((job) => job.status === "succeeded").length;
  return `<button class="${active.trim()}" type="button" data-action="job-filter" data-job-filter="${escapeHTML(filter)}">${escapeHTML(label)} ${escapeHTML(count)}</button>`;
}

function jobLogScrollSnapshot() {
  const log = $("#jobLogList");
  if (!log || !state.selectedJobId) return null;
  const bottomGap = log.scrollHeight - log.clientHeight - log.scrollTop;
  return {
    jobId: state.selectedJobId,
    top: log.scrollTop,
    stickToBottom: bottomGap <= 4,
  };
}

function restoreJobLogScroll(snapshot) {
  if (!snapshot || snapshot.jobId !== state.selectedJobId) return;
  const log = $("#jobLogList");
  if (!log) return;
  if (snapshot.stickToBottom) {
    log.scrollTop = log.scrollHeight;
    return;
  }
  log.scrollTop = snapshot.top;
}

function renderJobsPanel() {
  const list = $("#jobsList");
  if (!list) return;
  $("#jobCount").textContent = String(state.jobs.length);
  if (!state.jobs.length) {
    list.innerHTML = `
      <div class="job-filter-bar">
        ${jobFilterButton("active", "Active")}
        ${jobFilterButton("failed", "Needs attention")}
        ${jobFilterButton("succeeded", "Done")}
        ${jobFilterButton("all", "All")}
      </div>
      <div class="empty">No jobs</div>
    `;
    renderJobDetails();
    return;
  }
  const page = paginatedItems(filteredJobs(), "jobs", 50);
  list.innerHTML = `
    <div class="job-filter-bar">
      ${jobFilterButton("active", "Active")}
      ${jobFilterButton("failed", "Needs attention")}
      ${jobFilterButton("succeeded", "Done")}
      ${jobFilterButton("all", "All")}
    </div>
    ${
      page.items.length
        ? page.items
    .map((job) => {
      const active = job.id === state.selectedJobId ? " active" : "";
      return `
        <button class="job-item${active}" type="button" data-action="select-job" data-job-id="${escapeHTML(job.id)}">
          <span class="job-line">
            <strong>${escapeHTML(jobLabels[job.type] || job.type)}</strong>
            ${badge(job.status)}
            <span class="job-time">${escapeHTML(formatTime(job.updated_at))}</span>
          </span>
          <span class="job-message">${escapeHTML(resultSummary(job))}</span>
          ${progressMarkup(job)}
        </button>
      `;
    })
    .join("")
        : `<div class="empty">No jobs match this filter</div>`
    }
    ${renderPager(page.meta, "jobs")}
  `;
  renderJobDetails();
}

function renderJobDetails() {
  const details = $("#jobDetails");
  if (!details) return;
  const logScroll = jobLogScrollSnapshot();
  if (!state.jobs.length) {
    details.className = "job-details compact";
    details.innerHTML = `
      <div class="empty">No job activity yet.</div>
      <div id="jobLogList" class="job-log-list hidden"></div>
    `;
    return;
  }
  const job = state.jobs.find((item) => item.id === state.selectedJobId) || state.jobs[0];
  state.selectedJobId = job.id;
  details.className = "job-details compact";
  const logs = job.logs || [];
  const canStop = ["running", "stopping"].includes(job.status);
  const canRetry = !canStop && ["failed", "stopped", "succeeded"].includes(job.status);
  details.innerHTML = `
    <div class="job-detail-head">
      <div>
        <h3>${escapeHTML(jobLabels[job.type] || job.type)}</h3>
        <span class="muted-text">${escapeHTML(job.id)}</span>
      </div>
      ${badge(job.status)}
    </div>
    <div class="job-detail-message">${escapeHTML(resultSummary(job))}</div>
    ${progressMarkup(job)}
    <div class="job-detail-grid">
      <span>Created</span><strong>${escapeHTML(formatTime(job.created_at))}</strong>
      <span>Updated</span><strong>${escapeHTML(formatTime(job.updated_at))}</strong>
      ${job.retry_of ? `<span>Retry of</span><strong>${escapeHTML(job.retry_of)}</strong>` : ""}
    </div>
    <div class="job-detail-actions row-actions">
      ${canStop ? `<button type="button" class="danger" data-action="job-stop" data-job-stop="${escapeHTML(job.id)}">Stop</button>` : ""}
      ${canRetry ? `<button type="button" data-action="job-retry" data-job-retry="${escapeHTML(job.id)}">Retry</button>` : ""}
    </div>
    <div id="jobLogList" class="job-log-list">
      ${
        logs.length
          ? logs
              .map(
                (entry) => `
                  <div class="job-log ${escapeHTML(entry.level || "info")}">
                    <span>${escapeHTML(formatTime(entry.time))}</span>
                    <strong>${escapeHTML(entry.level || "info")}</strong>
                    <p>${escapeHTML(entry.message)}</p>
                  </div>
                `,
              )
              .join("")
          : `<div class="empty">No logs yet.</div>`
      }
    </div>
  `;
  restoreJobLogScroll(logScroll);
}

function renderUtilityPanel() {
  const activeTab = state.ui.utilityTab || "jobs";
  $("#utilityJobsTab")?.classList.toggle("active", activeTab === "jobs");
  $("#utilityAssistantTab")?.classList.toggle("active", activeTab === "assistant");
  $("#jobsPanel")?.classList.toggle("active", activeTab === "jobs");
  $("#assistantPanel")?.classList.toggle("active", activeTab === "assistant");
  renderJobsPanel();
  if (activeTab === "assistant" && state.activeChatSession) {
    renderAssistantAnswer();
  }
}

function render() {
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.view === state.view);
  });
  viewTitle.textContent = viewLabels[state.view] || state.view;
  viewMeta.textContent = state.kbDir || "No KB selected";

  if (!state.kbDir && state.view !== "settings") {
    mainView.innerHTML = `
      <div class="empty">
        <button class="primary" type="button" data-switch-settings>Open Settings</button>
      </div>
    `;
    mainView.querySelector("[data-switch-settings]").addEventListener("click", () => switchView("settings"));
    return;
  }

  renderMainView();
  renderUtilityPanel();
}

function renderMainView() {
  if (state.view === "overview") renderOverview();
  if (state.view === "documents") renderDocuments();
  if (state.view === "sources") renderSources();
  if (state.view === "ocr") renderOcr();
  if (state.view === "wiki") renderWiki();
  if (state.view === "sessions") renderSessions();
  if (state.view === "usage") renderLlmUsage();
  if (state.view === "reports") renderReports();
  if (state.view === "settings") renderSettings();
}

function renderOverview() {
  const dirs = state.status?.directories || {};
  const cfg = state.config || {};
  const pool = state.modelPool || {};
  const healthyRoutes = (pool.profiles || []).reduce(
    (count, profile) => count + (profile.routes || []).filter((route) => route.health === "healthy").length,
    0,
  );
  mainView.innerHTML = `
    <div class="stats-grid">
      ${stat("Indexed", state.status?.total_indexed ?? 0)}
      ${stat("Raw", dirs.raw ?? 0)}
      ${stat("Summaries", dirs.summaries ?? 0)}
      ${stat("Companies", dirs.companies ?? 0)}
      ${stat("Concepts", dirs.concepts ?? 0)}
      ${stat("Reports", dirs.reports ?? 0)}
    </div>
    <div class="workbench-grid">
      <section class="section">
        <header>
          <div>
            <h3>Corpus</h3>
            <span class="muted-text">${escapeHTML(state.status?.last_compile || "No compile yet")}</span>
          </div>
          <button type="button" data-view-target="documents">Open</button>
        </header>
        <div class="section-body">${documentsTable(6)}</div>
      </section>
      <section class="section">
        <header>
          <div>
            <h3>Investment Map</h3>
            <span class="muted-text">Companies, themes, metrics, risks</span>
          </div>
          <button type="button" data-view-target="wiki">Open</button>
        </header>
        <div class="section-body">${investmentMap(dirs)}</div>
      </section>
      <section class="section">
        <header>
          <div>
            <h3>Quality</h3>
            <span class="muted-text">${escapeHTML(state.status?.last_lint || "No lint yet")}</span>
          </div>
          <button type="button" data-view-target="reports">Open</button>
        </header>
        <div class="section-body">${qualitySummary()}</div>
      </section>
      <section class="section">
        <header>
          <div>
            <h3>Runtime</h3>
            <span class="muted-text">${escapeHTML(healthyRoutes)} healthy route(s) in model pool</span>
          </div>
          <span class="badge ${pool.enabled ? "good" : "muted"}">${pool.enabled ? "Pool enabled" : "Pool disabled"}</span>
        </header>
        <div class="section-body runtime-list">
          <div><span>Profiles</span><strong>${escapeHTML(pool.summary?.total || 0)}</strong></div>
          <div><span>Language</span><strong>${escapeHTML(cfg.language || "")}</strong></div>
          <div><span>Strategy</span><strong>${escapeHTML(pool.strategy || "weighted_round_robin")}</strong></div>
        </div>
      </section>
    </div>
  `;
  bindViewButtons();
  bindSourceDocumentActions();
}

function stat(label, value) {
  return `<div class="stat"><span class="muted-text">${escapeHTML(label)}</span><strong>${escapeHTML(value)}</strong></div>`;
}

function investmentMap(dirs) {
  const rows = [
    ["Companies", dirs.companies ?? 0],
    ["Industries", dirs.industries ?? 0],
    ["Themes", dirs.themes ?? 0],
    ["Metrics", dirs.metrics ?? 0],
    ["Risks", dirs.risks ?? 0],
    ["Concepts", dirs.concepts ?? 0],
  ];
  return `
    <div class="metric-list">
      ${rows.map(([label, value]) => `<div><span>${escapeHTML(label)}</span><strong>${escapeHTML(value)}</strong></div>`).join("")}
    </div>
  `;
}

function qualitySummary() {
  const reports = state.documents?.reports || [];
  const newest = reports[reports.length - 1] || "";
  return `
    <div class="quality-overview">
      <div><span>Reports</span><strong>${escapeHTML(reports.length)}</strong></div>
      <div><span>Latest</span><strong>${escapeHTML(newest || "None")}</strong></div>
      <div><span>Fix plan</span><strong>${escapeHTML(state.fixPlan?.candidates?.length ?? 0)} candidate(s)</strong></div>
    </div>
    ${jobsSummary(3)}
  `;
}

const sourceRelationGroupLabels = [
  ["summaries", "Summaries"],
  ["companies", "Companies"],
  ["industries", "Industries"],
  ["themes", "Themes"],
  ["metrics", "Metrics"],
  ["risks", "Risks"],
  ["concepts", "Concepts"],
];

function sourceDocuments() {
  return state.documents?.documents || [];
}

function sourceRelatedCount(doc) {
  if (doc?.related_count !== undefined && doc?.related_count !== null) {
    const explicit = Number(doc.related_count);
    if (Number.isFinite(explicit)) return explicit;
  }
  return Object.values(doc?.related_pages || {}).reduce((total, pages) => total + (Array.isArray(pages) ? pages.length : 0), 0);
}

function documentsTable(limit) {
  const allDocs = state.documents?.documents || [];
  const page = limit
    ? { items: allDocs.slice(0, limit), meta: null }
    : paginatedItems(allDocs, "documents", 50);
  const docs = page.items;
  if (!docs.length) return `<div class="empty">No documents</div>`;
  return `
    <div class="data-table-shell">
      <div class="data-grid-table documents-table">
        <table>
          <thead><tr><th>Name</th><th>Type</th><th>Pages</th><th>Wiki Pages</th><th>Actions</th></tr></thead>
          <tbody>
            ${docs
              .map((doc) => {
                const relatedCount = sourceRelatedCount(doc);
                return `
                  <tr>
                    <td>${escapeHTML(doc.name)}</td>
                    <td>${escapeHTML(doc.type)}</td>
                    <td>${escapeHTML(doc.pages || "")}</td>
                    <td>
                      <button class="text-button" type="button" data-source-focus="${escapeHTML(doc.hash)}">
                        ${escapeHTML(relatedCount)} page(s)
                      </button>
                    </td>
                    <td class="source-actions">
                      <button type="button" data-source-focus="${escapeHTML(doc.hash)}">Open</button>
                      <button
                        class="danger"
                        type="button"
                        data-delete-source="${escapeHTML(doc.hash)}"
                        data-source-name="${escapeHTML(doc.name)}"
                        data-related-count="${escapeHTML(relatedCount)}"
                      >Delete</button>
                    </td>
                  </tr>
                `;
              })
              .join("")}
          </tbody>
        </table>
      </div>
      ${page.meta ? renderPager(page.meta, "documents") : ""}
    </div>
  `;
}

function jobsSummary(limit = 5) {
  if (!state.jobs.length) return `<div class="empty">No jobs</div>`;
  return state.jobs
    .slice(0, limit)
    .map(
      (job) => `
        <div class="job-item compact">
          <span class="job-line"><strong>${escapeHTML(jobLabels[job.type] || job.type)}</strong>${badge(job.status)}</span>
          <span class="job-message">${escapeHTML(resultSummary(job))}</span>
          ${progressMarkup(job)}
        </div>
      `,
    )
    .join("");
}

function renderDocuments() {
  mainView.innerHTML = `
    <div class="documents-layout">
      <section class="section add-source-panel">
        <header><h3>Add Source</h3></header>
        <div class="section-body add-source-grid">
          <div class="field">
            <label for="addPathInput">Path</label>
            <input id="addPathInput" type="text" placeholder="D:\\path\\to\\folder-or-document.pdf" />
          </div>
          <div class="field">
            <label for="importStrategyInput">Import Strategy</label>
            <select id="importStrategyInput">
              <option value="">auto</option>
              <option value="plain-local-long">plain-local-long</option>
              <option value="ocr-local-long">ocr-local-long</option>
              <option value="ocr-pageindex-local">ocr-pageindex-local</option>
            </select>
          </div>
          <div class="row-actions add-source-actions">
            <button id="addPathBtn" class="primary" type="button">Add Folder / File</button>
          </div>
          <div class="field">
            <label for="uploadInput">File</label>
            <input id="uploadInput" type="file" multiple />
          </div>
          <div class="row-actions add-source-actions">
            <button id="uploadBtn" type="button">Upload</button>
          </div>
        </div>
      </section>
      <section class="section">
        <header>
          <div>
            <h3>Indexed</h3>
            <span class="muted-text">${escapeHTML(sourceDocuments().length)} source file(s)</span>
          </div>
          <button type="button" data-view-target="sources">Relations</button>
        </header>
        <div class="section-body">${documentsTable()}</div>
      </section>
    </div>
  `;
  $("#addPathBtn").addEventListener("click", addPath);
  $("#uploadBtn").addEventListener("click", uploadFile);
  bindViewButtons();
  bindSourceDocumentActions();
}

function importStrategy() {
  return $("#importStrategyInput")?.value || "";
}

function bindSourceDocumentActions(root = mainView) {
  root.querySelectorAll("[data-source-focus]").forEach((button) => {
    button.addEventListener("click", () => openSourceWorkbench(button.dataset.sourceFocus));
  });
  root.querySelectorAll("[data-delete-source]").forEach((button) => {
    button.addEventListener("click", deleteSourceDocument);
  });
}

function filteredSourceDocuments() {
  const query = state.sourceSearch.trim().toLowerCase();
  const docs = sourceDocuments();
  if (!query) return docs;
  return docs.filter((doc) => {
    const haystack = [
      doc.name,
      doc.stem,
      doc.type,
      doc.hash,
      doc.source_summary,
      doc.source_path,
      Object.values(doc.related_pages || {})
        .flat()
        .map((page) => page.path)
        .join(" "),
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return haystack.includes(query);
  });
}

function syncSelectedSource(docs = sourceDocuments()) {
  if (!docs.length) {
    state.selectedSourceHash = null;
    return null;
  }
  const selected = docs.find((doc) => doc.hash === state.selectedSourceHash);
  if (selected) return selected;
  state.selectedSourceHash = docs[0].hash;
  return docs[0];
}

function selectSourceDocument(hash) {
  state.selectedSourceHash = hash;
  renderSources();
}

function openSourceWorkbench(hash) {
  state.selectedSourceHash = hash || state.selectedSourceHash;
  state.view = "sources";
  render();
}

function sourceDocumentList(docs) {
  if (!sourceDocuments().length) return `<div class="empty">No documents</div>`;
  if (!docs.length) return `<div class="empty">No matching sources</div>`;
  const page = paginatedItems(docs, "sources", 50);
  return `
    ${page.items
      .map((doc) => {
        const active = doc.hash === state.selectedSourceHash ? " active" : "";
        const rawBadge = doc.raw_exists === false ? `<span class="badge warn">raw missing</span>` : `<span class="badge muted">${escapeHTML(doc.type || "unknown")}</span>`;
        return `
          <button class="source-list-item${active}" type="button" data-source-select="${escapeHTML(doc.hash)}">
            <span class="source-list-name">${escapeHTML(doc.name)}</span>
            <span class="source-list-meta">
              ${rawBadge}
              <span>${escapeHTML(sourceRelatedCount(doc))} page(s)</span>
            </span>
          </button>
        `;
      })
      .join("")}
    ${renderPager(page.meta, "sources")}
  `;
}

function sourceRelationGroups(doc) {
  const related = doc?.related_pages || {};
  const groups = sourceRelationGroupLabels
    .map(([key, label]) => ({
      key,
      label,
      pages: related[key] || [],
    }))
    .filter((group) => group.pages.length);
  if (!groups.length) return `<div class="empty">No related wiki pages</div>`;
  return groups
    .map(
      (group) => `
        <section class="relation-group">
          <header>
            <h3>${escapeHTML(group.label)}</h3>
            <span class="badge muted">${escapeHTML(group.pages.length)}</span>
          </header>
          <div class="relation-list">
            ${group.pages
              .map(
                (page) => `
                  <button class="relation-row" type="button" data-source-open-page="${escapeHTML(page.path)}">
                    <span>
                      <strong>${escapeHTML(page.title || page.page || page.path)}</strong>
                      <small>${escapeHTML(page.path)}</small>
                    </span>
                    ${page.shared ? `<span class="badge info">shared</span>` : `<span class="badge muted">owned</span>`}
                  </button>
                `,
              )
              .join("")}
          </div>
        </section>
      `,
    )
    .join("");
}

function renderSources() {
  const filtered = filteredSourceDocuments();
  const hasSearch = Boolean(state.sourceSearch.trim());
  const selected = filtered.length ? syncSelectedSource(filtered) : hasSearch ? null : syncSelectedSource(sourceDocuments());
  const totalRelated = sourceDocuments().reduce((total, doc) => total + sourceRelatedCount(doc), 0);
  mainView.innerHTML = `
    <div class="sources-layout">
      <aside class="source-browser">
        <div class="source-browser-head">
          <div>
            <strong>Sources</strong>
            <span>${escapeHTML(sourceDocuments().length)} file(s), ${escapeHTML(totalRelated)} page(s)</span>
          </div>
          <input data-source-search type="search" placeholder="Search sources or pages" value="${escapeHTML(state.sourceSearch)}" />
        </div>
        <div class="source-list">
          ${sourceDocumentList(filtered)}
        </div>
      </aside>
      <section class="source-detail">
        ${
          selected
            ? `
              <header class="source-detail-head">
                <div>
                  <h3>${escapeHTML(selected.name)}</h3>
                  <span class="muted-text">${escapeHTML(selected.hash)}</span>
                </div>
                <div class="row-actions">
                  <button type="button" data-source-open-page="${escapeHTML(selected.source_summary || "")}" ${selected.summary_exists === false ? "disabled" : ""}>Summary</button>
                  <button
                    class="danger"
                    type="button"
                    data-delete-source="${escapeHTML(selected.hash)}"
                    data-source-name="${escapeHTML(selected.name)}"
                    data-related-count="${escapeHTML(sourceRelatedCount(selected))}"
                  >Delete Source</button>
                </div>
              </header>
              <div class="source-summary-grid">
                <div><span>Type</span><strong>${escapeHTML(selected.type || "unknown")}</strong></div>
                <div><span>Pages</span><strong>${escapeHTML(selected.pages || "")}</strong></div>
                <div><span>Wiki Pages</span><strong>${escapeHTML(sourceRelatedCount(selected))}</strong></div>
                <div><span>Raw</span><strong>${selected.raw_exists === false ? "Missing" : "Present"}</strong></div>
                <div><span>Summary</span><strong>${selected.summary_exists === false ? "Missing" : escapeHTML(selected.source_summary || "")}</strong></div>
                <div><span>Full Text</span><strong>${escapeHTML(selected.source_path || "None")}</strong></div>
              </div>
              <div class="relation-groups">
                ${sourceRelationGroups(selected)}
              </div>
            `
            : `<div class="empty">No source selected</div>`
        }
      </section>
    </div>
  `;
  mainView.querySelector("[data-source-search]")?.addEventListener("input", (event) => {
    state.sourceSearch = event.target.value;
    resetPage("sources");
    renderSources();
    const search = mainView.querySelector("[data-source-search]");
    search?.focus();
    search?.setSelectionRange(search.value.length, search.value.length);
  });
  mainView.querySelectorAll("[data-source-select]").forEach((button) => {
    button.addEventListener("click", () => selectSourceDocument(button.dataset.sourceSelect));
  });
  mainView.querySelectorAll("[data-source-open-page]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!button.dataset.sourceOpenPage) return;
      state.selectedWikiPath = button.dataset.sourceOpenPage;
      switchView("wiki");
    });
  });
  mainView.querySelectorAll("[data-source-focus]").forEach((button) => {
    button.addEventListener("click", () => openSourceWorkbench(button.dataset.sourceFocus));
  });
  mainView.querySelectorAll("[data-delete-source]").forEach((button) => {
    button.addEventListener("click", deleteSourceDocument);
  });
}

async function addPath(event) {
  const button = event?.currentTarget;
  const path = $("#addPathInput").value.trim();
  if (!path) {
    notify("Enter a folder or file path first.", "warning");
    return;
  }
  setButtonBusy(button, true, "Queueing...");
  try {
    const result = await api("/api/documents/add", {
      method: "POST",
      body: JSON.stringify({ kb_dir: state.kbDir, path, strategy_override: importStrategy() }),
    });
    trackJob(result.job, "Add job queued");
  } catch (error) {
    setError(error.message);
  } finally {
    setButtonBusy(button, false);
  }
}

async function uploadFile(event) {
  const button = event?.currentTarget;
  const input = $("#uploadInput");
  if (!input.files.length) {
    notify("Choose a file first.", "warning");
    return;
  }
  const files = Array.from(input.files);
  const form = new FormData();
  Array.from(input.files).forEach((file) => form.append("file", file));
  setButtonBusy(button, true, "Uploading...");
  try {
    const uploadUrl = new URL("/api/documents/upload", window.location.origin);
    uploadUrl.searchParams.set("kb_dir", state.kbDir);
    if (importStrategy()) uploadUrl.searchParams.set("strategy_override", importStrategy());
    const result = await api(`${uploadUrl.pathname}${uploadUrl.search}`, {
      method: "POST",
      body: form,
    });
    input.value = "";
    trackJob(result.job, files.length > 1 ? `${files.length} uploads queued` : "Upload queued");
  } catch (error) {
    setError(error.message);
  } finally {
    setButtonBusy(button, false);
  }
}

function buildWikiDirectory(files) {
  const root = { name: "wiki", path: "", files: [], children: new Map(), count: 0 };
  (files || []).forEach((file) => {
    const parts = String(file.path || "").split("/").filter(Boolean);
    if (!parts.length) return;
    let node = root;
    parts.slice(0, -1).forEach((part) => {
      const childPath = node.path ? `${node.path}/${part}` : part;
      if (!node.children.has(part)) {
        node.children.set(part, { name: part, path: childPath, files: [], children: new Map(), count: 0 });
      }
      node = node.children.get(part);
    });
    node.files.push(file);
  });

  const finalize = (node) => {
    const children = Array.from(node.children.values()).sort((a, b) => a.name.localeCompare(b.name));
    node.files.sort((a, b) => a.name.localeCompare(b.name));
    node.children = children;
    node.count = node.files.length + children.reduce((total, child) => total + finalize(child), 0);
    return node.count;
  };
  finalize(root);
  return root;
}

function renderOcr() {
  const entries = state.ocrCache?.entries || [];
  const runtime = state.pageindexLocalStatus || {};
  mainView.innerHTML = `
    <div class="ocr-workbench">
      <section class="ocr-runtime-strip" aria-label="Local PageIndex">
        <div>
          <h3>Local PageIndex</h3>
          <span class="muted-text">${escapeHTML(runtime.root || "")}</span>
        </div>
        <div class="runtime-pills">
          ${runtime.ready ? `<span class="badge good">ready</span>` : `<span class="badge warn">setup</span>`}
          <span><span>Enabled</span><strong>${runtime.enabled ? "yes" : "no"}</strong></span>
          <span><span>State</span><strong>${escapeHTML(runtime.installation_state || "not_installed")}</strong></span>
          <span><span>Version</span><strong>${escapeHTML(runtime.manifest?.version || "")}</strong></span>
        </div>
      </section>
      <section class="section ocr-cache-panel">
        <header>
          <div>
            <h3>OCR Cache</h3>
            <span class="muted-text">${escapeHTML(entries.length)} cached file(s)</span>
          </div>
          <button id="refreshOcrBtn" type="button">Refresh</button>
        </header>
        <div class="section-body">
          ${ocrCacheTable(entries)}
        </div>
      </section>
    </div>
  `;
  $("#refreshOcrBtn").addEventListener("click", refreshOcr);
  bindOcrActions();
}

function ocrCacheTable(entries) {
  if (!entries.length) return `<div class="empty">No OCR cache entries</div>`;
  const page = paginatedItems(entries, "ocr", 50);
  return `
    <div class="data-table-shell">
      <div class="data-grid-table ocr-table">
        <table>
          <thead><tr><th>Document</th><th>Status</th><th>Pages</th><th>Model</th><th>Artifacts</th><th>Actions</th></tr></thead>
          <tbody>
            ${page.items
              .map(
                (entry) => `
                  <tr>
                    <td>${escapeHTML(entry.doc_name || entry.file_hash)}</td>
                    <td>${badge(entry.status || "unknown")}</td>
                    <td>${escapeHTML(entry.page_count || "")}</td>
                    <td>${escapeHTML(entry.ocr_model || "")}</td>
                    <td>${entry.has_pages ? `<span class="badge good">pages</span>` : `<span class="badge warn">pages</span>`} ${entry.has_pageindex_input ? `<span class="badge good">md</span>` : `<span class="badge warn">md</span>`}</td>
                    <td class="source-actions">
                      <button type="button" data-ocr-rerun="${escapeHTML(entry.file_hash)}">Rerun</button>
                      <button type="button" data-ocr-retry="${escapeHTML(entry.file_hash)}">Retry</button>
                      <button class="danger" type="button" data-ocr-invalidate="${escapeHTML(entry.file_hash)}">Invalidate</button>
                    </td>
                  </tr>
                `,
              )
              .join("")}
          </tbody>
        </table>
      </div>
      ${renderPager(page.meta, "ocr")}
    </div>
  `;
}

function bindOcrActions() {
  mainView.querySelectorAll("[data-ocr-invalidate]").forEach((button) => {
    button.addEventListener("click", () => invalidateOcrCache(button.dataset.ocrInvalidate));
  });
  mainView.querySelectorAll("[data-ocr-rerun]").forEach((button) => {
    button.addEventListener("click", () => rerunOcrCache(button.dataset.ocrRerun));
  });
  mainView.querySelectorAll("[data-ocr-retry]").forEach((button) => {
    button.addEventListener("click", () => retryOcrCache(button.dataset.ocrRetry));
  });
}

async function refreshOcr(event) {
  const button = event?.currentTarget;
  setButtonBusy(button, true, "Refreshing...");
  try {
    await loadOcrData();
    renderOcr();
  } catch (error) {
    setError(error.message);
  } finally {
    setButtonBusy(button, false);
  }
}

async function invalidateOcrCache(fileHash) {
  if (!fileHash) return;
  try {
    await api(`/api/ocr/cache/${encodeURIComponent(fileHash)}/invalidate`, {
      method: "POST",
      body: JSON.stringify({ kb_dir: state.kbDir }),
    });
    await loadOcrData();
    renderOcr();
    notify("OCR cache invalidated", "success");
  } catch (error) {
    notify(error.message, "error");
  }
}

async function rerunOcrCache(fileHash) {
  if (!fileHash) return;
  try {
    const result = await api(`/api/ocr/cache/${encodeURIComponent(fileHash)}/rerun`, {
      method: "POST",
      body: JSON.stringify({ kb_dir: state.kbDir, strategy_override: "ocr-pageindex-local" }),
    });
    trackJob(result.job, "OCR rerun queued");
  } catch (error) {
    notify(error.message, "error");
  }
}

async function retryOcrCache(fileHash) {
  if (!fileHash) return;
  try {
    const result = await api(`/api/ocr/cache/${encodeURIComponent(fileHash)}/retry`, {
      method: "POST",
      body: JSON.stringify({ kb_dir: state.kbDir, strategy_override: "ocr-local-long" }),
    });
    trackJob(result.job, "OCR retry queued");
  } catch (error) {
    notify(error.message, "error");
  }
}

function wikiFileDirectory(fileOrPath) {
  if (typeof fileOrPath === "string") {
    const parts = fileOrPath.split("/").filter(Boolean);
    return parts.length > 1 ? parts.slice(0, -1).join("/") : "";
  }
  return fileOrPath?.directory || wikiFileDirectory(fileOrPath?.path || "");
}

function wikiDirectoryLabel(directory) {
  return directory ? `${directory}/` : "wiki/";
}

function wikiDirectories() {
  const counts = new Map();
  (state.wikiTree || []).forEach((file) => {
    const directory = wikiFileDirectory(file);
    counts.set(directory, (counts.get(directory) || 0) + 1);
  });
  return Array.from(counts.entries())
    .map(([path, count]) => ({ path, count, label: wikiDirectoryLabel(path) }))
    .sort((a, b) => (a.path === "" ? -1 : b.path === "" ? 1 : a.path.localeCompare(b.path)));
}

function filesInWikiDirectory(directory = state.wikiDirectory) {
  return (state.wikiTree || []).filter((file) => wikiFileDirectory(file) === (directory || ""));
}

function filteredWikiDirectoryFiles() {
  const query = state.wikiSearch.trim().toLowerCase();
  const files = filesInWikiDirectory();
  if (!query) return files;
  return files.filter((file) => {
    const haystack = [file.name, file.path, file.extension, file.modified].filter(Boolean).join(" ").toLowerCase();
    return haystack.includes(query);
  });
}

function renderWikiFileList(files) {
  if (!files.length) return `<div class="empty">No matching files in this directory</div>`;
  return files
    .map((file) => {
      const active = file.path === state.selectedWikiPath ? " active" : "";
      return `
        <button type="button" data-action="wiki-select" data-wiki-path="${escapeHTML(file.path)}" class="file-row${active}">
          <span>${escapeHTML(file.name)}</span>
          <small>${escapeHTML(file.modified || "")}${file.size ? ` 路 ${escapeHTML(file.size)} bytes` : ""}</small>
        </button>
      `;
    })
    .join("");
}

function wikiDisplayContent(path = state.selectedWikiPath) {
  return state.wikiDrafts[path] ?? state.wikiFileCache[path]?.content ?? "";
}

function inlineMarkdown(text) {
  return escapeHTML(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[\[([^\]]+)\]\]/g, "<code>[[$1]]</code>");
}

function renderMarkdown(markdown) {
  const lines = String(markdown || "").replace(/\r\n/g, "\n").split("\n");
  const html = [];
  let inCode = false;
  let listType = null;

  const closeList = () => {
    if (!listType) return;
    html.push(`</${listType}>`);
    listType = null;
  };

  lines.forEach((line) => {
    const trimmed = line.trim();
    if (trimmed.startsWith("```")) {
      closeList();
      html.push(inCode ? "</code></pre>" : "<pre><code>");
      inCode = !inCode;
      return;
    }
    if (inCode) {
      html.push(`${escapeHTML(line)}\n`);
      return;
    }
    if (!trimmed) {
      closeList();
      return;
    }
    const heading = /^(#{1,6})\s+(.+)$/.exec(trimmed);
    if (heading) {
      closeList();
      const level = heading[1].length;
      html.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
      return;
    }
    if (/^[-*_]{3,}$/.test(trimmed)) {
      closeList();
      html.push("<hr />");
      return;
    }
    const unordered = /^[-*]\s+(.+)$/.exec(trimmed);
    if (unordered) {
      if (listType !== "ul") {
        closeList();
        listType = "ul";
        html.push("<ul>");
      }
      html.push(`<li>${inlineMarkdown(unordered[1])}</li>`);
      return;
    }
    const ordered = /^\d+\.\s+(.+)$/.exec(trimmed);
    if (ordered) {
      if (listType !== "ol") {
        closeList();
        listType = "ol";
        html.push("<ol>");
      }
      html.push(`<li>${inlineMarkdown(ordered[1])}</li>`);
      return;
    }
    if (trimmed.startsWith(">")) {
      closeList();
      html.push(`<blockquote>${inlineMarkdown(trimmed.replace(/^>\s?/, ""))}</blockquote>`);
      return;
    }
    closeList();
    html.push(`<p>${inlineMarkdown(trimmed)}</p>`);
  });
  closeList();
  if (inCode) html.push("</code></pre>");
  return html.join("");
}

function updateWikiDocumentPanes(path = state.selectedWikiPath) {
  const content = wikiDisplayContent(path);
  if ($("#wikiPathInput")) $("#wikiPathInput").value = path || "";
  if ($("#wikiEditor") && $("#wikiEditor").value !== content) $("#wikiEditor").value = content;
  if ($("#wikiPreviewPane")) {
    $("#wikiPreviewPane").innerHTML =
      state.wikiLoadingPath === path ? `<div class="empty">Loading...</div>` : renderMarkdown(content);
  }
}

function selectWikiFile(path) {
  if (!path) return;
  state.selectedWikiPath = path;
  state.wikiDirectory = wikiFileDirectory(path);
  renderWiki();
  ensureWikiFileLoaded(path);
}

async function ensureWikiFileLoaded(path, options = {}) {
  if (!path) return;
  const cached = state.wikiFileCache[path];
  if (cached?.content !== undefined && !options.force) {
    if (state.selectedWikiPath === path) updateWikiDocumentPanes(path);
    return;
  }
  state.wikiLoadingPath = path;
  if (state.selectedWikiPath === path) updateWikiDocumentPanes(path);
  try {
    const file = await api(withKb(`/api/wiki/file?path=${encodeURIComponent(path)}`));
    state.wikiFileCache[file.path] = { path: file.path, content: file.content };
    if (state.wikiDrafts[file.path] === undefined && state.selectedWikiPath === file.path) {
      updateWikiDocumentPanes(file.path);
    }
  } catch (error) {
    if ($("#wikiPreviewPane") && state.selectedWikiPath === path) {
      $("#wikiPreviewPane").innerHTML = `<div class="empty">${escapeHTML(error.message)}</div>`;
    }
    notify(error.message, "error");
  } finally {
    if (state.wikiLoadingPath === path) state.wikiLoadingPath = null;
    if (state.selectedWikiPath === path) updateWikiDocumentPanes(path);
  }
}

function selectWikiDirectory(directory) {
  state.wikiDirectory = directory || "";
  resetPage("wiki");
  const files = filesInWikiDirectory();
  if (files.length && !files.some((file) => file.path === state.selectedWikiPath)) {
    state.selectedWikiPath = files[0].path;
  }
  renderWiki();
  if (state.selectedWikiPath) ensureWikiFileLoaded(state.selectedWikiPath);
}

function renderWiki() {
  const files = state.wikiTree || [];
  const selectedFile = files.find((file) => file.path === state.selectedWikiPath);
  if (selectedFile && wikiFileDirectory(selectedFile) !== state.wikiDirectory) {
    state.wikiDirectory = wikiFileDirectory(selectedFile);
  }
  if (!selectedFile && files.length) {
    state.selectedWikiPath = files[0].path;
    state.wikiDirectory = wikiFileDirectory(files[0]);
  }
  const directories = wikiDirectories();
  if (directories.length && !directories.some((directory) => directory.path === state.wikiDirectory)) {
    state.wikiDirectory = directories[0].path;
  }
  const directoryFiles = filesInWikiDirectory();
  if (directoryFiles.length && !directoryFiles.some((file) => file.path === state.selectedWikiPath)) {
    state.selectedWikiPath = directoryFiles[0].path;
  }
  const filtered = filteredWikiDirectoryFiles();
  const page = paginatedItems(filtered, "wiki", 40);
  const displayContent = state.wikiLoadingPath === state.selectedWikiPath ? "" : wikiDisplayContent();
  const mode = state.ui.wikiMode || "preview";
  mainView.innerHTML = `
    <div class="wiki-layout">
      <div class="wiki-directory-toolbar">
        <div class="field">
          <label for="wikiDirectorySelect">Directory</label>
          <select id="wikiDirectorySelect" data-action="wiki-directory">
            ${directories
              .map(
                (directory) =>
                  `<option value="${escapeHTML(directory.path)}" ${directory.path === state.wikiDirectory ? "selected" : ""}>${escapeHTML(directory.label)} (${escapeHTML(directory.count)})</option>`,
              )
              .join("")}
          </select>
        </div>
        <div class="wiki-search-row">
          <input data-action="wiki-search" type="search" placeholder="Search this directory" value="${escapeHTML(state.wikiSearch)}" />
        </div>
        <div class="wiki-page-summary">
          <strong>${escapeHTML(filtered.length)}</strong>
          <span>of ${escapeHTML(directoryFiles.length)} page(s)</span>
        </div>
      </div>
      <div class="wiki-body">
        <aside class="wiki-browser" aria-label="Wiki pages">
          <div class="wiki-tree-list">
            ${files.length ? renderWikiFileList(page.items) : `<div class="empty">No files</div>`}
            ${renderPager(page.meta, "wiki")}
          </div>
        </aside>
        <div class="markdown-pane">
          <div class="wiki-editor-toolbar">
            <input id="wikiPathInput" type="hidden" value="${escapeHTML(state.selectedWikiPath || "")}" />
            <div class="wiki-current-path">
              <strong>${escapeHTML(state.selectedWikiPath || "No page selected")}</strong>
              <span>${state.wikiDrafts[state.selectedWikiPath] !== undefined ? "Unsaved changes" : "Saved"}</span>
            </div>
            <div class="wiki-mode-tabs" role="tablist" aria-label="Markdown mode">
              <button class="${mode === "preview" ? "active" : ""}" type="button" data-action="wiki-mode" data-wiki-mode="preview">Preview</button>
              <button class="${mode === "source" ? "active" : ""}" type="button" data-action="wiki-mode" data-wiki-mode="source">Source</button>
            </div>
            <button id="saveWikiBtn" class="primary" type="button">Save</button>
          </div>
          <section id="wikiPreviewPane" class="wiki-preview-pane${mode === "preview" ? " active" : ""}" aria-label="Markdown preview">
            ${state.wikiLoadingPath === state.selectedWikiPath ? `<div class="empty">Loading...</div>` : renderMarkdown(displayContent)}
          </section>
          <section id="wikiSourcePane" class="wiki-source-pane${mode === "source" ? " active" : ""}" aria-label="Markdown source">
            <textarea id="wikiEditor" spellcheck="false">${escapeHTML(displayContent)}</textarea>
          </section>
        </div>
      </div>
    </div>
  `;
  $("#wikiDirectorySelect")?.addEventListener("change", (event) => selectWikiDirectory(event.currentTarget.value));
  $("#saveWikiBtn")?.addEventListener("click", saveWikiFile);
  $("#wikiEditor")?.addEventListener("input", () => {
    state.wikiDrafts[state.selectedWikiPath] = $("#wikiEditor").value;
    updateWikiDocumentPanes(state.selectedWikiPath);
  });
  if (state.selectedWikiPath && !state.wikiFileCache[state.selectedWikiPath] && state.wikiLoadingPath !== state.selectedWikiPath) {
    ensureWikiFileLoaded(state.selectedWikiPath);
  }
}

async function saveWikiFile(event) {
  const button = event?.currentTarget;
  const path = state.selectedWikiPath || $("#wikiPathInput")?.value?.trim();
  if (!path) {
    notify("Select a wiki page first.", "warning");
    return;
  }
  const content = $("#wikiEditor")?.value ?? wikiDisplayContent(path);
  setButtonBusy(button, true, "Saving...");
  try {
    await api("/api/wiki/file", {
      method: "PUT",
      body: JSON.stringify({
        kb_dir: state.kbDir,
        path,
        content,
      }),
    });
    notify("Wiki page saved", "success");
    state.wikiFileCache[path] = { path, content };
    delete state.wikiDrafts[path];
    state.selectedWikiPath = path;
    state.wikiDirectory = wikiFileDirectory(path);
    await loadKnowledgeData();
    render();
  } catch (error) {
    setError(error.message);
  } finally {
    setButtonBusy(button, false);
  }
}

function renderSessions() {
  const sessions = state.chats || [];
  const page = paginatedItems(sessions, "sessions", 50);
  mainView.innerHTML = `
    <section class="section">
      <header><h3>Chat Sessions</h3></header>
      <div class="section-body">
        ${
          sessions.length
            ? `<div class="data-table-shell">
                <div class="data-grid-table sessions-table">
                  <table><thead><tr><th>ID</th><th>Turns</th><th>Updated</th><th>Title</th><th>Actions</th></tr></thead><tbody>
                    ${page.items.map((session) => `<tr><td>${escapeHTML(session.id)}</td><td>${escapeHTML(session.turn_count)}</td><td>${escapeHTML(session.updated_at)}</td><td>${escapeHTML(session.title || "")}</td><td class="source-actions"><button type="button" data-action="open-chat" data-open-chat="${escapeHTML(session.id)}">Open</button><button class="danger" type="button" data-delete-chat="${escapeHTML(session.id)}">Delete</button></td></tr>`).join("")}
                  </tbody></table>
                </div>
                ${renderPager(page.meta, "sessions")}
              </div>`
            : `<div class="empty">No sessions</div>`
        }
      </div>
    </section>
  `;
  mainView.querySelectorAll("[data-delete-chat]").forEach((button) => {
    button.addEventListener("click", async () => {
      setButtonBusy(button, true, "Deleting...");
      try {
        await api(withKb(`/api/chats/${encodeURIComponent(button.dataset.deleteChat)}`), { method: "DELETE" });
        if (button.dataset.deleteChat === state.activeChatSessionId) {
          setActiveChatSession(null);
        }
        notify("Session deleted", "success");
        await loadKnowledgeData();
        render();
      } catch (error) {
        setError(error.message);
      } finally {
        setButtonBusy(button, false);
      }
    });
  });
}

function renderLlmUsage() {
  const usage = state.llmUsage;
  const items = usage?.items || [];
  const meta = usage
    ? {
        page: usage.page || 1,
        pageSize: usage.page_size || 50,
        pages: usage.pages || 1,
        total: usage.total || 0,
        start: usage.start || 0,
        end: usage.end || 0,
      }
    : null;
  mainView.innerHTML = `
    <section class="section">
      <header>
        <div>
          <h3>LLM Usage</h3>
          <span class="muted-text">${escapeHTML(meta?.total || 0)} record(s)</span>
        </div>
        <div class="row-actions">
          <button id="exportLlmUsageBtn" type="button">Export</button>
        </div>
      </header>
      <div class="section-body">
        <div class="llm-usage-toolbar">
          <div class="wiki-search-row">
            <input id="llmUsageSearchInput" type="search" placeholder="Search feature, model, status, or error" value="${escapeHTML(state.llmUsageSearch)}" />
          </div>
          <div class="llm-usage-summary">
            ${
              meta
                ? `Showing ${escapeHTML(meta.start)}-${escapeHTML(meta.end)} of ${escapeHTML(meta.total)}`
                : "Loading records"
            }
          </div>
          ${meta ? renderUsagePager(meta) : ""}
        </div>
        <div class="data-table-shell">
          <div class="data-grid-table">
            <table>
              <thead>
                <tr><th>Time</th><th>Feature</th><th>Model</th><th>API</th><th>Duration</th><th>Status</th><th>Error</th></tr>
              </thead>
              <tbody>
                ${
                  usage === null
                    ? `<tr><td colspan="7">Loading...</td></tr>`
                    : items.length
                      ? items
                          .map(
                            (item) => `
                              <tr>
                                <td>${escapeHTML(formatDateTime(item.created_at))}</td>
                                <td>${escapeHTML(item.feature)}</td>
                                <td>${escapeHTML(item.model)}</td>
                                <td>${escapeHTML(item.wire_api)}</td>
                                <td>${escapeHTML(`${item.duration_ms} ms`)}</td>
                                <td>${badge(item.status)}</td>
                                <td>${escapeHTML(item.error || "")}</td>
                              </tr>
                            `,
                          )
                          .join("")
                      : `<tr><td colspan="7">No usage records</td></tr>`
                }
              </tbody>
            </table>
          </div>
          ${meta ? renderUsagePager(meta) : ""}
        </div>
      </div>
    </section>
  `;
  $("#llmUsageSearchInput")?.addEventListener("input", async (event) => {
    state.llmUsageSearch = event.target.value;
    setPage("usage", 1);
    try {
      await loadLlmUsage({ page: 1 });
      renderLlmUsage();
      const search = $("#llmUsageSearchInput");
      search?.focus();
      search?.setSelectionRange(search.value.length, search.value.length);
    } catch (error) {
      notify(error.message, "error");
    }
  });
  $("#exportLlmUsageBtn")?.addEventListener("click", exportLlmUsage);
}

function renderReports() {
  const reports = state.documents?.reports || [];
  syncSelectedReport(reports);
  syncFixPlanFromJobs();
  const candidates = state.fixPlan?.candidates || [];
  const reportPage = paginatedItems(reports, "reports", 50);
  const selectedCount = candidates.filter((item) => isSelectableFix(item) && state.selectedFixes[fixKey(item)]).length;
  const activeReportName = state.selectedReport ? state.selectedReport.replace(/^reports\//, "") : "";
  mainView.innerHTML = `
    <div class="quality-layout">
      <section class="section quality-actions">
        <header>
          <div>
            <h3>Quality Workflow</h3>
            <span class="muted-text">${escapeHTML(activeReportName || "No report selected")}</span>
          </div>
          <div class="row-actions">
            <button id="runLintBtn" type="button">Run Lint</button>
            <button id="planFixBtn" class="primary" type="button" ${state.selectedReport ? "" : "disabled"}>Generate Fix Plan</button>
            <button id="applyFixBtn" type="button" ${selectedCount ? "" : "disabled"}>Apply Approved</button>
          </div>
        </header>
        <div class="section-body quality-strip">
          ${qualityStat("Reports", reports.length)}
          ${qualityStat("Candidates", candidates.length)}
          ${qualityStat("Approved", selectedCount)}
          ${qualityStat("Created", state.lastFixApply?.created?.length ?? 0)}
          ${qualityStat("Review", state.lastFixApply?.reviewed?.length ?? 0)}
        </div>
      </section>

      <section class="section report-browser">
        <header>
          <div>
            <h3>Reports</h3>
            <span class="muted-text">${escapeHTML(state.status?.last_lint || "")}</span>
          </div>
          ${activeReportName ? `<span class="badge muted">${escapeHTML(activeReportName)}</span>` : ""}
        </header>
        <div class="section-body report-browser-body">
          <div class="report-list">
            ${reportPage.items.map((name) => {
              const path = `reports/${name}`;
              const active = path === state.selectedReport ? " active" : "";
              return `<button type="button" class="report-button${active}" data-report="${escapeHTML(path)}">${escapeHTML(name)}</button>`;
            }).join("") || `<div class="empty">No reports</div>`}
            ${renderPager(reportPage.meta, "reports")}
          </div>
          <pre id="reportPreview" class="preview report-preview">${escapeHTML(reportPreviewText())}</pre>
        </div>
      </section>

      <section class="section fix-panel">
        <header>
          <div>
            <h3>Fix Plan</h3>
            <span class="muted-text">${escapeHTML(state.fixPlan?.report || "")}</span>
          </div>
          <div class="row-actions">
            <button id="selectAllFixesBtn" type="button" ${candidates.length ? "" : "disabled"}>All</button>
            <button id="clearFixesBtn" type="button" ${candidates.length ? "" : "disabled"}>None</button>
          </div>
        </header>
        <div class="section-body fix-list">
          ${renderFixCandidates(candidates)}
          ${renderAppliedFixes()}
        </div>
      </section>
    </div>
  `;
  $("#runLintBtn").addEventListener("click", runLint);
  $("#planFixBtn")?.addEventListener("click", generateFixPlan);
  $("#applyFixBtn")?.addEventListener("click", applyApprovedFixes);
  $("#selectAllFixesBtn")?.addEventListener("click", () => setAllFixes(true));
  $("#clearFixesBtn")?.addEventListener("click", () => setAllFixes(false));
  mainView.querySelectorAll("[data-report]").forEach((button) => {
    button.addEventListener("click", () => selectReport(button.dataset.report));
  });
  mainView.querySelectorAll("[data-fix-key]").forEach((input) => {
    input.addEventListener("change", () => {
      state.selectedFixes[input.dataset.fixKey] = input.checked;
      renderReports();
    });
  });
  if (state.selectedReport && state.reportPreview.path !== state.selectedReport && state.reportPreviewLoading !== state.selectedReport) {
    loadReportPreview(state.selectedReport);
  }
}

async function deleteSourceDocument(event) {
  const button = event?.currentTarget;
  const selector = button?.dataset.deleteSource;
  if (!selector) return;
  const row = button.closest("tr");
  const doc = sourceDocuments().find((item) => item.hash === selector);
  const name = button.dataset.sourceName || doc?.name || row?.querySelector("td")?.textContent?.trim() || "this source document";
  const relatedCount = button.dataset.relatedCount || (doc ? sourceRelatedCount(doc) : "");
  const suffix = relatedCount !== "" ? ` This will also clean ${relatedCount} related wiki page(s).` : "";
  if (!window.confirm(`Delete ${name} and clean its generated pages?${suffix}`)) return;
  setButtonBusy(button, true, "Deleting...");
  try {
    const result = await api(withKb(`/api/documents/${encodeURIComponent(selector)}`), { method: "DELETE" });
    trackJob(result.job, "Delete job queued");
  } catch (error) {
    setError(error.message);
  } finally {
    setButtonBusy(button, false);
  }
}

function syncSelectedReport(reports) {
  const paths = reports.map((name) => `reports/${name}`);
  if (!paths.length) {
    state.selectedReport = null;
    state.reportPreview = { path: null, content: "" };
    return;
  }
  if (!state.selectedReport || !paths.includes(state.selectedReport)) {
    state.selectedReport = paths[paths.length - 1];
  }
}

function qualityStat(label, value) {
  return `<div><span>${escapeHTML(label)}</span><strong>${escapeHTML(value)}</strong></div>`;
}

function fixKey(item) {
  return item?.id || item?.path || item?.name || "";
}

function isAutoFix(item) {
  return (item?.action || "create") === "create" && item?.auto_applicable !== false;
}

function isManualReviewFix(item) {
  return (item?.action || "create") === "manual-review";
}

function isSelectableFix(item) {
  return isAutoFix(item) || isManualReviewFix(item);
}

function reportPreviewText() {
  if (!state.selectedReport) return "";
  if (state.reportPreviewLoading === state.selectedReport) return "Loading...";
  if (state.reportPreview.path === state.selectedReport) return state.reportPreview.content;
  return "";
}

function renderFixCandidates(candidates) {
  if (!candidates.length) return `<div class="empty">No fix candidates</div>`;
  const page = paginatedItems(candidates, "fixes", 30);
  return `
    ${page.items
      .map((item) => {
        const key = fixKey(item);
        const auto = isAutoFix(item);
        const review = isManualReviewFix(item);
        const selectable = isSelectableFix(item);
        const checked = state.selectedFixes[key] ? "checked" : "";
        const disabled = selectable ? "" : "disabled";
        const badgeClass = auto ? "warn" : review ? "info" : "muted";
        const actionLabel = item.action || "create";
        return `
          <div class="fix-row${review ? " review-only" : ""}${selectable ? "" : " unavailable"}">
            <label class="fix-select">
              <input type="checkbox" data-fix-key="${escapeHTML(key)}" ${checked} ${disabled} />
              <span class="fix-main">
                <strong>${escapeHTML(item.title || item.name)}</strong>
                <span>${escapeHTML(item.path || `concepts/${item.name}.md`)}</span>
              </span>
            </label>
            <span class="badge ${badgeClass}">${escapeHTML(actionLabel)}</span>
            <div class="fix-detail">
              <div class="fix-meta">
                <span>${escapeHTML(item.source_section || "Lint report")}</span>
                ${item.status ? `<span>${escapeHTML(item.status)}</span>` : ""}
              </div>
              <p class="fix-reason">${escapeHTML(item.reason || "")}</p>
              <details class="fix-preview" open>
                <summary>Planned content</summary>
                <pre>${escapeHTML(item.preview || "")}</pre>
              </details>
            </div>
          </div>
        `;
      })
      .join("")}
    ${renderPager(page.meta, "fixes")}
  `;
}

function renderAppliedFixes() {
  const created = state.lastFixApply?.created || [];
  const reviewed = state.lastFixApply?.reviewed || [];
  if (!created.length && !reviewed.length) return "";
  return `
    <div class="applied-fixes">
      ${created
        .map(
          (item) => `
            <div class="applied-fix created-fix">
              <span>Created draft - ${escapeHTML(item.path)}</span>
              <strong>${escapeHTML(item.title || item.name)}</strong>
            </div>
          `,
        )
        .join("")}
      ${reviewed
        .map(
          (item) => `
            <div class="applied-fix reviewed-fix">
              <span>Approved review - ${escapeHTML(item.path)}</span>
              <strong>${escapeHTML(item.title || item.name)}</strong>
              ${item.reason ? `<em>${escapeHTML(item.reason)}</em>` : ""}
            </div>
          `,
        )
        .join("")}
    </div>
  `;
}

async function selectReport(reportPath) {
  state.selectedReport = reportPath;
  state.fixPlan = null;
  state.selectedFixes = {};
  state.lastFixApply = null;
  state.reportPreviewLoading = reportPath;
  renderReports();
  await loadReportPreview(reportPath);
}

async function loadReportPreview(reportPath) {
  if (!reportPath) return;
  state.reportPreviewLoading = reportPath;
  if ($("#reportPreview")) $("#reportPreview").textContent = "Loading...";
  try {
    const file = await api(withKb(`/api/wiki/file?path=${encodeURIComponent(reportPath)}`));
    state.reportPreview = { path: file.path, content: file.content };
    if ($("#reportPreview") && state.selectedReport === reportPath) {
      $("#reportPreview").textContent = file.content;
    }
  } catch (error) {
    notify(error.message, "error");
    if ($("#reportPreview")) $("#reportPreview").textContent = error.message;
  } finally {
    if (state.reportPreviewLoading === reportPath) state.reportPreviewLoading = null;
  }
}

function setAllFixes(value) {
  (state.fixPlan?.candidates || []).forEach((item) => {
    if (isSelectableFix(item)) {
      state.selectedFixes[fixKey(item)] = value;
    }
  });
  renderReports();
}

function handleAppClick(event) {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const action = target.dataset.action;
  if (action === "utility-tab") {
    state.ui.utilityTab = target.dataset.utilityTab || "jobs";
    renderUtilityPanel();
    return;
  }
  if (action === "job-filter") {
    state.ui.jobFilter = target.dataset.jobFilter || "active";
    resetPage("jobs");
    renderJobsPanel();
    return;
  }
  if (action === "jobs-page") {
    setPage("jobs", target.dataset.page);
    renderJobsPanel();
    return;
  }
  if (action === "select-job") {
    selectJob(target.dataset.jobId);
    return;
  }
  if (action === "job-stop") {
    stopJob(target.dataset.jobStop);
    return;
  }
  if (action === "job-retry") {
    retryJob(target.dataset.jobRetry);
    return;
  }
  if (action === "wiki-select") {
    selectWikiFile(target.dataset.wikiPath);
    return;
  }
  if (action === "wiki-mode") {
    state.ui.wikiMode = target.dataset.wikiMode || "preview";
    renderWiki();
    return;
  }
  if (action === "wiki-page") {
    setPage("wiki", target.dataset.page);
    renderWiki();
    return;
  }
  if (action === "documents-page") {
    setPage("documents", target.dataset.page);
    renderDocuments();
    return;
  }
  if (action === "sources-page") {
    setPage("sources", target.dataset.page);
    renderSources();
    return;
  }
  if (action === "ocr-page") {
    setPage("ocr", target.dataset.page);
    renderOcr();
    return;
  }
  if (action === "sessions-page") {
    setPage("sessions", target.dataset.page);
    renderSessions();
    return;
  }
  if (action === "usage-page") {
    setPage("usage", target.dataset.page);
    loadLlmUsage({ page: target.dataset.page }).then(() => {
      renderLlmUsage();
    }).catch((error) => {
      notify(error.message, "error");
    });
    return;
  }
  if (action === "open-chat") {
    openChatSession(target.dataset.openChat);
    return;
  }
  if (action === "reports-page") {
    setPage("reports", target.dataset.page);
    renderReports();
    return;
  }
  if (action === "fixes-page") {
    setPage("fixes", target.dataset.page);
    renderReports();
    return;
  }
  if (action === "settings-tab") {
    state.ui.settingsTab = target.dataset.settingsTab || "model-pool";
    renderSettings();
    return;
  }
  if (action === "model-pool-page") {
    setPage("model-pool", target.dataset.page);
    renderSettings();
    return;
  }
  if (action === "model-probe") {
    probeModelPoolProfile(target.dataset.profileId, event);
    return;
  }
  if (action === "model-edit") {
    openModelProfileDialog(target.dataset.profileId);
    return;
  }
  if (action === "model-add") {
    openModelProfileDialog(null);
    return;
  }
  if (action === "model-delete") {
    deleteModelPoolProfile(target.dataset.profileId, event);
    return;
  }
  if (action === "model-profile-close") {
    if (target.classList?.contains("model-dialog-backdrop") && event.target !== target) return;
    closeModelProfileDialog();
    return;
  }
  if (action === "model-profile-save") {
    saveModelPoolProfile(event);
    return;
  }
  if (action === "model-toggle") {
    toggleModelPoolProfile(target.dataset.profileId, target.dataset.enabled !== "true", event);
    return;
  }
}

function handleAppInput(event) {
  const target = event.target;
  if (target?.dataset?.action === "wiki-search") {
    state.wikiSearch = target.value;
    resetPage("wiki");
    const matches = filteredWikiDirectoryFiles();
    if (matches.length && !matches.some((file) => file.path === state.selectedWikiPath)) {
      state.selectedWikiPath = matches[0].path;
    }
    renderWiki();
    if (state.selectedWikiPath) ensureWikiFileLoaded(state.selectedWikiPath);
    const input = document.querySelector('[data-action="wiki-search"]');
    if (input) {
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    }
  }
  if (target?.hasAttribute("data-model-pool-search")) {
    state.modelPoolSearch = target.value;
    resetPage("model-pool");
    renderSettings();
    const input = document.querySelector("[data-model-pool-search]");
    if (input) {
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    }
  }
  if (target?.hasAttribute("data-model-health-filter")) {
    state.modelPoolHealthFilter = target.value || "all";
    resetPage("model-pool");
    renderSettings();
  }
}

async function runLint(event) {
  const button = event?.currentTarget;
  setButtonBusy(button, true, "Queueing...");
  try {
    const result = await api("/api/lint", {
      method: "POST",
      body: JSON.stringify({ kb_dir: state.kbDir }),
    });
    trackJob(result.job, "Lint job queued");
  } catch (error) {
    setError(error.message);
  } finally {
    setButtonBusy(button, false);
  }
}

function toggleApiKeyVisibility() {
  const apiKeyInput = $("#apiKeyInput");
  const button = $("#toggleApiKeyBtn");
  if (!apiKeyInput) return;
  const nextType = apiKeyInput.type === "password" ? "text" : "password";
  apiKeyInput.type = nextType;
  if (button) {
    button.textContent = nextType === "password" ? "Show" : "Hide";
    button.setAttribute("aria-label", nextType === "password" ? "Show API key" : "Hide API key");
  }
}

function settingsTabs() {
  const tabs = [
    ["model-pool", "Model Pool"],
    ["general", "General"],
  ];
  return `
    <div class="settings-tabs" role="tablist" aria-label="Settings pages">
      ${tabs
        .map(([id, label]) => {
          const active = (state.ui.settingsTab || "model-pool") === id ? " active" : "";
          return `<button class="${active.trim()}" type="button" data-action="settings-tab" data-settings-tab="${escapeHTML(id)}">${escapeHTML(label)}</button>`;
        })
        .join("")}
    </div>
  `;
}

function modelPoolProfiles() {
  const profiles = state.modelPool?.profiles || [];
  const query = state.modelPoolSearch.trim().toLowerCase();
  const health = state.modelPoolHealthFilter || "all";
  return profiles.filter((profile) => {
    const haystack = [
      profile.name,
      profile.id,
      profile.model,
      profile.base_url,
      ...(profile.tags || []),
      ...(profile.features || []),
      ...(profile.probe_models || []),
    ]
      .join(" ")
      .toLowerCase();
    return (!query || haystack.includes(query)) && (health === "all" || profile.health === health);
  });
}

function modelHealthBadge(profile) {
  const health = profile.health || "unknown";
  const cls = health === "healthy" ? "good" : health === "offline" ? "bad" : health === "degraded" ? "warn" : "muted";
  return `<span class="badge ${cls}"><span class="model-health-dot ${escapeHTML(health)}"></span>${escapeHTML(health)}</span>`;
}

function modelRowsText(profile) {
  const rows = profile?.routes?.length ? profile.routes : profile?.models || [];
  return rows
    .map((route) => `${route.model || route.name || ""}, ${route.weight || 100}`)
    .filter((line) => line.trim())
    .join("\n");
}

function parseModelRows(text) {
  return String(text || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [namePart, weightPart] = line.split(/[,\s]+/, 2);
      return {
        name: (namePart || "").trim(),
        weight: Math.max(Number(weightPart || 100), 1),
      };
    })
    .filter((item) => item.name);
}

function modelPoolStat(label, value) {
  return `<div><span>${escapeHTML(label)}</span><strong>${escapeHTML(value)}</strong></div>`;
}

function renderModelPoolCard(profile) {
  const endpoint = profile.base_url || "(default provider)";
  const routes = profile.routes?.length
    ? profile.routes
    : (profile.probe_models || [profile.model]).map((model) => ({
        model,
        weight: 100,
        health: (profile.available_models || []).includes(model) ? "healthy" : profile.failed_models?.[model] ? "offline" : "unknown",
        latency_ms: profile.latency_ms,
        last_error: profile.failed_models?.[model] || "",
      }));
  const disabled = profile.enabled === false;
  return `
    <article class="model-pool-card" data-profile-card="${escapeHTML(profile.id)}">
      <header>
        <div class="model-avatar">${escapeHTML((profile.name || profile.id || "M").slice(0, 2))}</div>
        <div class="model-card-title">
          <strong>${escapeHTML(profile.name || profile.id)}</strong>
          <span>${escapeHTML(profile.wire_api || "chat_completions")}</span>
        </div>
        ${modelHealthBadge(profile)}
      </header>
      <div class="model-tags">
        ${(profile.tags || []).map((tag) => `<span>${escapeHTML(tag)}</span>`).join("")}
        ${(profile.features || []).map((feature) => `<span>${escapeHTML(feature)}</span>`).join("")}
        ${profile.api_key_configured ? `<span>key set</span>` : `<span>no key</span>`}
      </div>
      <p class="model-description">${escapeHTML(profile.last_error || "Available for pool load balancing")}</p>
      <div class="model-endpoint">
        <span class="model-health-dot ${escapeHTML(profile.health || "unknown")}"></span>
        <code>${escapeHTML(endpoint)}</code>
      </div>
      <div class="model-list">
        ${routes
          .map((route) => {
            const ok = route.health === "healthy";
            const error = route.last_error || (route.health === "offline" ? "offline" : "");
            return `
              <div>
                <span><i class="model-health-dot ${escapeHTML(route.health || "unknown")}"></i>${escapeHTML(route.model)} <em>w${escapeHTML(route.weight || 100)}</em></span>
                ${ok ? `<strong class="good-text">${escapeHTML(route.latency_ms || "")}ms</strong>` : error ? `<strong class="bad-text">${escapeHTML(error)}</strong>` : `<strong class="muted-text">pending</strong>`}
              </div>
            `;
          })
          .join("")}
      </div>
      <footer>
        <span>Checked ${escapeHTML(formatTime(profile.last_checked_at) || "never")}</span>
        <div class="row-actions">
          <button type="button" data-action="model-probe" data-profile-id="${escapeHTML(profile.id)}">Probe</button>
          <button type="button" data-action="model-edit" data-profile-id="${escapeHTML(profile.id)}">Edit</button>
          <button class="danger" type="button" data-action="model-delete" data-profile-id="${escapeHTML(profile.id)}">Delete</button>
          <button type="button" data-action="model-toggle" data-profile-id="${escapeHTML(profile.id)}" data-enabled="${disabled ? "false" : "true"}">${disabled ? "Enable" : "Disable"}</button>
        </div>
      </footer>
    </article>
  `;
}

function renderModelProfileDialog() {
  const dialog = state.modelProfileDialog;
  if (!dialog) return "";
  const profile = dialog.profileId ? poolProfileById(dialog.profileId) : {};
  const title = dialog.profileId ? "Edit Model Endpoint" : "Add Model Endpoint";
  return `
    <div class="model-dialog-backdrop" data-action="model-profile-close">
      <section class="model-profile-dialog" role="dialog" aria-modal="true" aria-label="${escapeHTML(title)}">
        <header>
          <div>
            <h3>${escapeHTML(title)}</h3>
            <span class="muted-text">One endpoint can expose multiple weighted models</span>
          </div>
          <button type="button" data-action="model-profile-close" aria-label="Close">x</button>
        </header>
        <div class="model-profile-form">
          <div class="field">
            <label for="modelProfileNameInput">Name</label>
            <input id="modelProfileNameInput" type="text" value="${escapeHTML(profile?.name || "")}" placeholder="Gateway" />
          </div>
          <div class="field">
            <label for="modelProfileWireApiInput">Wire API</label>
            <select id="modelProfileWireApiInput">
              <option value="responses">responses</option>
              <option value="chat_completions">chat_completions</option>
            </select>
          </div>
          <div class="field full">
            <label for="modelProfileBaseUrlInput">Base URL</label>
            <input id="modelProfileBaseUrlInput" type="url" value="${escapeHTML(profile?.base_url || "")}" placeholder="https://api.example.com/v1" />
          </div>
          <div class="field full">
            <label for="modelProfileModelsInput">Models</label>
            <textarea id="modelProfileModelsInput" spellcheck="false" placeholder="gpt-4o-mini, 100&#10;gpt-5.4-mini, 50">${escapeHTML(modelRowsText(profile))}</textarea>
          </div>
          <div class="field full">
            <label for="apiKeyInput">API Key</label>
            <div class="key-input-row">
              <input id="apiKeyInput" type="password" value="${escapeHTML(profile?.api_key || "")}" placeholder="Paste API key" />
              <button id="toggleApiKeyBtn" type="button" aria-label="Show API key">Show</button>
            </div>
          </div>
          <label class="checkline full">
            <input id="modelProfileEnabledInput" type="checkbox" ${profile?.enabled === false ? "" : "checked"} />
            Enabled
          </label>
        </div>
        <footer>
          <button type="button" data-action="model-profile-close">Cancel</button>
          <button class="primary" type="button" data-action="model-profile-save" data-profile-id="${escapeHTML(dialog.profileId || "")}">Save</button>
        </footer>
      </section>
    </div>
  `;
}

function renderModelPool() {
  const pool = state.modelPool || { summary: {}, profiles: [] };
  const page = paginatedItems(modelPoolProfiles(), "model-pool", 24);
  return `
    <section class="section model-pool-section">
      <header>
        <div>
          <h3>Model Pool</h3>
          <span class="muted-text">${escapeHTML(pool.summary?.healthy || 0)} healthy / ${escapeHTML(pool.summary?.total || 0)} profile(s)</span>
        </div>
        <div class="row-actions">
          <button id="probeAllModelPoolBtn" type="button">Probe All</button>
          <button id="addModelProfileBtn" class="primary" type="button" data-action="model-add">Add Endpoint</button>
        </div>
      </header>
      <div class="model-pool-toggle-row">
        <label class="checkline">
          <input id="modelPoolEnabledInput" type="checkbox" ${pool.enabled ? "checked" : ""} />
          Enable Model Pool
        </label>
        <button id="saveModelPoolSettingsBtn" type="button">Save Pool Settings</button>
      </div>
      <div class="model-pool-toolbar">
        <input type="search" placeholder="Search sites or API base URL..." value="${escapeHTML(state.modelPoolSearch)}" data-model-pool-search />
        <select data-model-health-filter>
          <option value="all">All Health</option>
          <option value="healthy">Healthy</option>
          <option value="degraded">Degraded</option>
          <option value="offline">Offline</option>
          <option value="disabled">Disabled</option>
          <option value="unknown">Unknown</option>
        </select>
        <div class="model-pool-summary">
          ${modelPoolStat("Healthy", pool.summary?.healthy || 0)}
          ${modelPoolStat("Degraded", pool.summary?.degraded || 0)}
          ${modelPoolStat("Offline", pool.summary?.offline || 0)}
        </div>
      </div>
      <div class="model-pool-grid">
        ${page.items.length ? page.items.map(renderModelPoolCard).join("") : `<div class="empty">No matching model profiles</div>`}
      </div>
      ${renderPager(page.meta, "model-pool")}
    </section>
    ${renderModelProfileDialog()}
  `;
}

function renderGeneralSettings() {
  const cfg = state.config || {};
  const runtime = state.pageindexLocalStatus || {};
  return `
    <div class="settings-grid">
      <section class="section">
        <header>
          <h3>Knowledge Base</h3>
          <div class="row-actions">
            <button id="exportProfilesBtn" type="button">Export Settings</button>
            <button id="importProfilesBtn" type="button">Import Settings</button>
            <input id="importProfilesInput" type="file" accept="application/json,.json" hidden />
            <button id="saveSettingsBtn" class="primary" type="button">Save Settings</button>
          </div>
        </header>
        <div class="section-body form-grid">
          <div class="field full">
            <label for="kbPathInput">Path</label>
            <input id="kbPathInput" type="text" value="${escapeHTML(state.kbDir || "")}" />
          </div>
          <div class="row-actions full">
            <button id="useKbBtn" type="button">Use</button>
            <button id="createKbBtn" class="primary" type="button">Create</button>
          </div>
          <div class="field">
            <label for="languageInput">Language</label>
            <input id="languageInput" type="text" value="${escapeHTML(cfg.language || "en")}" />
          </div>
          <div class="field">
            <label for="thresholdInput">PageIndex Threshold</label>
            <input id="thresholdInput" type="number" min="1" value="${escapeHTML(cfg.pageindex_threshold || 20)}" />
          </div>
          <div class="field">
            <label for="compileConcurrencyInput">Compile Concurrency</label>
            <input id="compileConcurrencyInput" type="number" min="1" value="${escapeHTML(cfg.compile_max_concurrency || 2)}" />
          </div>
          <div class="field">
            <label for="ocrEnabledInput">OCR</label>
            <label class="checkline">
              <input id="ocrEnabledInput" type="checkbox" ${cfg.ocr_enabled === false ? "" : "checked"} />
              Enabled
            </label>
          </div>
          <div class="field">
            <label for="ocrDetectionModeInput">OCR Detection</label>
            <select id="ocrDetectionModeInput">
              <option value="auto_recommend">auto_recommend</option>
              <option value="always_ask">always_ask</option>
              <option value="disabled">disabled</option>
            </select>
          </div>
          <div class="field">
            <label for="ocrDefaultModelInput">OCR Model</label>
            <select id="ocrDefaultModelInput">
              <option value="PaddleOCR-VL-1.5">PaddleOCR-VL-1.5</option>
              <option value="PP-StructureV3">PP-StructureV3</option>
            </select>
          </div>
          <div class="field">
            <label for="ocrChunkPagesInput">OCR Chunk Pages</label>
            <input id="ocrChunkPagesInput" type="number" min="1" max="100" value="${escapeHTML(cfg.ocr_chunk_pages || 100)}" />
          </div>
          <div class="field">
            <label for="ocrAutoRecommendInput">OCR Recommendation</label>
            <label class="checkline">
              <input id="ocrAutoRecommendInput" type="checkbox" ${cfg.ocr_auto_recommend === false ? "" : "checked"} />
              Auto
            </label>
          </div>
          <div class="field">
            <label for="pageindexLocalEnabledInput">Local PageIndex</label>
            <label class="checkline">
              <input id="pageindexLocalEnabledInput" type="checkbox" ${cfg.pageindex_local_enabled ? "checked" : ""} />
              Enabled
            </label>
          </div>
          <div class="field">
            <label for="pageindexLocalModelInput">Local PageIndex Model</label>
            <input id="pageindexLocalModelInput" type="text" value="${escapeHTML(cfg.pageindex_local_model || "")}" />
          </div>
          <div class="field">
            <label for="pageindexLocalInstallationStateInput">Local PageIndex State</label>
            <select id="pageindexLocalInstallationStateInput">
              <option value="not_installed">not_installed</option>
              <option value="installing">installing</option>
              <option value="installed">installed</option>
              <option value="failed">failed</option>
            </select>
          </div>
          <div class="field full">
            <label>Local PageIndex Runtime</label>
            <div class="runtime-list">
              <div><span>Ready</span><strong>${runtime.ready ? "yes" : "no"}</strong></div>
              <div><span>Version</span><strong>${escapeHTML(runtime.manifest?.version || cfg.pageindex_local_version || "")}</strong></div>
              <div><span>Root</span><strong>${escapeHTML(runtime.root || "")}</strong></div>
            </div>
          </div>
          <div class="field full">
            <label for="pageindexLocalRepoDirInput">Local PageIndex Repo Dir</label>
            <input id="pageindexLocalRepoDirInput" type="text" value="${escapeHTML(cfg.pageindex_local_repo_dir || "")}" />
          </div>
          <div class="field full">
            <label for="pageindexLocalPythonPathInput">Local PageIndex Python Path</label>
            <input id="pageindexLocalPythonPathInput" type="text" value="${escapeHTML(cfg.pageindex_local_python_path || "")}" />
          </div>
          <div class="field full">
            <label for="pageindexLocalScriptPathInput">Local PageIndex Script Path</label>
            <input id="pageindexLocalScriptPathInput" type="text" value="${escapeHTML(cfg.pageindex_local_script_path || "")}" />
          </div>
          <div class="field full">
            <label for="paddleocrTokenInput">PaddleOCR Token</label>
            <input id="paddleocrTokenInput" type="password" value="${escapeHTML(cfg.paddleocr_token || "")}" />
          </div>
        </div>
      </section>
    </div>
  `;
}

function renderSettings() {
  mainView.innerHTML = `
    <div class="settings-page">
      ${settingsTabs()}
      ${(state.ui.settingsTab || "model-pool") === "general" ? renderGeneralSettings() : renderModelPool()}
    </div>
  `;
  const dialogProfile = state.modelProfileDialog?.profileId ? poolProfileById(state.modelProfileDialog.profileId) : null;
  if ($("#modelProfileWireApiInput")) $("#modelProfileWireApiInput").value = dialogProfile?.wire_api || "chat_completions";
  if ($("#ocrDetectionModeInput")) $("#ocrDetectionModeInput").value = state.config?.ocr_detection_mode || "auto_recommend";
  if ($("#ocrDefaultModelInput")) $("#ocrDefaultModelInput").value = state.config?.ocr_default_model || "PaddleOCR-VL-1.5";
  if ($("#pageindexLocalInstallationStateInput")) $("#pageindexLocalInstallationStateInput").value = state.config?.pageindex_local_installation_state || "not_installed";
  $("#useKbBtn")?.addEventListener("click", useKb);
  $("#createKbBtn")?.addEventListener("click", createKb);
  $("#saveSettingsBtn")?.addEventListener("click", saveSettings);
  $("#toggleApiKeyBtn")?.addEventListener("click", toggleApiKeyVisibility);
  $("#exportProfilesBtn")?.addEventListener("click", exportLlmConfig);
  $("#importProfilesBtn")?.addEventListener("click", () => $("#importProfilesInput").click());
  $("#importProfilesInput")?.addEventListener("change", importLlmConfig);
  $("#probeAllModelPoolBtn")?.addEventListener("click", probeAllModelPool);
  $("#saveModelPoolSettingsBtn")?.addEventListener("click", saveModelPoolSettings);
  const healthFilter = document.querySelector("[data-model-health-filter]");
  if (healthFilter) healthFilter.value = state.modelPoolHealthFilter || "all";
}
function settingsPayload() {
  const cfg = state.config || {};
  return {
    // language: $("#languageInput").value.trim()
    language: $("#languageInput") ? $("#languageInput").value.trim() : cfg.language || "en",
    pageindex_threshold: Number($("#thresholdInput")?.value || cfg.pageindex_threshold || 20),
    // compile_max_concurrency: Number($("#compileConcurrencyInput").value || 2)
    compile_max_concurrency: Number($("#compileConcurrencyInput")?.value || cfg.compile_max_concurrency || 2),
    // ocr_enabled: $("#ocrEnabledInput").checked
    ocr_enabled: $("#ocrEnabledInput") ? $("#ocrEnabledInput").checked : cfg.ocr_enabled !== false,
    // ocr_detection_mode: $("#ocrDetectionModeInput").value
    ocr_detection_mode: $("#ocrDetectionModeInput")?.value || cfg.ocr_detection_mode || "auto_recommend",
    // ocr_default_model: $("#ocrDefaultModelInput").value
    ocr_default_model: $("#ocrDefaultModelInput")?.value || cfg.ocr_default_model || "PaddleOCR-VL-1.5",
    // ocr_chunk_pages: Number($("#ocrChunkPagesInput").value || 100)
    ocr_chunk_pages: Number($("#ocrChunkPagesInput")?.value || cfg.ocr_chunk_pages || 100),
    // ocr_auto_recommend: $("#ocrAutoRecommendInput").checked
    ocr_auto_recommend: $("#ocrAutoRecommendInput") ? $("#ocrAutoRecommendInput").checked : cfg.ocr_auto_recommend !== false,
    // paddleocr_token: $("#paddleocrTokenInput").value
    paddleocr_token: $("#paddleocrTokenInput")?.value || cfg.paddleocr_token || "",
    // pageindex_local_enabled: $("#pageindexLocalEnabledInput").checked
    pageindex_local_enabled: $("#pageindexLocalEnabledInput") ? $("#pageindexLocalEnabledInput").checked : Boolean(cfg.pageindex_local_enabled),
    // pageindex_local_model: $("#pageindexLocalModelInput").value.trim()
    pageindex_local_model: $("#pageindexLocalModelInput")?.value.trim() || cfg.pageindex_local_model || "",
    // pageindex_local_installation_state: $("#pageindexLocalInstallationStateInput").value
    pageindex_local_installation_state: $("#pageindexLocalInstallationStateInput")?.value || cfg.pageindex_local_installation_state || "not_installed",
    // pageindex_local_repo_dir: $("#pageindexLocalRepoDirInput").value.trim()
    pageindex_local_repo_dir: $("#pageindexLocalRepoDirInput")?.value.trim() || cfg.pageindex_local_repo_dir || "",
    // pageindex_local_python_path: $("#pageindexLocalPythonPathInput").value.trim()
    pageindex_local_python_path: $("#pageindexLocalPythonPathInput")?.value.trim() || cfg.pageindex_local_python_path || "",
    // pageindex_local_script_path: $("#pageindexLocalScriptPathInput").value.trim()
    pageindex_local_script_path: $("#pageindexLocalScriptPathInput")?.value.trim() || cfg.pageindex_local_script_path || "",
  };
}

async function generateFixPlan(event) {
  const button = event?.currentTarget;
  if (!state.selectedReport) {
    notify("Select a lint report first.", "warning");
    return;
  }
  setButtonBusy(button, true, "Queueing...");
  try {
    const result = await api("/api/lint/fix-plan", {
      method: "POST",
      body: JSON.stringify({ kb_dir: state.kbDir, report: state.selectedReport }),
    });
    trackJob(result.job, "Fix plan queued");
  } catch (error) {
    setError(error.message);
  } finally {
    setButtonBusy(button, false);
  }
}

async function applyApprovedFixes(event) {
  const button = event?.currentTarget;
  const candidates = (state.fixPlan?.candidates || []).map((item) => ({
    ...item,
    approved: Boolean(isSelectableFix(item) && state.selectedFixes[fixKey(item)]),
  }));
  const approved = candidates.filter((item) => item.approved);
  if (!approved.length) {
    notify("Choose at least one fix candidate.", "warning");
    return;
  }
  setButtonBusy(button, true, "Queueing...");
  try {
    const result = await api("/api/lint/apply-fixes", {
      method: "POST",
      body: JSON.stringify({ kb_dir: state.kbDir, candidates }),
    });
    trackJob(result.job, "Approved fixes queued");
  } catch (error) {
    setError(error.message);
  } finally {
    setButtonBusy(button, false);
  }
}

async function useKb(event) {
  const button = event?.currentTarget;
  const path = $("#kbPathInput")?.value.trim() || state.kbDir || "";
  if (!path) {
    notify("Enter a knowledge base path first.", "warning");
    return;
  }
  setButtonBusy(button, true, "Switching...");
  try {
    await api("/api/kbs/use", { method: "POST", body: JSON.stringify({ path }) });
    notify("Knowledge base selected", "success");
    await loadAll();
  } catch (error) {
    setError(error.message);
  } finally {
    setButtonBusy(button, false);
  }
}

async function createKb(event) {
  const button = event?.currentTarget;
  const path = $("#kbPathInput")?.value.trim() || "";
  if (!path) {
    notify("Enter a knowledge base path first.", "warning");
    return;
  }
  setButtonBusy(button, true, "Creating...");
  try {
    await api("/api/kbs/init", {
      method: "POST",
      body: JSON.stringify({
        path,
        model: $("#modelInput")?.value.trim() || state.config?.model || "gpt-5.4-mini",
        language: $("#languageInput")?.value.trim() || state.config?.language || "en",
        pageindex_threshold: Number($("#thresholdInput")?.value || 20),
        compile_max_concurrency: Number($("#compileConcurrencyInput")?.value || 2),
        wire_api: $("#wireApiInput")?.value || "chat_completions",
        base_url: $("#baseUrlInput")?.value.trim() || "",
        api_key: $("#apiKeyInput")?.value || "",
      }),
    });
    notify("Knowledge base created", "success");
    await loadAll();
  } catch (error) {
    setError(error.message);
  } finally {
    setButtonBusy(button, false);
  }
}

async function exportLlmUsage(event) {
  const button = event?.currentTarget;
  if (!state.kbDir) {
    notify("Select or create a knowledge base first.", "warning");
    return;
  }
  setButtonBusy(button, true, "Exporting...");
  try {
    const url = new URL(withKb("/api/llm-usage/export"), window.location.origin);
    if (state.llmUsageSearch) url.searchParams.set("q", state.llmUsageSearch);
    const result = await api(`${url.pathname}${url.search}`);
    const blob = new Blob([JSON.stringify(result, null, 2)], { type: "application/json" });
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = "openkb-llm-usage.json";
    link.click();
    URL.revokeObjectURL(objectUrl);
    notify("LLM usage exported", "success");
  } catch (error) {
    setError(error.message);
  } finally {
    setButtonBusy(button, false);
  }
}

async function exportLlmConfig(event) {
  const button = event?.currentTarget;
  if (!state.kbDir) {
    notify("Select or create a knowledge base first.", "warning");
    return;
  }
  setButtonBusy(button, true, "Exporting...");
  try {
    const result = await api(withKb("/api/config/export"));
    const blob = new Blob([JSON.stringify(result, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "openkb-settings-config.json";
    link.click();
    URL.revokeObjectURL(url);
    notify("Settings exported", "success");
  } catch (error) {
    setError(error.message);
  } finally {
    setButtonBusy(button, false);
  }
}

async function importLlmConfig(event) {
  const input = event?.currentTarget;
  const file = input?.files?.[0];
  if (!file) return;
  try {
    const parsed = JSON.parse(await file.text());
    await api("/api/config/import", {
      method: "POST",
      body: JSON.stringify({ kb_dir: state.kbDir, config: parsed }),
    });
    notify("Settings imported", "success");
    await loadAll();
    render();
  } catch (error) {
    setError(error.message);
  } finally {
    if (input) input.value = "";
  }
}

async function saveSettings(event) {
  const button = event?.currentTarget;
  if (!state.kbDir) {
    notify("Select or create a knowledge base first.", "warning");
    return;
  }
  setButtonBusy(button, true, "Saving...");
  try {
    await api("/api/config", {
      method: "PUT",
      body: JSON.stringify({
        kb_dir: state.kbDir,
        ...settingsPayload(),
      }),
    });
    notify("Settings saved", "success");
    await loadAll();
    render();
  } catch (error) {
    setError(error.message);
  } finally {
    setButtonBusy(button, false);
  }
}

async function saveModelPoolSettings(event) {
  const button = event?.currentTarget;
  if (!state.kbDir) {
    notify("Select or create a knowledge base first.", "warning");
    return;
  }
  setButtonBusy(button, true, "Saving...");
  try {
    await api("/api/config", {
      method: "PUT",
      body: JSON.stringify({
        kb_dir: state.kbDir,
        model_pool_enabled: $("#modelPoolEnabledInput") ? $("#modelPoolEnabledInput").checked : true,
      }),
    });
    notify("Model pool settings saved", "success");
    await loadAll();
    render();
  } catch (error) {
    setError(error.message);
  } finally {
    setButtonBusy(button, false);
  }
}

async function saveConfig(event, options = {}) {
  return saveModelPoolProfile(event, options);
}

async function saveNewProfile(event) {
  return saveConfig(event, { createProfile: true });
}

function setFixPlanState(result) {
  const candidates = result?.candidates || [];
  state.fixPlan = {
    report: result?.report || state.selectedReport || null,
    candidates,
  };
  state.selectedFixes = Object.fromEntries(candidates.map((item) => [fixKey(item), false]));
  state.lastFixApply = null;
}

function captureFixPlan(result) {
  setFixPlanState(result);
  if (state.view === "reports") {
    renderReports();
  }
}

async function testLlm(event) {
  const button = event?.currentTarget;
  if (!state.kbDir) {
    notify("Select or create a knowledge base first.", "warning");
    return;
  }
  setButtonBusy(button, true, "Testing...");
  try {
    const result = await api("/api/config/test-llm", {
      method: "POST",
      body: JSON.stringify({
        kb_dir: state.kbDir,
        model: $("#modelInput")?.value.trim() || state.config?.model || "",
        ...settingsPayload(),
        wire_api: $("#wireApiInput")?.value || "chat_completions",
        base_url: $("#baseUrlInput")?.value.trim() || "",
        api_key: $("#apiKeyInput")?.value || "",
      }),
    });
    notify(result?.message || "LLM test succeeded.", "success");
  } catch (error) {
    notify(`LLM test failed: ${error.message}`, "error");
  } finally {
    setButtonBusy(button, false);
  }
}

async function probeModelPoolProfile(profileId, event) {
  if (!profileId || !state.kbDir) return;
  const button = event?.target?.closest?.("button") || event?.currentTarget?.closest?.("button") || event?.currentTarget || null;
  setButtonBusy(button, true, "Probing...");
  try {
    const result = await api(`/api/model-pool/profiles/${encodeURIComponent(profileId)}/probe`, {
      method: "POST",
      body: JSON.stringify({ kb_dir: state.kbDir }),
    });
    state.modelPool = result.model_pool || state.modelPool;
    notify("Model profile probed", "success");
    renderSettings();
  } catch (error) {
    notify(error.message, "error");
  } finally {
    setButtonBusy(button, false);
  }
}

async function probeAllModelPool(event) {
  const button = event?.currentTarget;
  if (!state.kbDir) return;
  setButtonBusy(button, true, "Probing...");
  try {
    const result = await api("/api/model-pool/probe", {
      method: "POST",
      body: JSON.stringify({ kb_dir: state.kbDir }),
    });
    state.modelPool = result.model_pool || state.modelPool;
    notify("Model pool probed", "success");
    renderSettings();
  } catch (error) {
    notify(error.message, "error");
  } finally {
    setButtonBusy(button, false);
  }
}

function openModelProfileDialog(profileId) {
  state.modelProfileDialog = { profileId: profileId || null };
  renderSettings();
  $("#modelProfileNameInput")?.focus();
}

function closeModelProfileDialog() {
  state.modelProfileDialog = null;
  renderSettings();
}

function modelProfilePayload() {
  return {
    kb_dir: state.kbDir,
    name: $("#modelProfileNameInput")?.value.trim() || "Model Endpoint",
    wire_api: $("#modelProfileWireApiInput")?.value || "chat_completions",
    base_url: $("#modelProfileBaseUrlInput")?.value.trim() || "",
    api_key: $("#apiKeyInput")?.value || "",
    enabled: $("#modelProfileEnabledInput") ? $("#modelProfileEnabledInput").checked : true,
    models: parseModelRows($("#modelProfileModelsInput")?.value || ""),
  };
}

async function saveModelPoolProfile(event) {
  if (!state.kbDir) return;
  const button = event?.target?.closest("button");
  const profileId = state.modelProfileDialog?.profileId || "";
  const payload = modelProfilePayload();
  if (!payload.models.length) {
    notify("Add at least one model.", "warning");
    return;
  }
  setButtonBusy(button, true, "Saving...");
  try {
    const result = await api(profileId ? `/api/model-pool/profiles/${encodeURIComponent(profileId)}` : "/api/model-pool/profiles", {
      method: profileId ? "PUT" : "POST",
      body: JSON.stringify(payload),
    });
    state.config = result.config || state.config;
    state.modelPool = result.model_pool || state.modelPool;
    state.modelProfileDialog = null;
    notify("Model endpoint saved", "success");
    renderSettings();
  } catch (error) {
    notify(error.message, "error");
  } finally {
    setButtonBusy(button, false);
  }
}

async function deleteModelPoolProfile(profileId, event) {
  if (!profileId || !state.kbDir) return;
  const profile = poolProfileById(profileId);
  if (!window.confirm(`Delete ${profile?.name || profileId} from the model pool?`)) return;
  const button = event?.target?.closest("button");
  setButtonBusy(button, true, "Deleting...");
  try {
    const result = await api(`/api/model-pool/profiles/${encodeURIComponent(profileId)}`, {
      method: "DELETE",
      body: JSON.stringify({ kb_dir: state.kbDir }),
    });
    state.config = result.config || state.config;
    state.modelPool = result.model_pool || state.modelPool;
    notify("Model endpoint deleted", "success");
    renderSettings();
  } catch (error) {
    notify(error.message, "error");
  } finally {
    setButtonBusy(button, false);
  }
}

async function toggleModelPoolProfile(profileId, enable, event) {
  if (!profileId || !state.kbDir) return;
  const button = event?.target?.closest("button");
  setButtonBusy(button, true, enable ? "Enabling..." : "Disabling...");
  try {
    const result = await api(`/api/model-pool/profiles/${encodeURIComponent(profileId)}/${enable ? "enable" : "disable"}`, {
      method: "POST",
      body: JSON.stringify({ kb_dir: state.kbDir }),
    });
    state.config = result.config || state.config;
    state.modelPool = result.model_pool || state.modelPool;
    notify(enable ? "Profile enabled" : "Profile disabled", "success");
    renderSettings();
  } catch (error) {
    notify(error.message, "error");
  } finally {
    setButtonBusy(button, false);
  }
}

function bindViewButtons() {
  mainView.querySelectorAll("[data-view-target]").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.viewTarget));
  });
}

function switchView(view) {
  state.view = view;
  const workspace = $(".workspace");
  if (workspace) workspace.scrollTop = 0;
  render();
  if (view === "usage" && state.kbDir) {
    loadLlmUsage().then(() => {
      if (state.view === "usage") renderLlmUsage();
    }).catch((error) => {
      notify(error.message, "error");
    });
  }
}

function parseSseChunk(buffer, onEvent) {
  let rest = buffer;
  while (rest.includes("\n\n")) {
    const index = rest.indexOf("\n\n");
    const raw = rest.slice(0, index);
    rest = rest.slice(index + 2);
    const lines = raw.split("\n");
    const event = (lines.find((line) => line.startsWith("event: ")) || "event: message").slice(7);
    const data = lines
      .filter((line) => line.startsWith("data: "))
      .map((line) => line.slice(6))
      .join("\n");
    if (data) onEvent(event, JSON.parse(data));
  }
  return rest;
}

function handleQueryStreamEvent(event, payload, liveText) {
  switch (event) {
    case "session":
      state.activeChatSessionId = payload.session_id || state.activeChatSessionId;
      return liveText;
    case "delta": {
      const next = liveText + (payload.text || "");
      renderAssistantAnswer(next, state.activeQueryReferences);
      return next;
    }
    case "done":
      state.activeChatSessionId = payload.session_id || state.activeChatSessionId;
      state.activeChatSession = payload.session || state.activeChatSession;
      state.activeQueryReferences = payload.references || [];
      renderAssistantAnswer(renderChatTranscript() || payload.answer || "", state.activeQueryReferences);
      loadKnowledgeData().then(() => {
        if (state.view === "sessions") renderSessions();
      }).catch(() => {});
      return payload.answer || liveText;
    case "error":
      throw new Error(payload.message || "Query failed");
    default:
      return liveText;
  }
}

async function streamQuery(payload, onEvent) {
  const response = await fetch("/api/query/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok || !response.body) {
    throw new Error(response.statusText || "Query failed");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let liveText = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replaceAll("\r\n", "\n");
    buffer = parseSseChunk(buffer, (event, data) => {
      liveText = onEvent(event, data, liveText);
    });
  }
  buffer += decoder.decode();
  parseSseChunk(buffer, (event, data) => {
    liveText = onEvent(event, data, liveText);
  });
  return liveText;
}

async function askQuestion(event) {
  const button = event?.currentTarget || $("#askBtn");
  const question = $("#questionInput").value.trim();
  if (!question || !state.kbDir) {
    notify(state.kbDir ? "Enter a question first." : "Select a knowledge base first.", "warning");
    return;
  }
  state.activeQueryReferences = [];
  $("#answerBox").textContent = "";
  setButtonBusy(button, true, "Asking...");
  try {
    state.ui.utilityTab = "assistant";
    renderUtilityPanel();
    await streamQuery(
      {
        kb_dir: state.kbDir,
        question,
        session_id: state.activeChatSessionId,
        save: $("#saveQueryInput").checked,
      },
      handleQueryStreamEvent,
    );
  } catch (error) {
    $("#answerBox").textContent = error.message;
    notify(error.message, "error");
  } finally {
    setButtonBusy(button, false);
  }
}

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => switchView(button.dataset.view));
});

document.addEventListener("click", handleAppClick);
document.addEventListener("input", handleAppInput);

$("#refreshBtn").addEventListener("click", loadAll);
$("#askBtn").addEventListener("click", askQuestion);
$("#questionInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    askQuestion();
  }
});
$("#clearAnswerBtn").addEventListener("click", () => {
  $("#answerBox").textContent = "";
});

setInterval(loadJobs, 1200);
setInterval(autoProbeModelPool, 60000);
loadAll();

