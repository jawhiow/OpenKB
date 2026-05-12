'use client';

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertCircle,
  CheckCircle2,
  Eye,
  FileUp,
  FolderPlus,
  Loader2,
  RefreshCcw,
  ShieldCheck,
  Sparkles,
  Trash2,
} from 'lucide-react';
import {
  deleteDocument,
  DocumentItem,
  DocumentQueryParams,
  getDocuments,
  importDocuments,
  promoteDocuments,
  reviewSummaries,
  summarizeDocuments,
  uploadDocuments,
} from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

type StageView = 'inventory' | 'review' | 'promotion';

type SelectionState = Record<string, boolean>;

const INVENTORY_FILTERS: Array<{ label: string; value: string }> = [
  { label: 'All inventory', value: '' },
  { label: 'Source ready', value: 'ready' },
  { label: 'Source failed', value: 'failed' },
  { label: 'Needs summary', value: 'not_started,failed' },
];

const REVIEW_FILTERS: Array<{ label: string; value: string }> = [
  { label: 'Needs review', value: 'unreviewed,held,scored' },
  { label: 'Approved', value: 'approved' },
  { label: 'Rejected', value: 'rejected' },
];

const PROMOTION_REVIEW_FILTERS: Array<{ label: string; value: string }> = [
  { label: 'Approved only', value: 'approved' },
];

const PROMOTION_STATE_FILTERS: Array<{ label: string; value: string }> = [
  { label: 'Not promoted / failed', value: 'not_selected,failed' },
  { label: 'Already promoted', value: 'promoted' },
  { label: 'Promotion failed', value: 'failed' },
];

export function DocumentsTab({
  kbDir,
  onJobStarted,
}: {
  kbDir: string;
  onJobStarted: (jobId: string) => void;
}) {
  const queryClient = useQueryClient();
  const [stageView, setStageView] = useState<StageView>('inventory');
  const [searchQuery, setSearchQuery] = useState('');
  const [inventorySourceState, setInventorySourceState] = useState('');
  const [reviewStateFilter, setReviewStateFilter] = useState('unreviewed,held,scored');
  const [promotionReviewFilter, setPromotionReviewFilter] = useState('approved');
  const [promotionStateFilter, setPromotionStateFilter] = useState('not_selected,failed');
  const [localPath, setLocalPath] = useState('');
  const [selection, setSelection] = useState<SelectionState>({});
  const [approvedBy, setApprovedBy] = useState('');
  const [reviewNotes, setReviewNotes] = useState('');
  const [summaryScore, setSummaryScore] = useState('');
  const [detailDocument, setDetailDocument] = useState<DocumentItem | null>(null);

  const queryParams = useMemo<DocumentQueryParams>(() => {
    if (stageView === 'inventory') {
      return {
        q: searchQuery,
        source_state: inventorySourceState,
      };
    }
    if (stageView === 'review') {
      return {
        q: searchQuery,
        summary_state: 'ready',
        review_state: reviewStateFilter,
      };
    }
    return {
      q: searchQuery,
      review_state: promotionReviewFilter,
      promotion_state: promotionStateFilter,
    };
  }, [inventorySourceState, promotionReviewFilter, promotionStateFilter, reviewStateFilter, searchQuery, stageView]);

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['documents', kbDir, stageView, queryParams],
    queryFn: () => getDocuments(kbDir, queryParams),
    enabled: !!kbDir,
  });

  const documents = useMemo(() => data?.documents ?? [], [data?.documents]);
  const visibleHashes = useMemo(() => new Set(documents.map((document) => document.hash)), [documents]);
  const visibleSelection = useMemo(
    () =>
      Object.fromEntries(
        Object.entries(selection).filter(([hash, checked]) => checked && visibleHashes.has(hash)),
      ) as SelectionState,
    [selection, visibleHashes],
  );
  const selectedHashes = documents.filter((document) => visibleSelection[document.hash]).map((document) => document.hash);
  const selectedDocuments = documents.filter((document) => visibleSelection[document.hash]);

  const invalidateDocumentQueries = () => {
    queryClient.invalidateQueries({ queryKey: ['documents', kbDir] });
    queryClient.invalidateQueries({ queryKey: ['kbStats', kbDir] });
    void refetch();
  };

  const handleJobStart = (jobId?: string | null) => {
    if (!jobId) return;
    onJobStarted(jobId);
    invalidateDocumentQueries();
  };

  const importMutation = useMutation({
    mutationFn: (path: string) => importDocuments(kbDir, path),
    onSuccess: (data) => {
      setLocalPath('');
      handleJobStart(data?.job?.id);
    },
  });

  const uploadMutation = useMutation({
    mutationFn: (files: File[]) => uploadDocuments(kbDir, files, { import_only: true }),
    onSuccess: (data) => {
      handleJobStart(data?.job?.id);
    },
  });

  const summarizeMutation = useMutation({
    mutationFn: (fileHashes: string[]) => summarizeDocuments(kbDir, fileHashes),
    onSuccess: (data) => {
      handleJobStart(data?.job?.id);
    },
  });

  const approveMutation = useMutation({
    mutationFn: (fileHashes: string[]) =>
      reviewSummaries(
        kbDir,
        fileHashes.map((fileHash) => ({
          file_hash: fileHash,
          review_state: 'approved',
          summary_score: summaryScore ? Number(summaryScore) : null,
          review_notes: reviewNotes,
          approved_by: approvedBy,
        })),
      ),
    onSuccess: (data) => {
      setReviewNotes('');
      setSummaryScore('');
      setSelection({});
      handleJobStart(data?.job?.id);
    },
  });

  const holdMutation = useMutation({
    mutationFn: (fileHashes: string[]) =>
      reviewSummaries(
        kbDir,
        fileHashes.map((fileHash) => ({
          file_hash: fileHash,
          review_state: 'held',
          summary_score: summaryScore ? Number(summaryScore) : null,
          review_notes: reviewNotes,
          approved_by: approvedBy,
        })),
      ),
    onSuccess: (data) => {
      setSelection({});
      handleJobStart(data?.job?.id);
    },
  });

  const rejectMutation = useMutation({
    mutationFn: (fileHashes: string[]) =>
      reviewSummaries(
        kbDir,
        fileHashes.map((fileHash) => ({
          file_hash: fileHash,
          review_state: 'rejected',
          summary_score: summaryScore ? Number(summaryScore) : null,
          review_notes: reviewNotes,
          approved_by: approvedBy,
        })),
      ),
    onSuccess: (data) => {
      setSelection({});
      handleJobStart(data?.job?.id);
    },
  });

  const promoteMutation = useMutation({
    mutationFn: (fileHashes: string[]) => promoteDocuments(kbDir, fileHashes),
    onSuccess: (data) => {
      setSelection({});
      handleJobStart(data?.job?.id);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (selector: string) => deleteDocument(kbDir, selector),
    onSuccess: (data) => {
      handleJobStart(data?.job?.id);
    },
  });

  const busy =
    importMutation.isPending ||
    uploadMutation.isPending ||
    summarizeMutation.isPending ||
    approveMutation.isPending ||
    holdMutation.isPending ||
    rejectMutation.isPending ||
    promoteMutation.isPending ||
    deleteMutation.isPending;

  const handleFileUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
    const fileList = event.target.files;
    if (!fileList?.length) return;
    uploadMutation.mutate(Array.from(fileList));
    event.target.value = '';
  };

  const toggleSelection = (hash: string) => {
    setSelection((current) => ({ ...current, [hash]: !current[hash] }));
  };

  const toggleAll = () => {
    const allSelected = documents.length > 0 && documents.every((document) => visibleSelection[document.hash]);
    setSelection(
      documents.reduce<SelectionState>((next, document) => {
        next[document.hash] = !allSelected;
        return next;
      }, {}),
    );
  };

  return (
    <Card className="h-full flex flex-col rounded-none border-t-0 border-b-0 border-x-0 sm:border-x sm:rounded-lg overflow-hidden min-h-0 bg-[linear-gradient(180deg,rgba(255,255,255,0.95),rgba(249,247,242,0.95))]">
      <CardHeader className="border-b bg-[linear-gradient(135deg,rgba(27,52,42,0.06),rgba(186,151,91,0.12))]">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
          <div>
            <CardTitle className="text-xl">Staged Document Workbench</CardTitle>
            <CardDescription>
              Import into inventory first, review summaries second, promote approved documents last.
            </CardDescription>
          </div>
          <div className="flex flex-col gap-3 xl:items-end">
            <div className="flex flex-wrap gap-2">
              <Input
                placeholder="Absolute local path"
                value={localPath}
                onChange={(event) => setLocalPath(event.target.value)}
                className="w-full min-w-[260px] bg-white/80 xl:w-80"
                disabled={busy}
              />
              <Button
                onClick={() => importMutation.mutate(localPath)}
                disabled={!localPath.trim() || busy}
                className="bg-[oklch(0.34_0.06_165)] text-white hover:bg-[oklch(0.29_0.06_165)]"
              >
                {importMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <FolderPlus className="mr-2 h-4 w-4" />}
                Import Path
              </Button>
              <div className="relative">
                <Input
                  type="file"
                  multiple
                  className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
                  onChange={handleFileUpload}
                  disabled={busy}
                />
                <Button variant="secondary" disabled={busy} className="bg-white/90">
                  {uploadMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <FileUp className="mr-2 h-4 w-4" />}
                  Import Files
                </Button>
              </div>
              <Button variant="ghost" onClick={() => void refetch()} disabled={busy}>
                <RefreshCcw className="mr-2 h-4 w-4" />
                Refresh
              </Button>
            </div>
            <div className="flex items-center gap-3 text-xs text-muted-foreground">
              <span>{documents.length} visible</span>
              <span>{selectedHashes.length} selected</span>
            </div>
          </div>
        </div>
      </CardHeader>

      <CardContent className="flex-1 overflow-hidden p-0">
        <Tabs
          value={stageView}
          onValueChange={(value) => setStageView(value as StageView)}
          className="flex h-full flex-col"
        >
          <div className="border-b px-6 pt-4">
            <TabsList className="grid w-full max-w-2xl grid-cols-3 bg-[oklch(0.96_0.01_95)]">
              <TabsTrigger value="inventory">Inventory</TabsTrigger>
              <TabsTrigger value="review">Summary Review</TabsTrigger>
              <TabsTrigger value="promotion">Promotion</TabsTrigger>
            </TabsList>
          </div>

          <TabsContent value="inventory" className="m-0 flex min-h-0 flex-1 flex-col">
            <StageToolbar
              searchQuery={searchQuery}
              onSearchChange={setSearchQuery}
              filterLabel="Source state"
              filterValue={inventorySourceState}
              onFilterChange={setInventorySourceState}
              filters={INVENTORY_FILTERS}
              rightSlot={
                <Button
                  onClick={() => summarizeMutation.mutate(selectedHashes)}
                  disabled={!selectedHashes.length || summarizeMutation.isPending}
                  className="bg-[oklch(0.55_0.11_70)] text-white hover:bg-[oklch(0.5_0.11_70)]"
                >
                  {summarizeMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
                  Summarize Selected
                </Button>
              }
            />
            <DocumentStageTable
              documents={documents}
              isLoading={isLoading}
              selection={visibleSelection}
              onToggleSelection={toggleSelection}
              onToggleAll={toggleAll}
              onDelete={(selector) => deleteMutation.mutate(selector)}
              onSummarize={(hash) => summarizeMutation.mutate([hash])}
              onApprove={(hash) =>
                approveMutation.mutate([hash])
              }
              onPromote={(hash) => promoteMutation.mutate([hash])}
              onViewDetail={setDetailDocument}
              showDelete
            />
          </TabsContent>

          <TabsContent value="review" className="m-0 flex min-h-0 flex-1 flex-col">
            <StageToolbar
              searchQuery={searchQuery}
              onSearchChange={setSearchQuery}
              filterLabel="Review state"
              filterValue={reviewStateFilter}
              onFilterChange={setReviewStateFilter}
              filters={REVIEW_FILTERS}
              rightSlot={
                <div className="flex flex-wrap items-center gap-2">
                  <Input
                    placeholder="Reviewer"
                    value={approvedBy}
                    onChange={(event) => setApprovedBy(event.target.value)}
                    className="w-36 bg-white"
                  />
                  <Input
                    placeholder="Score"
                    value={summaryScore}
                    onChange={(event) => setSummaryScore(event.target.value)}
                    className="w-24 bg-white"
                  />
                  <Input
                    placeholder="Review notes"
                    value={reviewNotes}
                    onChange={(event) => setReviewNotes(event.target.value)}
                    className="w-48 bg-white"
                  />
                  <Button
                    onClick={() => approveMutation.mutate(selectedHashes)}
                    disabled={!selectedHashes.length || approveMutation.isPending}
                    className="bg-[oklch(0.43_0.11_155)] text-white hover:bg-[oklch(0.38_0.11_155)]"
                  >
                    <ShieldCheck className="mr-2 h-4 w-4" />
                    Approve
                  </Button>
                  <Button
                    variant="secondary"
                    onClick={() => holdMutation.mutate(selectedHashes)}
                    disabled={!selectedHashes.length || holdMutation.isPending}
                  >
                    Hold
                  </Button>
                  <Button
                    variant="destructive"
                    onClick={() => rejectMutation.mutate(selectedHashes)}
                    disabled={!selectedHashes.length || rejectMutation.isPending}
                  >
                    Reject
                  </Button>
                </div>
              }
            />
            <DocumentStageTable
              documents={documents}
              isLoading={isLoading}
              selection={visibleSelection}
              onToggleSelection={toggleSelection}
              onToggleAll={toggleAll}
              onApprove={(hash) => approveMutation.mutate([hash])}
              onHold={(hash) => holdMutation.mutate([hash])}
              onReject={(hash) => rejectMutation.mutate([hash])}
              onViewDetail={setDetailDocument}
            />
          </TabsContent>

          <TabsContent value="promotion" className="m-0 flex min-h-0 flex-1 flex-col">
            <div className="border-b bg-[rgba(186,151,91,0.08)] px-6 py-4">
              <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
                <div className="flex flex-1 flex-wrap items-center gap-2">
                  <Input
                    placeholder="Search approved documents"
                    value={searchQuery}
                    onChange={(event) => setSearchQuery(event.target.value)}
                    className="w-full bg-white xl:w-72"
                  />
                  <select
                    value={promotionReviewFilter}
                    onChange={(event) => setPromotionReviewFilter(event.target.value)}
                    className="h-9 rounded-md border border-border bg-white px-3 text-sm"
                  >
                    {PROMOTION_REVIEW_FILTERS.map((filter) => (
                      <option key={filter.label} value={filter.value}>
                        {filter.label}
                      </option>
                    ))}
                  </select>
                  <select
                    value={promotionStateFilter}
                    onChange={(event) => setPromotionStateFilter(event.target.value)}
                    className="h-9 rounded-md border border-border bg-white px-3 text-sm"
                  >
                    {PROMOTION_STATE_FILTERS.map((filter) => (
                      <option key={filter.label} value={filter.value}>
                        {filter.label}
                      </option>
                    ))}
                  </select>
                </div>
                <Button
                  onClick={() => promoteMutation.mutate(selectedHashes)}
                  disabled={!selectedHashes.length || promoteMutation.isPending}
                  className="bg-[oklch(0.34_0.06_165)] text-white hover:bg-[oklch(0.29_0.06_165)]"
                >
                  {promoteMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <CheckCircle2 className="mr-2 h-4 w-4" />}
                  Promote Approved
                </Button>
              </div>
            </div>
            <DocumentStageTable
              documents={documents}
              isLoading={isLoading}
              selection={visibleSelection}
              onToggleSelection={toggleSelection}
              onToggleAll={toggleAll}
              onPromote={(hash) => promoteMutation.mutate([hash])}
              onViewDetail={setDetailDocument}
            />
          </TabsContent>
        </Tabs>
      </CardContent>

      {stageView === 'review' && selectedDocuments.length > 0 ? (
        <div className="border-t bg-[rgba(27,52,42,0.04)] px-6 py-3 text-xs text-muted-foreground">
          Selected review set: {selectedDocuments.map((document) => document.stem).join(', ')}
        </div>
      ) : null}

      <DocumentDetailDialog document={detailDocument} onOpenChange={(open) => !open && setDetailDocument(null)} />
    </Card>
  );
}

function StageToolbar({
  searchQuery,
  onSearchChange,
  filterLabel,
  filterValue,
  onFilterChange,
  filters,
  rightSlot,
}: {
  searchQuery: string;
  onSearchChange: (value: string) => void;
  filterLabel: string;
  filterValue: string;
  onFilterChange: (value: string) => void;
  filters: Array<{ label: string; value: string }>;
  rightSlot?: React.ReactNode;
}) {
  return (
    <div className="border-b bg-[rgba(27,52,42,0.03)] px-6 py-4">
      <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
        <div className="flex flex-1 flex-wrap items-center gap-2">
          <Input
            placeholder="Search documents, paths, or source kind"
            value={searchQuery}
            onChange={(event) => onSearchChange(event.target.value)}
            className="w-full bg-white xl:w-80"
          />
          <label className="text-xs font-medium uppercase tracking-[0.16em] text-muted-foreground">
            {filterLabel}
          </label>
          <select
            value={filterValue}
            onChange={(event) => onFilterChange(event.target.value)}
            className="h-9 rounded-md border border-border bg-white px-3 text-sm"
          >
            {filters.map((filter) => (
              <option key={filter.label} value={filter.value}>
                {filter.label}
              </option>
            ))}
          </select>
        </div>
        {rightSlot}
      </div>
    </div>
  );
}

function DocumentStageTable({
  documents,
  isLoading,
  selection,
  onToggleSelection,
  onToggleAll,
  onDelete,
  onSummarize,
  onApprove,
  onHold,
  onReject,
  onPromote,
  onViewDetail,
  showDelete = false,
}: {
  documents: DocumentItem[];
  isLoading: boolean;
  selection: SelectionState;
  onToggleSelection: (hash: string) => void;
  onToggleAll: () => void;
  onDelete?: (selector: string) => void;
  onSummarize?: (hash: string) => void;
  onApprove?: (hash: string) => void;
  onHold?: (hash: string) => void;
  onReject?: (hash: string) => void;
  onPromote?: (hash: string) => void;
  onViewDetail?: (document: DocumentItem) => void;
  showDelete?: boolean;
}) {
  const allSelected = documents.length > 0 && documents.every((document) => selection[document.hash]);

  return (
    <div className="flex-1 overflow-auto px-6 py-4">
      {isLoading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : documents.length === 0 ? (
        <div className="flex h-full flex-col items-center justify-center rounded-2xl border border-dashed border-border bg-[rgba(255,255,255,0.65)] px-6 py-12 text-center">
          <AlertCircle className="mb-3 h-8 w-8 text-muted-foreground" />
          <p className="font-medium">No matching documents</p>
          <p className="mt-1 max-w-md text-sm text-muted-foreground">
            Adjust the current filters or import more files into inventory before continuing.
          </p>
        </div>
      ) : (
        <div className="overflow-hidden rounded-2xl border bg-white/85 shadow-sm">
          <Table>
            <TableHeader>
              <TableRow className="bg-[rgba(27,52,42,0.04)]">
                <TableHead className="w-10">
                  <input type="checkbox" checked={allSelected} onChange={onToggleAll} />
                </TableHead>
                <TableHead>Name</TableHead>
                <TableHead>Source</TableHead>
                <TableHead>Workflow</TableHead>
                <TableHead>Review</TableHead>
                <TableHead>Execution</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {documents.map((document) => (
                <TableRow key={document.hash}>
                  <TableCell>
                    <input
                      type="checkbox"
                      checked={!!selection[document.hash]}
                      onChange={() => onToggleSelection(document.hash)}
                    />
                  </TableCell>
                  <TableCell className="max-w-[280px] whitespace-normal">
                    <div className="font-medium">{document.name}</div>
                    <div className="mt-1 text-xs text-muted-foreground">{document.raw_path || document.stem}</div>
                  </TableCell>
                  <TableCell className="max-w-[220px] whitespace-normal text-xs">
                    <div>{document.type}</div>
                    <div className="mt-1 text-muted-foreground">
                      {document.source_path || document.source_kind || 'source pending'}
                    </div>
                    {document.pages ? <div className="mt-1 text-muted-foreground">{document.pages} pages</div> : null}
                  </TableCell>
                  <TableCell className="max-w-[220px] whitespace-normal text-xs">
                    <WorkflowBadge label="source" value={document.workflow_state.source_state} />
                    <WorkflowBadge label="summary" value={document.workflow_state.summary_state} />
                    <WorkflowBadge label="promotion" value={document.workflow_state.promotion_state} />
                  </TableCell>
                  <TableCell className="max-w-[200px] whitespace-normal text-xs">
                    <div className="font-medium">{document.workflow_state.review_state}</div>
                    {document.review.summary_score !== null ? (
                      <div className="mt-1 text-muted-foreground">score {document.review.summary_score}</div>
                    ) : null}
                    {document.review.review_notes ? (
                      <div className="mt-1 text-muted-foreground">{document.review.review_notes}</div>
                    ) : null}
                  </TableCell>
                  <TableCell className="max-w-[240px] whitespace-normal text-xs">
                    {document.execution.last_error ? (
                      <div className="rounded-lg bg-red-50 px-2 py-1 text-red-700">
                        {document.execution.last_error}
                      </div>
                    ) : (
                      <div className="text-muted-foreground">clean</div>
                    )}
                    {document.execution.retry_count ? (
                      <div className="mt-1 text-muted-foreground">retries {document.execution.retry_count}</div>
                    ) : null}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        onClick={() => onViewDetail?.(document)}
                        title="View detail"
                      >
                        <Eye className="h-4 w-4" />
                      </Button>
                      {onSummarize && document.workflow_state.source_state === 'ready' ? (
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => onSummarize(document.hash)}
                          title="Summarize"
                        >
                          <Sparkles className="h-4 w-4 text-amber-700" />
                        </Button>
                      ) : null}
                      {onApprove && document.workflow_state.summary_state === 'ready' ? (
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => onApprove(document.hash)}
                          title="Approve"
                        >
                          <ShieldCheck className="h-4 w-4 text-emerald-700" />
                        </Button>
                      ) : null}
                      {onHold && document.workflow_state.summary_state === 'ready' ? (
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => onHold(document.hash)}
                          title="Hold"
                        >
                          <AlertCircle className="h-4 w-4 text-amber-700" />
                        </Button>
                      ) : null}
                      {onReject && document.workflow_state.summary_state === 'ready' ? (
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => onReject(document.hash)}
                          title="Reject"
                        >
                          <Trash2 className="h-4 w-4 text-red-700" />
                        </Button>
                      ) : null}
                      {onPromote && document.workflow_state.review_state === 'approved' ? (
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => onPromote(document.hash)}
                          title="Promote"
                        >
                          <CheckCircle2 className="h-4 w-4 text-emerald-700" />
                        </Button>
                      ) : null}
                      {showDelete ? (
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        className="text-red-600 hover:bg-red-50 hover:text-red-700"
                        onClick={() => {
                          if (confirm(`Remove ${document.name}?`)) {
                            onDelete?.(document.hash);
                          }
                        }}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                      ) : null}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}

function WorkflowBadge({ label, value }: { label: string; value: string }) {
  const tone =
    value === 'ready' || value === 'approved' || value === 'promoted'
      ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
      : value === 'failed' || value === 'rejected'
        ? 'bg-red-50 text-red-700 border-red-200'
        : value === 'running' || value === 'queued'
          ? 'bg-amber-50 text-amber-700 border-amber-200'
          : 'bg-stone-100 text-stone-700 border-stone-200';

  return (
    <div className={`mb-1 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 ${tone}`}>
      <span className="uppercase tracking-[0.12em] text-[10px]">{label}</span>
      <span>{value}</span>
    </div>
  );
}

function DocumentDetailDialog({
  document,
  onOpenChange,
}: {
  document: DocumentItem | null;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={!!document} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>{document?.name || 'Document detail'}</DialogTitle>
          <DialogDescription>
            Workflow state, review metadata, and source references for the selected document.
          </DialogDescription>
        </DialogHeader>

        {document ? (
          <div className="grid gap-4 md:grid-cols-2">
            <DetailBlock
              title="Identity"
              lines={[
                `hash: ${document.hash}`,
                `stem: ${document.stem}`,
                `raw: ${document.raw_path || 'n/a'}`,
                `source: ${document.source_path || 'n/a'}`,
                `summary: ${document.source_summary || 'n/a'}`,
              ]}
            />
            <DetailBlock
              title="Workflow"
              lines={[
                `ingest: ${document.workflow_state.ingest_state}`,
                `ocr: ${document.workflow_state.ocr_state}`,
                `source: ${document.workflow_state.source_state}`,
                `summary: ${document.workflow_state.summary_state}`,
                `review: ${document.workflow_state.review_state}`,
                `promotion: ${document.workflow_state.promotion_state}`,
              ]}
            />
            <DetailBlock
              title="Review"
              lines={[
                `summary score: ${document.review.summary_score ?? 'n/a'}`,
                `approved by: ${document.review.approved_by || 'n/a'}`,
                `approved at: ${document.review.approved_at || 'n/a'}`,
                `notes: ${document.review.review_notes || 'n/a'}`,
              ]}
            />
            <DetailBlock
              title="Execution"
              lines={[
                `last error: ${document.execution.last_error || 'none'}`,
                `retries: ${document.execution.retry_count}`,
                `updated: ${document.execution.updated_at || 'n/a'}`,
                `related pages: ${document.related_count}`,
              ]}
            />
          </div>
        ) : null}

        <DialogFooter showCloseButton />
      </DialogContent>
    </Dialog>
  );
}

function DetailBlock({ title, lines }: { title: string; lines: string[] }) {
  return (
    <div className="rounded-xl border bg-muted/30 p-4">
      <div className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
        {title}
      </div>
      <div className="space-y-2 text-sm">
        {lines.map((line) => (
          <div key={line} className="break-words">
            {line}
          </div>
        ))}
      </div>
    </div>
  );
}
