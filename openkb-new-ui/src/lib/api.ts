import axios from 'axios';

const apiClient = axios.create({
  baseURL: '/api',
  headers: {
    'Content-Type': 'application/json',
  },
});

export interface KnownKb {
  path: string;
  exists: boolean;
  is_kb: boolean;
  is_default: boolean;
}

export interface KbListResponse {
  default_kb: string | null;
  known_kbs: KnownKb[];
}

export interface KbStatusResponse {
  kb_dir: string;
  directories: Record<string, number>;
  total_indexed: number;
  last_compile: string | null;
  last_lint: string | null;
}

export type WorkflowStateName =
  | 'ingest_state'
  | 'ocr_state'
  | 'source_state'
  | 'summary_state'
  | 'review_state'
  | 'promotion_state';

export interface DocumentWorkflowState {
  ingest_state: string;
  ocr_state: string;
  source_state: string;
  summary_state: string;
  review_state: string;
  promotion_state: string;
}

export interface DocumentReviewState {
  ingest_score: number | null;
  summary_score: number | null;
  promotion_score: number | null;
  review_notes: string;
  recommended_ingest_mode: string;
  approved_by: string;
  approved_at: string | null;
}

export interface DocumentExecutionState {
  last_error: string;
  retry_count: number;
  updated_at: string | null;
}

export interface RelatedPageEntry {
  path: string;
  page: string;
  title: string;
  shared: boolean;
}

export interface DocumentItem {
  hash: string;
  name: string;
  type: string;
  pages: number | string;
  stem: string;
  raw_path: string;
  raw_exists: boolean;
  source_path: string | null;
  source_summary: string | null;
  summary_exists: boolean;
  ingested_at: string | null;
  ingested_date: string | null;
  related_count: number;
  related_pages: {
    summaries: RelatedPageEntry[];
    companies: RelatedPageEntry[];
    industries: RelatedPageEntry[];
    concepts: RelatedPageEntry[];
  };
  source_kind: string;
  scan_detected: boolean;
  workflow_state: DocumentWorkflowState;
  review: DocumentReviewState;
  execution: DocumentExecutionState;
}

export interface DocumentListResponse {
  documents: DocumentItem[];
  summaries: string[];
  concepts: string[];
  reports: string[];
}

export interface DocumentQueryParams {
  q?: string;
  ingest_state?: string;
  ocr_state?: string;
  source_state?: string;
  summary_state?: string;
  review_state?: string;
  promotion_state?: string;
}

export interface JobPayload {
  id: string;
  type: string;
  status: string;
  message?: string;
  error?: string | null;
  progress?: {
    current: number;
    total: number;
  };
  result?: unknown;
}

export interface JobEnvelope {
  job: JobPayload;
}

export interface ReviewUpdatePayload {
  file_hash: string;
  review_state: string;
  summary_score?: number | null;
  review_notes?: string;
  approved_by?: string;
}

export interface ChatSessionSummary {
  id: string;
  title: string;
  turn_count: number;
  updated_at: string;
  model: string;
}

export interface ChatSessionDetail extends ChatSessionSummary {
  created_at: string;
  language: string;
  history: unknown[];
  user_turns: string[];
  assistant_texts: string[];
}

export interface ChatListResponse {
  sessions: ChatSessionSummary[];
}

export interface ChatReference {
  type?: string;
  path?: string;
  pages?: string;
  doc_name?: string;
  query?: string;
  top_k?: number;
}

export interface StreamQueryPayload {
  kb_dir: string;
  question: string;
  session_id?: string;
  save?: boolean;
}

export interface StreamQueryDonePayload {
  answer: string;
  session_id: string;
  session?: ChatSessionDetail;
  references?: ChatReference[];
}

export interface StreamQueryCallbacks {
  onSession?: (sessionId: string) => void;
  onDelta?: (text: string) => void;
  onDone?: (payload: StreamQueryDonePayload) => void;
}

export interface ConfigProfile {
  id: string;
  name: string;
  model: string;
  wire_api: string;
  base_url: string;
  provider: string;
  reasoning_effort: string;
  thinking_enabled: boolean;
  enabled: boolean;
  tags: string[];
  features: string[];
  probe_models: string[];
  models: Array<{ name: string; weight: number }>;
  priority: number;
  api_key: string;
  api_key_configured: boolean;
  is_active: boolean;
}

export interface ConfigData {
  model: string;
  language: string;
  pageindex_threshold: number;
  compile_max_concurrency: number;
  ingest_gate_enabled: boolean;
  ingest_gate_pass_threshold: number;
  ingest_gate_hold_threshold: number;
  ingest_gate_hard_reject_enabled: boolean;
  ingest_gate_log_all_decisions: boolean;
  ingest_gate_allow_force_pass: boolean;
  ingest_gate_allow_force_reject: boolean;
  ocr_enabled: boolean;
  ocr_detection_mode: string;
  ocr_default_model: string;
  ocr_chunk_pages: number;
  ocr_auto_recommend: boolean;
  paddleocr_token: string;
  pageindex_local_enabled: boolean;
  pageindex_local_model: string;
  pageindex_local_installation_state: string;
  pageindex_local_repo_dir: string;
  pageindex_local_python_path: string;
  pageindex_local_script_path: string;
  pageindex_local_version: string;
  wire_api: string;
  base_url: string;
  api_key: string;
  api_key_configured: boolean;
  active_profile: string;
  profiles: ConfigProfile[];
}

export interface ModelPoolRoute {
  id: string;
  profile_id: string;
  model: string;
  weight: number;
  health: string;
  latency_ms: number | null;
  base_url: string;
  wire_api: string;
  last_error?: string;
}

export interface ModelPoolProfile extends ConfigProfile {
  health: string;
  last_checked_at: string;
  latency_ms: number | null;
  consecutive_failures: number;
  available_models: string[];
  failed_models: Record<string, string>;
  last_error: string;
  routes: ModelPoolRoute[];
}

export interface ModelPoolData {
  enabled: boolean;
  strategy: string;
  probe_interval_seconds: number;
  failure_threshold: number;
  timeout_seconds: number;
  active_profile: string;
  summary: Record<string, number>;
  profiles: ModelPoolProfile[];
}

export interface PageIndexLocalStatus {
  enabled: boolean;
  ready: boolean;
  installation_state: string;
  root: string;
  manifest: Record<string, unknown>;
}

export interface TestLlmResponse {
  ok?: boolean;
  message?: string;
}

const DEFAULT_WORKFLOW_STATE: DocumentWorkflowState = {
  ingest_state: 'imported',
  ocr_state: 'not_needed',
  source_state: 'queued',
  summary_state: 'not_started',
  review_state: 'unreviewed',
  promotion_state: 'not_selected',
};

const DEFAULT_REVIEW_STATE: DocumentReviewState = {
  ingest_score: null,
  summary_score: null,
  promotion_score: null,
  review_notes: '',
  recommended_ingest_mode: '',
  approved_by: '',
  approved_at: null,
};

const DEFAULT_EXECUTION_STATE: DocumentExecutionState = {
  last_error: '',
  retry_count: 0,
  updated_at: null,
};

const DEFAULT_RELATED_PAGES = {
  summaries: [] as RelatedPageEntry[],
  companies: [] as RelatedPageEntry[],
  industries: [] as RelatedPageEntry[],
  concepts: [] as RelatedPageEntry[],
};

export const getKbs = async (): Promise<KbListResponse> => {
  const response = await apiClient.get('/kbs');
  return response.data;
};

export const getKbStats = async (kbDir: string): Promise<KbStatusResponse | null> => {
  if (!kbDir) return null;
  const response = await apiClient.get('/status', { params: { kb_dir: kbDir } });
  return response.data;
};

export const getDocuments = async (
  kbDir: string,
  params: DocumentQueryParams = {},
): Promise<DocumentListResponse> => {
  const response = await apiClient.get('/documents', {
    params: {
      kb_dir: kbDir,
      ...params,
    },
  });
  return normalizeDocumentListResponse(response.data);
};

export const importDocuments = async (
  kbDir: string,
  path: string,
  options: { force?: boolean; strategy_override?: string | null } = {},
): Promise<JobEnvelope> => {
  const response = await apiClient.post('/documents/import', {
    kb_dir: kbDir,
    path,
    force: options.force ?? false,
    strategy_override: options.strategy_override ?? null,
  });
  return response.data;
};

export const uploadDocuments = async (
  kbDir: string,
  files: File[],
  options: { import_only?: boolean; force?: boolean } = {},
): Promise<JobEnvelope> => {
  const formData = new FormData();
  for (const file of files) {
    formData.append('file', file);
  }
  const response = await apiClient.post('/documents/upload', formData, {
    params: {
      kb_dir: kbDir,
      import_only: options.import_only ?? true,
      force: options.force ?? false,
    },
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return response.data;
};

export const summarizeDocuments = async (
  kbDir: string,
  fileHashes: string[],
  options: { force?: boolean; model?: string | null } = {},
): Promise<JobEnvelope> => {
  const response = await apiClient.post('/documents/summarize', {
    kb_dir: kbDir,
    file_hashes: fileHashes,
    force: options.force ?? false,
    model: options.model ?? null,
  });
  return response.data;
};

export const reviewSummaries = async (
  kbDir: string,
  reviews: ReviewUpdatePayload[],
): Promise<JobEnvelope> => {
  const response = await apiClient.post('/documents/review-summary', {
    kb_dir: kbDir,
    reviews,
  });
  return response.data;
};

export const promoteDocuments = async (
  kbDir: string,
  fileHashes: string[],
  options: { force?: boolean; model?: string | null } = {},
): Promise<JobEnvelope> => {
  const response = await apiClient.post('/documents/promote', {
    kb_dir: kbDir,
    file_hashes: fileHashes,
    force: options.force ?? false,
    model: options.model ?? null,
  });
  return response.data;
};

export const deleteDocument = async (kbDir: string, selector: string): Promise<JobEnvelope> => {
  const response = await apiClient.delete(`/documents/${encodeURIComponent(selector)}`, {
    params: { kb_dir: kbDir },
  });
  return response.data;
};

export const getJob = async (jobId: string): Promise<JobPayload | null> => {
  if (!jobId) return null;
  const response = await apiClient.get(`/jobs/${jobId}`);
  return response.data;
};

export const getChats = async (kbDir: string): Promise<ChatListResponse> => {
  const response = await apiClient.get('/chats', {
    params: { kb_dir: kbDir },
  });
  return {
    sessions: Array.isArray(response.data?.sessions)
      ? response.data.sessions.map((item: unknown) => normalizeChatSessionSummary(item))
      : [],
  };
};

export const getChatSession = async (kbDir: string, sessionId: string): Promise<ChatSessionDetail> => {
  const response = await apiClient.get(`/chats/${encodeURIComponent(sessionId)}`, {
    params: { kb_dir: kbDir },
  });
  return normalizeChatSessionDetail(response.data);
};

export const deleteChatSession = async (kbDir: string, sessionId: string): Promise<{ deleted: boolean }> => {
  const response = await apiClient.delete(`/chats/${encodeURIComponent(sessionId)}`, {
    params: { kb_dir: kbDir },
  });
  return { deleted: Boolean(response.data?.deleted) };
};

export const getConfig = async (kbDir: string): Promise<ConfigData> => {
  const response = await apiClient.get('/config', {
    params: { kb_dir: kbDir },
  });
  return normalizeConfigData(response.data);
};

export const updateConfig = async (kbDir: string, updates: Record<string, unknown>): Promise<ConfigData> => {
  const response = await apiClient.put('/config', {
    kb_dir: kbDir,
    ...updates,
  });
  return normalizeConfigData(response.data);
};

export const exportConfig = async (kbDir: string): Promise<Record<string, unknown>> => {
  const response = await apiClient.get('/config/export', {
    params: { kb_dir: kbDir },
  });
  return isRecord(response.data) ? response.data : {};
};

export const importConfig = async (kbDir: string, config: Record<string, unknown>): Promise<ConfigData> => {
  const response = await apiClient.post('/config/import', {
    kb_dir: kbDir,
    config,
  });
  return normalizeConfigData(response.data);
};

export const testLlmConfig = async (payload: Record<string, unknown>): Promise<TestLlmResponse> => {
  const response = await apiClient.post('/config/test-llm', payload);
  return isRecord(response.data) ? (response.data as TestLlmResponse) : {};
};

export const selectKbDirectory = async (path: string): Promise<{ kb_dir: string }> => {
  const response = await apiClient.post('/kbs/use', { path });
  return { kb_dir: String(response.data?.kb_dir ?? '') };
};

export const createKnowledgeBase = async (payload: Record<string, unknown>): Promise<{ kb_dir: string; config?: ConfigData }> => {
  const response = await apiClient.post('/kbs/init', payload);
  return {
    kb_dir: String(response.data?.kb_dir ?? ''),
    config: isRecord(response.data?.config) ? normalizeConfigData(response.data.config) : undefined,
  };
};

export const getPageIndexLocalStatus = async (kbDir: string): Promise<PageIndexLocalStatus> => {
  const response = await apiClient.get('/pageindex-local/status', {
    params: { kb_dir: kbDir },
  });
  return normalizePageIndexLocalStatus(response.data);
};

export const getModelPool = async (kbDir: string): Promise<ModelPoolData> => {
  const response = await apiClient.get('/model-pool', {
    params: { kb_dir: kbDir },
  });
  return normalizeModelPoolData(response.data);
};

export const probeAllModelPoolProfiles = async (kbDir: string): Promise<ModelPoolData> => {
  const response = await apiClient.post('/model-pool/probe', { kb_dir: kbDir });
  return normalizeModelPoolData(response.data?.model_pool ?? response.data);
};

export const createModelPoolProfile = async (kbDir: string, payload: Record<string, unknown>): Promise<ModelPoolData> => {
  const response = await apiClient.post('/model-pool/profiles', {
    kb_dir: kbDir,
    ...payload,
  });
  return normalizeModelPoolData(response.data?.model_pool ?? response.data);
};

export const updateModelPoolProfile = async (
  kbDir: string,
  profileId: string,
  payload: Record<string, unknown>,
): Promise<ModelPoolData> => {
  const response = await apiClient.put(`/model-pool/profiles/${encodeURIComponent(profileId)}`, {
    kb_dir: kbDir,
    ...payload,
  });
  return normalizeModelPoolData(response.data?.model_pool ?? response.data);
};

export const deleteModelPoolProfile = async (kbDir: string, profileId: string): Promise<ModelPoolData> => {
  const response = await apiClient.delete(`/model-pool/profiles/${encodeURIComponent(profileId)}`, {
    data: { kb_dir: kbDir },
  });
  return normalizeModelPoolData(response.data?.model_pool ?? response.data);
};

export const probeModelPoolProfile = async (kbDir: string, profileId: string): Promise<ModelPoolData> => {
  const response = await apiClient.post(`/model-pool/profiles/${encodeURIComponent(profileId)}/probe`, {
    kb_dir: kbDir,
  });
  return normalizeModelPoolData(response.data?.model_pool ?? response.data);
};

export const setModelPoolProfileEnabled = async (
  kbDir: string,
  profileId: string,
  enabled: boolean,
): Promise<ModelPoolData> => {
  const response = await apiClient.post(
    `/model-pool/profiles/${encodeURIComponent(profileId)}/${enabled ? 'enable' : 'disable'}`,
    { kb_dir: kbDir },
  );
  return normalizeModelPoolData(response.data?.model_pool ?? response.data);
};

export async function streamQuery(
  payload: StreamQueryPayload,
  callbacks: StreamQueryCallbacks = {},
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch('/api/query/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: JSON.stringify(payload),
    signal,
  });

  if (!response.ok || !response.body) {
    throw new Error(`Query stream failed with status ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split('\n\n');
    buffer = events.pop() ?? '';

    for (const rawEvent of events) {
      const parsed = parseSseEvent(rawEvent);
      if (!parsed) continue;

      if (parsed.event === 'session') {
        callbacks.onSession?.(String(parsed.data?.session_id ?? ''));
        continue;
      }

      if (parsed.event === 'delta') {
        callbacks.onDelta?.(String(parsed.data?.text ?? ''));
        continue;
      }

      if (parsed.event === 'done') {
        callbacks.onDone?.({
          answer: String(parsed.data?.answer ?? ''),
          session_id: String(parsed.data?.session_id ?? ''),
          session: isRecord(parsed.data?.session) ? normalizeChatSessionDetail(parsed.data?.session) : undefined,
          references: asChatReferences(parsed.data?.references),
        });
        return;
      }

      if (parsed.event === 'error') {
        throw new Error(String(parsed.data?.message ?? 'Query stream failed'));
      }
    }
  }
}

function normalizeDocumentListResponse(raw: unknown): DocumentListResponse {
  const payload = (raw ?? {}) as Record<string, unknown>;
  const documents = Array.isArray(payload.documents)
    ? payload.documents.map((item) => normalizeDocument(item as Record<string, unknown>))
    : [];

  return {
    documents,
    summaries: asStringArray(payload.summaries),
    concepts: asStringArray(payload.concepts),
    reports: asStringArray(payload.reports),
  };
}

function normalizeDocument(raw: Record<string, unknown>): DocumentItem {
  const workflow_state = {
    ...DEFAULT_WORKFLOW_STATE,
    ...(isRecord(raw.workflow_state) ? raw.workflow_state : {}),
  };
  const review = {
    ...DEFAULT_REVIEW_STATE,
    ...(isRecord(raw.review) ? raw.review : {}),
  };
  const execution = {
    ...DEFAULT_EXECUTION_STATE,
    ...(isRecord(raw.execution) ? raw.execution : {}),
  };
  const related_pages = {
    summaries: asRelatedPageEntries(isRecord(raw.related_pages) ? raw.related_pages.summaries : []),
    companies: asRelatedPageEntries(isRecord(raw.related_pages) ? raw.related_pages.companies : []),
    industries: asRelatedPageEntries(isRecord(raw.related_pages) ? raw.related_pages.industries : []),
    concepts: asRelatedPageEntries(isRecord(raw.related_pages) ? raw.related_pages.concepts : []),
  };

  return {
    hash: String(raw.hash ?? ''),
    name: String(raw.name ?? ''),
    type: String(raw.type ?? 'unknown'),
    pages: typeof raw.pages === 'number' ? raw.pages : String(raw.pages ?? ''),
    stem: String(raw.stem ?? ''),
    raw_path: String(raw.raw_path ?? ''),
    raw_exists: Boolean(raw.raw_exists),
    source_path: nullableString(raw.source_path),
    source_summary: nullableString(raw.source_summary),
    summary_exists: Boolean(raw.summary_exists),
    ingested_at: nullableString(raw.ingested_at),
    ingested_date: nullableString(raw.ingested_date),
    related_count: Number(raw.related_count ?? 0),
    related_pages: {
      ...DEFAULT_RELATED_PAGES,
      ...related_pages,
    },
    source_kind: String(raw.source_kind ?? ''),
    scan_detected: Boolean(raw.scan_detected),
    workflow_state,
    review,
    execution,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function nullableString(value: unknown): string | null {
  if (value === null || value === undefined || value === '') return null;
  return String(value);
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)) : [];
}

function asRelatedPageEntries(value: unknown): RelatedPageEntry[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item) => isRecord(item))
    .map((item) => ({
      path: String(item.path ?? ''),
      page: String(item.page ?? ''),
      title: String(item.title ?? ''),
      shared: Boolean(item.shared),
    }));
}

function normalizeChatSessionSummary(raw: unknown): ChatSessionSummary {
  const item = isRecord(raw) ? raw : {};
  return {
    id: String(item.id ?? ''),
    title: String(item.title ?? ''),
    turn_count: Number(item.turn_count ?? 0),
    updated_at: String(item.updated_at ?? ''),
    model: String(item.model ?? ''),
  };
}

function normalizeChatSessionDetail(raw: unknown): ChatSessionDetail {
  const item = isRecord(raw) ? raw : {};
  const summary = normalizeChatSessionSummary(item);
  return {
    ...summary,
    created_at: String(item.created_at ?? ''),
    language: String(item.language ?? ''),
    history: Array.isArray(item.history) ? item.history : [],
    user_turns: asStringArray(item.user_turns),
    assistant_texts: asStringArray(item.assistant_texts),
  };
}

function normalizeConfigProfile(raw: unknown): ConfigProfile {
  const item = isRecord(raw) ? raw : {};
  return {
    id: String(item.id ?? ''),
    name: String(item.name ?? ''),
    model: String(item.model ?? ''),
    wire_api: String(item.wire_api ?? 'chat_completions'),
    base_url: String(item.base_url ?? ''),
    provider: String(item.provider ?? 'generic'),
    reasoning_effort: String(item.reasoning_effort ?? ''),
    thinking_enabled: Boolean(item.thinking_enabled),
    enabled: item.enabled !== false,
    tags: asStringArray(item.tags),
    features: asStringArray(item.features),
    probe_models: asStringArray(item.probe_models),
    models: Array.isArray(item.models)
      ? item.models
          .filter((model) => isRecord(model))
          .map((model) => ({
            name: String(model.name ?? ''),
            weight: Number(model.weight ?? 100),
          }))
      : [],
    priority: Number(item.priority ?? 50),
    api_key: String(item.api_key ?? ''),
    api_key_configured: Boolean(item.api_key_configured),
    is_active: Boolean(item.is_active),
  };
}

function normalizeConfigData(raw: unknown): ConfigData {
  const item = isRecord(raw) ? raw : {};
  return {
    model: String(item.model ?? ''),
    language: String(item.language ?? 'en'),
    pageindex_threshold: Number(item.pageindex_threshold ?? 20),
    compile_max_concurrency: Number(item.compile_max_concurrency ?? 2),
    ingest_gate_enabled: Boolean(item.ingest_gate_enabled),
    ingest_gate_pass_threshold: Number(item.ingest_gate_pass_threshold ?? 75),
    ingest_gate_hold_threshold: Number(item.ingest_gate_hold_threshold ?? 60),
    ingest_gate_hard_reject_enabled: item.ingest_gate_hard_reject_enabled !== false,
    ingest_gate_log_all_decisions: item.ingest_gate_log_all_decisions !== false,
    ingest_gate_allow_force_pass: item.ingest_gate_allow_force_pass !== false,
    ingest_gate_allow_force_reject: item.ingest_gate_allow_force_reject !== false,
    ocr_enabled: item.ocr_enabled !== false,
    ocr_detection_mode: String(item.ocr_detection_mode ?? 'auto_recommend'),
    ocr_default_model: String(item.ocr_default_model ?? 'PaddleOCR-VL-1.5'),
    ocr_chunk_pages: Number(item.ocr_chunk_pages ?? 100),
    ocr_auto_recommend: item.ocr_auto_recommend !== false,
    paddleocr_token: String(item.paddleocr_token ?? ''),
    pageindex_local_enabled: Boolean(item.pageindex_local_enabled),
    pageindex_local_model: String(item.pageindex_local_model ?? ''),
    pageindex_local_installation_state: String(item.pageindex_local_installation_state ?? 'not_installed'),
    pageindex_local_repo_dir: String(item.pageindex_local_repo_dir ?? ''),
    pageindex_local_python_path: String(item.pageindex_local_python_path ?? ''),
    pageindex_local_script_path: String(item.pageindex_local_script_path ?? ''),
    pageindex_local_version: String(item.pageindex_local_version ?? ''),
    wire_api: String(item.wire_api ?? 'chat_completions'),
    base_url: String(item.base_url ?? ''),
    api_key: String(item.api_key ?? ''),
    api_key_configured: Boolean(item.api_key_configured),
    active_profile: String(item.active_profile ?? ''),
    profiles: Array.isArray(item.profiles) ? item.profiles.map((profile) => normalizeConfigProfile(profile)) : [],
  };
}

function normalizeModelPoolData(raw: unknown): ModelPoolData {
  const item = isRecord(raw) ? raw : {};
  return {
    enabled: Boolean(item.enabled),
    strategy: String(item.strategy ?? 'weighted_round_robin'),
    probe_interval_seconds: Number(item.probe_interval_seconds ?? 600),
    failure_threshold: Number(item.failure_threshold ?? 3),
    timeout_seconds: Number(item.timeout_seconds ?? 12),
    active_profile: String(item.active_profile ?? ''),
    summary: isRecord(item.summary)
      ? Object.fromEntries(Object.entries(item.summary).map(([key, value]) => [key, Number(value ?? 0)]))
      : {},
    profiles: Array.isArray(item.profiles)
      ? item.profiles.map((profile) => {
          const normalized = normalizeConfigProfile(profile) as ModelPoolProfile;
          const profileItem = isRecord(profile) ? profile : {};
          return {
            ...normalized,
            health: String(profileItem.health ?? 'unknown'),
            last_checked_at: String(profileItem.last_checked_at ?? ''),
            latency_ms:
              profileItem.latency_ms === null || profileItem.latency_ms === undefined
                ? null
                : Number(profileItem.latency_ms),
            consecutive_failures: Number(profileItem.consecutive_failures ?? 0),
            available_models: asStringArray(profileItem.available_models),
            failed_models: isRecord(profileItem.failed_models)
              ? Object.fromEntries(Object.entries(profileItem.failed_models).map(([key, value]) => [key, String(value ?? '')]))
              : {},
            last_error: String(profileItem.last_error ?? ''),
            routes: Array.isArray(profileItem.routes)
              ? profileItem.routes
                  .filter((route) => isRecord(route))
                  .map((route) => ({
                    id: String(route.id ?? ''),
                    profile_id: String(route.profile_id ?? ''),
                    model: String(route.model ?? ''),
                    weight: Number(route.weight ?? 100),
                    health: String(route.health ?? 'unknown'),
                    latency_ms:
                      route.latency_ms === null || route.latency_ms === undefined ? null : Number(route.latency_ms),
                    base_url: String(route.base_url ?? ''),
                    wire_api: String(route.wire_api ?? ''),
                    last_error: String(route.last_error ?? ''),
                  }))
              : [],
          };
        })
      : [],
  };
}

function normalizePageIndexLocalStatus(raw: unknown): PageIndexLocalStatus {
  const item = isRecord(raw) ? raw : {};
  return {
    enabled: Boolean(item.enabled),
    ready: Boolean(item.ready),
    installation_state: String(item.installation_state ?? 'not_installed'),
    root: String(item.root ?? ''),
    manifest: isRecord(item.manifest) ? item.manifest : {},
  };
}

function asChatReferences(value: unknown): ChatReference[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item) => isRecord(item))
    .map((item) => ({
      type: nullableString(item.type) ?? undefined,
      path: nullableString(item.path) ?? undefined,
      pages: nullableString(item.pages) ?? undefined,
      doc_name: nullableString(item.doc_name) ?? undefined,
      query: nullableString(item.query) ?? undefined,
      top_k: item.top_k === undefined || item.top_k === null ? undefined : Number(item.top_k),
    }));
}

function parseSseEvent(rawEvent: string): { event: string; data: Record<string, unknown> | null } | null {
  const lines = rawEvent.split('\n');
  let event = 'message';
  const dataLines: string[] = [];

  for (const line of lines) {
    if (line.startsWith('event:')) {
      event = line.slice('event:'.length).trim();
      continue;
    }
    if (line.startsWith('data:')) {
      dataLines.push(line.slice('data:'.length).trim());
    }
  }

  let data: Record<string, unknown> | null = null;
  if (dataLines.length) {
    try {
      data = JSON.parse(dataLines.join('\n')) as Record<string, unknown>;
    } catch {
      data = null;
    }
  }

  return { event, data };
}
