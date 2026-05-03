const state = {
  view: "overview",
  kbDir: null,
  status: null,
  documents: null,
  wikiTree: [],
  selectedWikiPath: "index.md",
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
  jobStatuses: {},
  loadingAll: false,
};

const $ = (selector) => document.querySelector(selector);
const mainView = $("#mainView");
const viewTitle = $("#viewTitle h2");
const viewMeta = $("#viewMeta");

const viewLabels = {
  overview: "Overview",
  documents: "Documents",
  wiki: "Wiki",
  sessions: "Sessions",
  reports: "Quality",
  settings: "Settings",
};

const jobLabels = {
  add: "Add",
  lint: "Lint",
  lint_fix_plan: "Fix Plan",
  lint_fix_apply: "Apply Fixes",
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
  const cls = status === "succeeded" ? "good" : status === "failed" ? "bad" : status === "running" ? "warn" : "muted";
  return `<span class="badge ${cls}">${escapeHTML(status)}</span>`;
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function jobProgress(job) {
  const current = Math.max(Number(job.progress?.current || 0), 0);
  const total = Math.max(Number(job.progress?.total || 0), 0);
  const pct = total ? Math.min(100, Math.round((current / total) * 100)) : job.status === "succeeded" ? 100 : 0;
  return { current, total, pct };
}

function progressMarkup(job) {
  const progress = jobProgress(job);
  const indeterminate = job.status === "running" && !progress.total;
  const width = indeterminate ? 38 : progress.pct;
  const label = progress.total ? `${progress.current}/${progress.total}` : indeterminate ? "running" : `${progress.pct}%`;
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
  const [status, documents, tree, chats, config] = await Promise.all([
    api(withKb("/api/status")),
    api(withKb("/api/documents")),
    api(withKb("/api/wiki/tree")),
    api(withKb("/api/chats")),
    api(withKb("/api/config")),
  ]);
  state.status = status;
  state.documents = documents;
  state.wikiTree = tree.files || [];
  state.chats = chats.sessions || [];
  state.config = config;
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
    } else {
      state.status = null;
      state.documents = null;
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
    renderJobs();
    handleJobTransitions(previousStatuses);
    handleQueryJob();
  } catch (_) {
    state.jobs = [];
    renderJobs();
  }
}

function handleJobTransitions(previousStatuses) {
  let shouldRefresh = false;
  state.jobs.forEach((job) => {
    const previous = previousStatuses[job.id];
    if (previous !== "running" || job.status === "running") return;
    if (job.status === "succeeded") {
      notify(`${jobLabels[job.type] || job.type} finished`, "success");
      if (job.type === "lint_fix_plan") {
        captureFixPlan(job.result);
      }
      if (job.type === "lint_fix_apply") {
        state.lastFixApply = job.result || null;
      }
      shouldRefresh = shouldRefresh || ["add", "lint", "query", "lint_fix_apply"].includes(job.type);
    } else if (job.status === "failed") {
      notify(job.error || `${jobLabels[job.type] || job.type} failed`, "error");
    }
  });
  if (shouldRefresh && !state.loadingAll) {
    refreshKnowledgeData();
  }
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

function handleQueryJob() {
  if (!state.queryJobId) return;
  const job = state.jobs.find((item) => item.id === state.queryJobId);
  if (!job) return;
  if (job.status === "succeeded") {
    $("#answerBox").textContent = job.result?.answer || "";
    state.queryJobId = null;
  } else if (job.status === "failed") {
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
  renderJobs();
}

function trackJob(job, message) {
  if (!job) return;
  state.jobs = [job, ...state.jobs.filter((item) => item.id !== job.id)];
  state.jobStatuses[job.id] = job.status;
  state.selectedJobId = job.id;
  renderJobs();
  notify(message || `${jobLabels[job.type] || job.type} queued`, "info");
}

function renderJobs() {
  const list = $("#jobsList");
  if (!list) return;
  $("#jobCount").textContent = String(state.jobs.length);
  if (!state.jobs.length) {
    list.innerHTML = `<div class="empty">No jobs</div>`;
    renderJobDetails();
    return;
  }
  list.innerHTML = state.jobs
    .slice(0, 10)
    .map((job) => {
      const active = job.id === state.selectedJobId ? " active" : "";
      return `
        <button class="job-item${active}" type="button" data-job-id="${escapeHTML(job.id)}">
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
    .join("");
  list.querySelectorAll("[data-job-id]").forEach((button) => {
    button.addEventListener("click", () => selectJob(button.dataset.jobId));
  });
  renderJobDetails();
}

function renderJobDetails() {
  const details = $("#jobDetails");
  if (!details) return;
  if (!state.jobs.length) {
    details.innerHTML = `
      <div class="empty">No job activity yet.</div>
      <div id="jobLogList" class="job-log-list hidden"></div>
    `;
    return;
  }
  const job = state.jobs.find((item) => item.id === state.selectedJobId) || state.jobs[0];
  state.selectedJobId = job.id;
  const logs = job.logs || [];
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

  if (state.view === "overview") renderOverview();
  if (state.view === "documents") renderDocuments();
  if (state.view === "wiki") renderWiki();
  if (state.view === "sessions") renderSessions();
  if (state.view === "reports") renderReports();
  if (state.view === "settings") renderSettings();
}

function renderOverview() {
  const dirs = state.status?.directories || {};
  const cfg = state.config || {};
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
            <span class="muted-text">${escapeHTML(cfg.wire_api || "responses")}</span>
          </div>
          ${cfg.api_key_configured ? `<span class="badge good">Key set</span>` : `<span class="badge warn">No key</span>`}
        </header>
        <div class="section-body runtime-list">
          <div><span>Model</span><strong>${escapeHTML(cfg.model || "")}</strong></div>
          <div><span>Language</span><strong>${escapeHTML(cfg.language || "")}</strong></div>
          <div><span>Base URL</span><strong>${escapeHTML(cfg.base_url || "Default")}</strong></div>
        </div>
      </section>
    </div>
  `;
  bindViewButtons();
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

function documentsTable(limit) {
  const docs = (state.documents?.documents || []).slice(0, limit || undefined);
  if (!docs.length) return `<div class="empty">No documents</div>`;
  return `
    <table>
      <thead><tr><th>Name</th><th>Type</th><th>Pages</th></tr></thead>
      <tbody>
        ${docs.map((doc) => `<tr><td>${escapeHTML(doc.name)}</td><td>${escapeHTML(doc.type)}</td><td>${escapeHTML(doc.pages || "")}</td></tr>`).join("")}
      </tbody>
    </table>
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
    <div class="content-grid">
      <section class="section">
        <header><h3>Add</h3></header>
        <div class="section-body">
          <div class="field full">
            <label for="addPathInput">Path</label>
            <input id="addPathInput" type="text" placeholder="D:\\path\\to\\folder-or-document.pdf" />
          </div>
          <div class="row-actions">
            <button id="addPathBtn" class="primary" type="button">Add Folder / File</button>
          </div>
          <hr />
          <div class="field full">
            <label for="uploadInput">File</label>
            <input id="uploadInput" type="file" />
          </div>
          <div class="row-actions">
            <button id="uploadBtn" type="button">Upload</button>
          </div>
        </div>
      </section>
      <section class="section">
        <header><h3>Indexed</h3></header>
        <div class="section-body">${documentsTable()}</div>
      </section>
    </div>
  `;
  $("#addPathBtn").addEventListener("click", addPath);
  $("#uploadBtn").addEventListener("click", uploadFile);
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
      body: JSON.stringify({ kb_dir: state.kbDir, path }),
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
  const form = new FormData();
  form.append("file", input.files[0]);
  setButtonBusy(button, true, "Uploading...");
  try {
    const result = await api(`/api/documents/upload?kb_dir=${encodeURIComponent(state.kbDir)}`, {
      method: "POST",
      body: form,
    });
    input.value = "";
    trackJob(result.job, "Upload queued");
  } catch (error) {
    setError(error.message);
  } finally {
    setButtonBusy(button, false);
  }
}

function renderWiki() {
  const files = state.wikiTree || [];
  if (!files.some((file) => file.path === state.selectedWikiPath) && files.length) {
    state.selectedWikiPath = files[0].path;
  }
  mainView.innerHTML = `
    <div class="wiki-layout">
      <div class="file-list">
        ${files.map((file) => `<button type="button" data-wiki-path="${escapeHTML(file.path)}" class="${file.path === state.selectedWikiPath ? "active" : ""}">${escapeHTML(file.path)}</button>`).join("") || `<div class="empty">No files</div>`}
      </div>
      <div class="editor-pane">
        <div class="row-actions">
          <input id="wikiPathInput" type="text" value="${escapeHTML(state.selectedWikiPath)}" />
          <button id="loadWikiBtn" type="button">Load</button>
          <button id="saveWikiBtn" class="primary" type="button">Save</button>
        </div>
        <textarea id="wikiEditor" spellcheck="false"></textarea>
        <pre id="wikiPreview" class="preview"></pre>
      </div>
    </div>
  `;
  mainView.querySelectorAll("[data-wiki-path]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedWikiPath = button.dataset.wikiPath;
      renderWiki();
      loadWikiFile();
    });
  });
  $("#loadWikiBtn").addEventListener("click", (event) => {
    state.selectedWikiPath = $("#wikiPathInput").value.trim();
    loadWikiFile(event);
  });
  $("#saveWikiBtn").addEventListener("click", saveWikiFile);
  $("#wikiEditor").addEventListener("input", () => {
    $("#wikiPreview").textContent = $("#wikiEditor").value;
  });
  loadWikiFile();
}

async function loadWikiFile(event) {
  if (!state.selectedWikiPath) return;
  const button = event?.currentTarget;
  setButtonBusy(button, true, "Loading...");
  try {
    const file = await api(withKb(`/api/wiki/file?path=${encodeURIComponent(state.selectedWikiPath)}`));
    if (!$("#wikiEditor")) return;
    $("#wikiPathInput").value = file.path;
    $("#wikiEditor").value = file.content;
    $("#wikiPreview").textContent = file.content;
  } catch (error) {
    if ($("#wikiPreview")) $("#wikiPreview").textContent = error.message;
    notify(error.message, "error");
  } finally {
    setButtonBusy(button, false);
  }
}

async function saveWikiFile(event) {
  const button = event?.currentTarget;
  setButtonBusy(button, true, "Saving...");
  try {
    await api("/api/wiki/file", {
      method: "PUT",
      body: JSON.stringify({
        kb_dir: state.kbDir,
        path: $("#wikiPathInput").value.trim(),
        content: $("#wikiEditor").value,
      }),
    });
    notify("Wiki page saved", "success");
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
  mainView.innerHTML = `
    <section class="section">
      <header><h3>Chat Sessions</h3></header>
      <div class="section-body">
        ${
          sessions.length
            ? `<table><thead><tr><th>ID</th><th>Turns</th><th>Updated</th><th>Title</th><th></th></tr></thead><tbody>
              ${sessions.map((session) => `<tr><td>${escapeHTML(session.id)}</td><td>${escapeHTML(session.turn_count)}</td><td>${escapeHTML(session.updated_at)}</td><td>${escapeHTML(session.title || "")}</td><td><button class="danger" type="button" data-delete-chat="${escapeHTML(session.id)}">Delete</button></td></tr>`).join("")}
            </tbody></table>`
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

function renderReports() {
  const reports = state.documents?.reports || [];
  syncSelectedReport(reports);
  syncFixPlanFromJobs();
  const candidates = state.fixPlan?.candidates || [];
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
            ${reports.map((name) => {
              const path = `reports/${name}`;
              const active = path === state.selectedReport ? " active" : "";
              return `<button type="button" class="report-button${active}" data-report="${escapeHTML(path)}">${escapeHTML(name)}</button>`;
            }).join("") || `<div class="empty">No reports</div>`}
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
  return candidates
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
    .join("");
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

function renderSettings() {
  const cfg = state.config || {};
  mainView.innerHTML = `
    <div class="content-grid">
      <section class="section">
        <header><h3>Knowledge Base</h3></header>
        <div class="section-body form-grid">
          <div class="field full">
            <label for="kbPathInput">Path</label>
            <input id="kbPathInput" type="text" value="${escapeHTML(state.kbDir || "")}" />
          </div>
          <div class="row-actions full">
            <button id="useKbBtn" type="button">Use</button>
            <button id="createKbBtn" class="primary" type="button">Create</button>
          </div>
        </div>
      </section>
      <section class="section">
        <header><h3>Config</h3>${cfg.api_key_configured ? `<span class="badge good">Key set</span>` : `<span class="badge warn">No key</span>`}</header>
        <div class="section-body form-grid">
          <div class="field">
            <label for="modelInput">Model</label>
            <input id="modelInput" type="text" value="${escapeHTML(cfg.model || "gpt-5.4-mini")}" />
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
            <label for="wireApiInput">Wire API</label>
            <select id="wireApiInput">
              <option value="responses">responses</option>
              <option value="chat_completions">chat_completions</option>
            </select>
          </div>
          <div class="field full">
            <label for="baseUrlInput">Base URL</label>
            <input id="baseUrlInput" type="url" value="${escapeHTML(cfg.base_url || "")}" placeholder="https://api.example.com/v1" />
          </div>
          <div class="field full">
            <label for="apiKeyInput">API Key</label>
            <input id="apiKeyInput" type="password" value="" />
          </div>
          <div class="row-actions full">
            <button id="testLlmBtn" type="button">Test LLM</button>
            <button id="saveConfigBtn" class="primary" type="button">Save Config</button>
          </div>
        </div>
      </section>
    </div>
  `;
  $("#wireApiInput").value = cfg.wire_api || "responses";
  $("#useKbBtn").addEventListener("click", useKb);
  $("#createKbBtn").addEventListener("click", createKb);
  $("#testLlmBtn").addEventListener("click", testLlm);
  $("#saveConfigBtn").addEventListener("click", saveConfig);
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
  const path = $("#kbPathInput").value.trim();
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
  const path = $("#kbPathInput").value.trim();
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
        model: $("#modelInput").value.trim(),
        language: $("#languageInput").value.trim(),
        pageindex_threshold: Number($("#thresholdInput").value || 20),
        wire_api: $("#wireApiInput").value,
        base_url: $("#baseUrlInput").value.trim(),
        api_key: $("#apiKeyInput").value,
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

async function saveConfig(event) {
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
        model: $("#modelInput").value.trim(),
        language: $("#languageInput").value.trim(),
        pageindex_threshold: Number($("#thresholdInput").value || 20),
        wire_api: $("#wireApiInput").value,
        base_url: $("#baseUrlInput").value.trim(),
        api_key: $("#apiKeyInput").value,
      }),
    });
    notify("Config saved", "success");
    await loadAll();
  } catch (error) {
    setError(error.message);
  } finally {
    setButtonBusy(button, false);
  }
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
        model: $("#modelInput").value.trim(),
        language: $("#languageInput").value.trim(),
        pageindex_threshold: Number($("#thresholdInput").value || 20),
        wire_api: $("#wireApiInput").value,
        base_url: $("#baseUrlInput").value.trim(),
        api_key: $("#apiKeyInput").value,
      }),
    });
    notify(result?.message || "LLM test succeeded.", "success");
  } catch (error) {
    notify(`LLM test failed: ${error.message}`, "error");
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
  render();
}

async function askQuestion(event) {
  const button = event?.currentTarget || $("#askBtn");
  const question = $("#questionInput").value.trim();
  if (!question || !state.kbDir) {
    notify(state.kbDir ? "Enter a question first." : "Select a knowledge base first.", "warning");
    return;
  }
  $("#answerBox").textContent = "Queueing query...";
  setButtonBusy(button, true, "Queueing...");
  try {
    const result = await api("/api/query", {
      method: "POST",
      body: JSON.stringify({
        kb_dir: state.kbDir,
        question,
        save: $("#saveQueryInput").checked,
      }),
    });
    state.queryJobId = result.job.id;
    trackJob(result.job, "Query queued");
    renderQueryProgress(result.job);
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
loadAll();
