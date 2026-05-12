'use client';

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import axios from 'axios';
import {
  AlertCircle,
  CheckCircle2,
  FileSearch,
  Loader2,
  RefreshCcw,
  RotateCcw,
  Search,
  Trash2,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';

interface OcrCacheEntry {
  file_hash: string;
  status: string;
  doc_name: string;
  page_count: number;
  ocr_model: string;
  has_pages: boolean;
  has_pageindex_input: boolean;
  invalidated_at?: string;
}

interface OcrCacheResponse {
  entries: OcrCacheEntry[];
}

interface JobPayload {
  id: string;
  type?: string;
  status?: string;
  message?: string;
}

interface OcrActionResponse {
  entry?: OcrCacheEntry;
  job?: JobPayload;
}

type OcrAction = 'invalidate' | 'rerun' | 'retry';

const STATUS_TONE: Record<string, string> = {
  ready: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  completed: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  success: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  failed: 'border-red-200 bg-red-50 text-red-700',
  error: 'border-red-200 bg-red-50 text-red-700',
  invalid_manifest: 'border-red-200 bg-red-50 text-red-700',
  invalidated: 'border-amber-200 bg-amber-50 text-amber-700',
  unknown: 'border-slate-200 bg-slate-50 text-slate-600',
};

export function OcrTab({ kbDir }: { kbDir: string | null }) {
  const queryClient = useQueryClient();
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  const { data, error, isFetching, isLoading, refetch } = useQuery({
    queryKey: ['ocrCache', kbDir],
    queryFn: () => getOcrCache(kbDir ?? ''),
    enabled: !!kbDir,
  });

  const entries = useMemo(() => data?.entries ?? [], [data?.entries]);
  const statuses = useMemo(() => {
    const unique = new Set(entries.map((entry) => normalizeStatus(entry.status)).filter(Boolean));
    return ['all', ...Array.from(unique).sort()];
  }, [entries]);
  const filteredEntries = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return entries.filter((entry) => {
      const status = normalizeStatus(entry.status);
      const matchesStatus = statusFilter === 'all' || status === statusFilter;
      if (!matchesStatus) return false;
      if (!query) return true;
      return [entry.doc_name, entry.file_hash, entry.ocr_model, entry.status]
        .filter(Boolean)
        .some((value) => value.toLowerCase().includes(query));
    });
  }, [entries, searchQuery, statusFilter]);

  const counts = useMemo(
    () => ({
      total: entries.length,
      visible: filteredEntries.length,
      withPages: entries.filter((entry) => entry.has_pages).length,
      withPageIndexInput: entries.filter((entry) => entry.has_pageindex_input).length,
      invalidated: entries.filter((entry) => normalizeStatus(entry.status) === 'invalidated').length,
    }),
    [entries, filteredEntries.length],
  );

  const runActionMutation = useMutation({
    mutationFn: ({ fileHash, action }: { fileHash: string; action: OcrAction }) => {
      if (!kbDir) throw new Error('No knowledge base selected.');
      return runOcrAction(kbDir, fileHash, action);
    },
    onSuccess: (data, variables) => {
      setActionMessage(formatActionMessage(variables.action, data));
      queryClient.invalidateQueries({ queryKey: ['ocrCache', kbDir] });
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
      queryClient.invalidateQueries({ queryKey: ['documents', kbDir] });
    },
    onError: (error) => {
      setActionMessage(getErrorMessage(error));
    },
  });

  const busyHash = runActionMutation.variables?.fileHash ?? null;
  const busyAction = runActionMutation.variables?.action ?? null;

  if (!kbDir) {
    return (
      <Card className="flex h-full min-h-0 flex-col overflow-hidden rounded-none border-x-0 border-b-0 border-t-0 sm:rounded-lg sm:border-x">
        <CardHeader>
          <CardTitle>OCR Cache</CardTitle>
          <CardDescription>Select a knowledge base to inspect cached OCR artifacts.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
          No knowledge base selected.
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="flex h-full min-h-0 flex-col overflow-hidden rounded-none border-x-0 border-b-0 border-t-0 bg-[linear-gradient(180deg,rgba(255,255,255,0.95),rgba(249,247,242,0.95))] sm:rounded-lg sm:border-x">
      <CardHeader className="border-b bg-[linear-gradient(135deg,rgba(27,52,42,0.06),rgba(186,151,91,0.12))]">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
          <div>
            <CardTitle className="flex items-center gap-2 text-xl">
              <FileSearch className="h-5 w-5" />
              OCR Cache
            </CardTitle>
            <CardDescription>
              Inspect cached OCR artifacts and rerun OCR work for individual source documents.
            </CardDescription>
          </div>
          <div className="flex flex-col gap-3 xl:items-end">
            <div className="flex flex-wrap items-center gap-2">
              <div className="relative">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder="Search document, hash, model"
                  className="w-full bg-white/85 pl-9 xl:w-72"
                />
              </div>
              <select
                value={statusFilter}
                onChange={(event) => setStatusFilter(event.target.value)}
                className="h-9 rounded-md border border-border bg-white/85 px-3 text-sm"
              >
                {statuses.map((status) => (
                  <option key={status} value={status}>
                    {status === 'all' ? 'All statuses' : formatStatus(status)}
                  </option>
                ))}
              </select>
              <Button
                type="button"
                variant="ghost"
                onClick={() => void refetch()}
                disabled={isFetching || runActionMutation.isPending}
              >
                {isFetching ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCcw className="mr-2 h-4 w-4" />}
                Refresh
              </Button>
            </div>
            <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
              <span>{counts.visible} visible</span>
              <span>{counts.total} cached</span>
              <span>{counts.withPages} with pages</span>
              <span>{counts.withPageIndexInput} with PageIndex input</span>
              <span>{counts.invalidated} invalidated</span>
            </div>
          </div>
        </div>
      </CardHeader>

      {actionMessage ? (
        <div className="border-b bg-[rgba(27,52,42,0.04)] px-6 py-3 text-sm text-muted-foreground">
          {actionMessage}
        </div>
      ) : null}

      {error ? (
        <div className="border-b bg-red-50 px-6 py-3 text-sm text-red-700">
          <AlertCircle className="mr-2 inline h-4 w-4" />
          {getErrorMessage(error)}
        </div>
      ) : null}

      <CardContent className="min-h-0 flex-1 overflow-hidden p-0">
        {isLoading ? (
          <div className="flex h-full items-center justify-center text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            Loading OCR cache...
          </div>
        ) : filteredEntries.length === 0 ? (
          <div className="flex h-full items-center justify-center p-6">
            <div className="rounded-lg border border-dashed bg-white/70 px-8 py-10 text-center text-sm text-muted-foreground">
              {entries.length === 0 ? 'No OCR cache entries found.' : 'No OCR cache entries match the current filters.'}
            </div>
          </div>
        ) : (
          <div className="h-full overflow-auto">
            <Table>
              <TableHeader className="sticky top-0 z-10 bg-[oklch(0.98_0.01_95)]">
                <TableRow>
                  <TableHead>Document</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Pages</TableHead>
                  <TableHead>Model</TableHead>
                  <TableHead>Artifacts</TableHead>
                  <TableHead>Invalidated</TableHead>
                  <TableHead className="w-[250px] text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredEntries.map((entry) => (
                  <TableRow key={entry.file_hash}>
                    <TableCell className="min-w-[260px]">
                      <div className="max-w-[360px]">
                        <div className="truncate font-medium" title={entry.doc_name || entry.file_hash}>
                          {entry.doc_name || '(unnamed source)'}
                        </div>
                        <div className="mt-1 truncate font-mono text-xs text-muted-foreground" title={entry.file_hash}>
                          {entry.file_hash}
                        </div>
                      </div>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={entry.status} />
                    </TableCell>
                    <TableCell>{entry.page_count || '-'}</TableCell>
                    <TableCell className="max-w-[180px] truncate" title={entry.ocr_model || ''}>
                      {entry.ocr_model || '-'}
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1.5">
                        <ArtifactBadge enabled={entry.has_pages} label="pages" />
                        <ArtifactBadge enabled={entry.has_pageindex_input} label="pageindex" />
                      </div>
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                      {entry.invalidated_at ? formatDate(entry.invalidated_at) : '-'}
                    </TableCell>
                    <TableCell>
                      <div className="flex justify-end gap-2">
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={runActionMutation.isPending}
                          onClick={() => runActionMutation.mutate({ fileHash: entry.file_hash, action: 'invalidate' })}
                        >
                          {busyHash === entry.file_hash && busyAction === 'invalidate' ? (
                            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                          )}
                          Invalidate
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant="secondary"
                          disabled={runActionMutation.isPending}
                          onClick={() => runActionMutation.mutate({ fileHash: entry.file_hash, action: 'rerun' })}
                        >
                          {busyHash === entry.file_hash && busyAction === 'rerun' ? (
                            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <RefreshCcw className="mr-1.5 h-3.5 w-3.5" />
                          )}
                          Rerun
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          disabled={runActionMutation.isPending}
                          onClick={() => runActionMutation.mutate({ fileHash: entry.file_hash, action: 'retry' })}
                        >
                          {busyHash === entry.file_hash && busyAction === 'retry' ? (
                            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
                          )}
                          Retry
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

async function getOcrCache(kbDir: string): Promise<OcrCacheResponse> {
  const response = await axios.get('/api/ocr/cache', { params: { kb_dir: kbDir } });
  const entries = Array.isArray(response.data?.entries) ? response.data.entries.map(normalizeOcrCacheEntry) : [];
  return { entries };
}

async function runOcrAction(kbDir: string, fileHash: string, action: OcrAction): Promise<OcrActionResponse> {
  const response = await axios.post(`/api/ocr/cache/${encodeURIComponent(fileHash)}/${action}`, { kb_dir: kbDir });
  return response.data ?? {};
}

function normalizeOcrCacheEntry(value: unknown): OcrCacheEntry {
  const item = isRecord(value) ? value : {};
  return {
    file_hash: String(item.file_hash ?? ''),
    status: String(item.status ?? 'unknown'),
    doc_name: String(item.doc_name ?? ''),
    page_count: Number(item.page_count ?? 0),
    ocr_model: String(item.ocr_model ?? ''),
    has_pages: Boolean(item.has_pages),
    has_pageindex_input: Boolean(item.has_pageindex_input),
    invalidated_at: item.invalidated_at ? String(item.invalidated_at) : undefined,
  };
}

function StatusBadge({ status }: { status: string }) {
  const normalized = normalizeStatus(status);
  const className = STATUS_TONE[normalized] ?? 'border-slate-200 bg-slate-50 text-slate-600';
  return (
    <span className={`inline-flex rounded-full border px-2 py-0.5 text-xs font-medium ${className}`}>
      {formatStatus(normalized)}
    </span>
  );
}

function ArtifactBadge({ enabled, label }: { enabled: boolean; label: string }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs ${
        enabled ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-slate-200 bg-slate-50 text-slate-500'
      }`}
    >
      {enabled ? <CheckCircle2 className="mr-1 h-3 w-3" /> : null}
      {label}
    </span>
  );
}

function formatActionMessage(action: OcrAction, data: OcrActionResponse): string {
  if (data.job?.id) {
    return `${formatAction(action)} started job ${data.job.id}${data.job.status ? ` (${data.job.status})` : ''}.`;
  }
  if (data.entry) {
    return `${formatAction(action)} updated ${data.entry.doc_name || data.entry.file_hash}.`;
  }
  return `${formatAction(action)} completed.`;
}

function formatAction(action: OcrAction): string {
  if (action === 'rerun') return 'Rerun';
  if (action === 'retry') return 'Retry';
  return 'Invalidate';
}

function normalizeStatus(status: string): string {
  return (status || 'unknown').trim().toLowerCase();
}

function formatStatus(status: string): string {
  return status
    .split('_')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function getErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (isRecord(detail) && typeof detail.message === 'string') return detail.message;
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return 'Unexpected OCR cache error.';
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}
