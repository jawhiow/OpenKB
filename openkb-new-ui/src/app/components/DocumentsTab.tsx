'use client';

import { Fragment, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertCircle,
  CheckCircle2,
  ExternalLink,
  Eye,
  FileUp,
  FolderPlus,
  Loader2,
  Maximize2,
  Minimize2,
  MoreHorizontal,
  RefreshCcw,
  ShieldCheck,
  Sparkles,
  Trash2,
} from 'lucide-react';
import { toast } from '@/components/ui/toaster';
import { confirm as confirmDialog } from '@/components/ui/confirm-dialog';
import { TableSkeleton } from '@/components/ui/skeleton';
import { BatchBar } from '@/components/ui/batch-bar';
import { Pagination } from '@/components/ui/pagination';
import { ActiveFilters, type FilterChip } from '@/components/ui/active-filters';
import { DataFreshness } from '@/components/ui/data-freshness';
import { ArrowDown, ArrowUp, ChevronsUpDown } from 'lucide-react';
import {
  deleteDocument,
  DocumentItem,
  DocumentQueryParams,
  getDocuments,
  importDocuments,
  promoteDocuments,
  rawFileUrl,
  RelatedPageEntry,
  retryDocumentImport,
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

type SortKey = 'name' | 'type' | 'summary_score' | 'source_state' | 'summary_state' | 'review_state' | 'promotion_state';
type SortDir = 'asc' | 'desc';
type RawPreviewKind = 'pdf' | 'image' | 'text' | 'unsupported';

const SORT_ACCESSORS: Record<SortKey, (d: DocumentItem) => string | number> = {
  name: (d) => d.name.toLowerCase(),
  type: (d) => d.type.toLowerCase(),
  summary_score: (d) => d.review.summary_score ?? -1,
  source_state: (d) => d.workflow_state.source_state,
  summary_state: (d) => d.workflow_state.summary_state,
  review_state: (d) => d.workflow_state.review_state,
  promotion_state: (d) => d.workflow_state.promotion_state,
};

const INVENTORY_FILTERS: Array<{ label: string; value: string }> = [
  { label: 'All inventory', value: '' },
  { label: 'New imports', value: 'new' },
  { label: 'Ready to summarize', value: 'ready_to_summarize' },
  { label: 'Needs summary', value: 'needs_summary' },
  { label: 'Has summary page', value: 'has_summary_page' },
  { label: 'Has company page', value: 'has_company_page' },
  { label: 'Has industry page', value: 'has_industry_page' },
  { label: 'Has concept page', value: 'has_concept_page' },
  { label: 'No related pages', value: 'no_related_pages' },
  { label: 'Any failed', value: 'failed' },
];

const INVENTORY_STATUS_FILTERS = INVENTORY_FILTERS.filter((filter) => filter.value);

const REVIEW_FILTERS: Array<{ label: string; value: string }> = [
  { label: 'Needs review', value: 'unreviewed,held,scored' },
  { label: 'Approved', value: 'approved' },
  { label: 'Rejected', value: 'rejected' },
];

const SCORE_FILTERS: Array<{ label: string; value: string }> = [
  { label: 'All scores', value: '' },
  { label: 'Has score', value: 'scored' },
  { label: 'High score 85+', value: 'high' },
  { label: 'Strong score 70-84', value: 'strong' },
  { label: 'Needs attention <70', value: 'attention' },
  { label: 'Unscored', value: 'unscored' },
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
  onNavigateToWiki,
}: {
  kbDir: string;
  onJobStarted: (jobId: string) => void;
  onNavigateToWiki?: (path: string) => void;
}) {
  const queryClient = useQueryClient();
  const [stageView, setStageView] = useState<StageView>('review');
  const [searchQuery, setSearchQuery] = useState('');
  const [inventoryStatusFilters, setInventoryStatusFilters] = useState<string[]>([]);
  const [inventoryDateFilter, setInventoryDateFilter] = useState('');
  const [reviewStateFilter, setReviewStateFilter] = useState('unreviewed,held,scored');
  const [promotionReviewFilter, setPromotionReviewFilter] = useState('approved');
  const [promotionStateFilter, setPromotionStateFilter] = useState('not_selected,failed');
  const [scoreFilter, setScoreFilter] = useState('');
  const [minScore, setMinScore] = useState('');
  const [maxScore, setMaxScore] = useState('');
  const [localPath, setLocalPath] = useState('');
  const [selection, setSelection] = useState<SelectionState>({});
  const [approvedBy, setApprovedBy] = useState('');
  const [reviewNotes, setReviewNotes] = useState('');
  const [summaryScore, setSummaryScore] = useState('');
  const [detailDocument, setDetailDocument] = useState<DocumentItem | null>(null);
  const [rawPreviewDocument, setRawPreviewDocument] = useState<DocumentItem | null>(null);
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>('asc');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [stageKey, setStageKey] = useState<string>(`${stageView}|`);

  // Reset page when stage/filters change (derived-state pattern, avoids set-state-in-effect).
  const currentStageKey = `${stageView}|${searchQuery}|${inventoryStatusFilters.join(',')}|${inventoryDateFilter}|${reviewStateFilter}|${promotionReviewFilter}|${promotionStateFilter}|${scoreFilter}|${minScore}|${maxScore}`;
  if (currentStageKey !== stageKey) {
    setStageKey(currentStageKey);
    setPage(1);
  }

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((dir) => (dir === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('asc');
    }
    setPage(1);
  };

  // Active filter chips per stage. Each chip's onRemove resets that filter to its default.
  const activeFilters: FilterChip[] = (() => {
    const chips: FilterChip[] = [];
    if (searchQuery.trim()) {
      chips.push({
        key: 'search',
        label: `Search: "${searchQuery.trim()}"`,
        onRemove: () => setSearchQuery(''),
      });
    }
    if (stageView === 'inventory') {
      for (const statusFilter of inventoryStatusFilters) {
        const label = INVENTORY_FILTERS.find((f) => f.value === statusFilter)?.label ?? statusFilter;
        chips.push({
          key: `inv-state-${statusFilter}`,
          label: `Status: ${label}`,
          onRemove: () => setInventoryStatusFilters((current) => current.filter((value) => value !== statusFilter)),
        });
      }
    }
    if (stageView === 'inventory' && inventoryDateFilter) {
      chips.push({
        key: 'inv-date',
        label: `Imported: ${inventoryDateFilter}`,
        onRemove: () => setInventoryDateFilter(''),
      });
    }
    if (stageView === 'review' && reviewStateFilter !== 'unreviewed,held,scored') {
      const label = REVIEW_FILTERS.find((f) => f.value === reviewStateFilter)?.label
        ?? reviewStateFilter;
      chips.push({
        key: 'rev-state',
        label: `Review: ${label}`,
        onRemove: () => setReviewStateFilter('unreviewed,held,scored'),
      });
    }
    if (stageView === 'promotion') {
      if (promotionReviewFilter !== 'approved') {
        const label = PROMOTION_REVIEW_FILTERS.find((f) => f.value === promotionReviewFilter)?.label
          ?? promotionReviewFilter;
        chips.push({
          key: 'prom-rev',
          label: `Review: ${label}`,
          onRemove: () => setPromotionReviewFilter('approved'),
        });
      }
      if (promotionStateFilter !== 'not_selected,failed') {
        const label = PROMOTION_STATE_FILTERS.find((f) => f.value === promotionStateFilter)?.label
          ?? promotionStateFilter;
        chips.push({
          key: 'prom-state',
          label: `Promotion: ${label}`,
          onRemove: () => setPromotionStateFilter('not_selected,failed'),
        });
      }
    }
    if (scoreFilter) {
      const label = SCORE_FILTERS.find((f) => f.value === scoreFilter)?.label ?? scoreFilter;
      chips.push({
        key: 'score-filter',
        label: `Score: ${label}`,
        onRemove: () => setScoreFilter(''),
      });
    }
    if (minScore.trim()) {
      chips.push({
        key: 'min-score',
        label: `Min score: ${minScore.trim()}`,
        onRemove: () => setMinScore(''),
      });
    }
    if (maxScore.trim()) {
      chips.push({
        key: 'max-score',
        label: `Max score: ${maxScore.trim()}`,
        onRemove: () => setMaxScore(''),
      });
    }
    return chips;
  })();

  const clearAllFilters = () => {
    setSearchQuery('');
    setScoreFilter('');
    setMinScore('');
    setMaxScore('');
    if (stageView === 'inventory') {
      setInventoryStatusFilters([]);
      setInventoryDateFilter('');
    }
    if (stageView === 'review') setReviewStateFilter('unreviewed,held,scored');
    if (stageView === 'promotion') {
      setPromotionReviewFilter('approved');
      setPromotionStateFilter('not_selected,failed');
    }
  };

  const queryParams = useMemo<DocumentQueryParams>(() => {
    if (stageView === 'inventory') {
      return {
        q: searchQuery,
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
  }, [promotionReviewFilter, promotionStateFilter, reviewStateFilter, searchQuery, stageView]);

  const { data, isLoading, isFetching, dataUpdatedAt, refetch } = useQuery({
    queryKey: ['documents', kbDir, stageView, queryParams],
    queryFn: () => getDocuments(kbDir, queryParams),
    enabled: !!kbDir,
  });

  const serverDocuments = useMemo(() => data?.documents ?? [], [data?.documents]);

  const documents = useMemo(() => {
    const minValue = parseOptionalScore(minScore);
    const maxValue = parseOptionalScore(maxScore);
    const stageFiltered = stageView === 'inventory'
      ? serverDocuments.filter((document) => matchesInventoryFilters(document, inventoryStatusFilters, inventoryDateFilter))
      : serverDocuments;
    return stageFiltered.filter((document) => matchesScoreFilters(document, {
      scoreFilter,
      minScore: minValue,
      maxScore: maxValue,
    }));
  }, [inventoryDateFilter, inventoryStatusFilters, maxScore, minScore, scoreFilter, serverDocuments, stageView]);

  const sortedDocuments = useMemo(() => {
    if (!sortKey) return documents;
    const accessor = SORT_ACCESSORS[sortKey];
    const direction = sortDir === 'asc' ? 1 : -1;
    return [...documents].sort((a, b) => {
      const av = accessor(a);
      const bv = accessor(b);
      if (typeof av === 'number' && typeof bv === 'number') {
        return (av - bv) * direction;
      }
      if (av < bv) return -direction;
      if (av > bv) return direction;
      return 0;
    });
  }, [documents, sortKey, sortDir]);

  const displayDocuments = useMemo(() => {
    const start = (page - 1) * pageSize;
    return sortedDocuments.slice(start, start + pageSize);
  }, [sortedDocuments, page, pageSize]);

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
  const summarizableSelectedHashes = selectedDocuments
    .filter((document) => document.workflow_state.source_state === 'ready')
    .map((document) => document.hash);

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
      setStageView('inventory');
      setInventoryStatusFilters([]);
      setInventoryDateFilter('');
      setSearchQuery('');
      setSelection({});
      handleJobStart(data?.job?.id);
      toast.success('Import job started');
    },
    onError: (error) => toast.error('Import failed', errorMessage(error)),
  });

  const uploadMutation = useMutation({
    mutationFn: (files: File[]) => uploadDocuments(kbDir, files, { import_only: true }),
    onSuccess: (data) => {
      setStageView('inventory');
      setInventoryStatusFilters([]);
      setInventoryDateFilter('');
      setSearchQuery('');
      setSelection({});
      handleJobStart(data?.job?.id);
      toast.success('Upload job started');
    },
    onError: (error) => toast.error('Upload failed', errorMessage(error)),
  });

  const summarizeMutation = useMutation({
    mutationFn: (fileHashes: string[]) => summarizeDocuments(kbDir, fileHashes),
    onSuccess: (data) => {
      handleJobStart(data?.job?.id);
      toast.success('Summarize job started');
    },
    onError: (error) => toast.error('Summarize failed', errorMessage(error)),
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
      toast.success('Documents approved');
    },
    onError: (error) => toast.error('Approve failed', errorMessage(error)),
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
      toast.success('Documents held');
    },
    onError: (error) => toast.error('Hold failed', errorMessage(error)),
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
      toast.success('Documents rejected');
    },
    onError: (error) => toast.error('Reject failed', errorMessage(error)),
  });

  const promoteMutation = useMutation({
    mutationFn: (fileHashes: string[]) => promoteDocuments(kbDir, fileHashes),
    onSuccess: (data) => {
      setSelection({});
      handleJobStart(data?.job?.id);
      toast.success('Promotion job started');
    },
    onError: (error) => toast.error('Promote failed', errorMessage(error)),
  });

  const deleteMutation = useMutation({
    mutationFn: (selector: string) => deleteDocument(kbDir, selector),
    onSuccess: (data) => {
      handleJobStart(data?.job?.id);
      toast.success('Delete job started');
    },
    onError: (error) => toast.error('Delete failed', errorMessage(error)),
  });

  const retryImportMutation = useMutation({
    mutationFn: (selector: string) => retryDocumentImport(kbDir, selector),
    onSuccess: (data) => {
      handleJobStart(data?.job?.id);
      toast.success('Import retry started');
    },
    onError: (error) => toast.error('Retry failed', errorMessage(error)),
  });

  const openRawSource = (document: DocumentItem) => {
    if (!document.raw_exists || !document.raw_path) {
      toast.error('Raw source missing', document.raw_path || document.name);
      return;
    }
    setRawPreviewDocument(document);
  };

  const busy =
    importMutation.isPending ||
    uploadMutation.isPending ||
    summarizeMutation.isPending ||
    approveMutation.isPending ||
    holdMutation.isPending ||
    rejectMutation.isPending ||
    promoteMutation.isPending ||
    deleteMutation.isPending ||
    retryImportMutation.isPending;

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

  const toggleInventoryStatusFilter = (value: string) => {
    setInventoryStatusFilters((current) => {
      if (current.includes(value)) {
        return current.filter((item) => item !== value);
      }
      return [...current, value];
    });
  };

  return (
    <Card className="relative h-full flex flex-col rounded-none border-t-0 border-b-0 border-x-0 sm:border-x sm:rounded-lg overflow-hidden min-h-0 bg-[linear-gradient(180deg,rgba(255,255,255,0.95),rgba(249,247,242,0.95))] dark:bg-[linear-gradient(180deg,rgba(24,24,27,0.96),rgba(33,33,36,0.96))]">
      <CardHeader className="hidden border-b bg-[linear-gradient(135deg,rgba(27,52,42,0.06),rgba(186,151,91,0.12))] dark:bg-[linear-gradient(135deg,rgba(16,28,24,0.82),rgba(79,58,24,0.38))] md:block">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
          <div>
            <CardTitle className="text-xl">Staged Document Workbench</CardTitle>
            <CardDescription>
              Import into inventory first, review summaries second, promote approved documents last.
            </CardDescription>
          </div>
          <div className="hidden flex-col gap-3 md:flex xl:items-end">
            <div className="flex flex-wrap gap-2">
              <Input
                placeholder="Absolute local path"
                value={localPath}
                onChange={(event) => setLocalPath(event.target.value)}
                className="w-full min-w-[260px] bg-background/80 dark:bg-input/30 xl:w-80"
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
                <Button variant="secondary" disabled={busy} className="bg-background/90 dark:bg-input/30">
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
              <DataFreshness
                updatedAt={dataUpdatedAt}
                isFetching={isFetching}
                onRefresh={() => void refetch()}
              />
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
          <div className="border-b px-2 py-2 md:px-6 md:pb-0 md:pt-4">
            <TabsList className="grid h-9 w-full grid-cols-3 bg-muted/70 dark:bg-muted/40 md:h-10 md:max-w-2xl">
              <TabsTrigger value="inventory">Inventory</TabsTrigger>
              <TabsTrigger value="review">
                <span className="md:hidden">Review</span>
                <span className="hidden md:inline">Summary Review</span>
              </TabsTrigger>
              <TabsTrigger value="promotion">
                <span className="md:hidden">Promo</span>
                <span className="hidden md:inline">Promotion</span>
              </TabsTrigger>
            </TabsList>
            {activeFilters.length > 0 ? (
              <div className="pb-3 pt-3">
                <ActiveFilters filters={activeFilters} onClearAll={clearAllFilters} />
              </div>
            ) : null}
          </div>

          <TabsContent value="inventory" className="m-0 flex min-h-0 flex-1 flex-col">
            <div className="hidden min-h-0 flex-1 flex-col md:flex">
              <InventoryToolbar
                searchQuery={searchQuery}
                onSearchChange={setSearchQuery}
                statusFilters={inventoryStatusFilters}
                onToggleStatusFilter={toggleInventoryStatusFilter}
                importDate={inventoryDateFilter}
                onImportDateChange={setInventoryDateFilter}
                scoreFilter={scoreFilter}
                onScoreFilterChange={setScoreFilter}
                minScore={minScore}
                onMinScoreChange={setMinScore}
                maxScore={maxScore}
                onMaxScoreChange={setMaxScore}
                rightSlot={
                  <Button
                    onClick={() => summarizeMutation.mutate(summarizableSelectedHashes)}
                    disabled={!summarizableSelectedHashes.length || summarizeMutation.isPending}
                    className="bg-[oklch(0.55_0.11_70)] text-white hover:bg-[oklch(0.5_0.11_70)]"
                  >
                    {summarizeMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
                    Summarize Selected
                  </Button>
                }
              />
              <DocumentStageTable
                documents={displayDocuments}
                isLoading={isLoading}
                selection={visibleSelection}
                onToggleSelection={toggleSelection}
                onToggleAll={toggleAll}
                sortKey={sortKey}
                sortDir={sortDir}
                onSort={handleSort}
                hasActiveFilters={activeFilters.length > 0}
                onClearFilters={clearAllFilters}
                onDelete={(selector) => deleteMutation.mutate(selector)}
                onRetryImport={(hash) => retryImportMutation.mutate(hash)}
                onSummarize={(hash) => summarizeMutation.mutate([hash])}
                onApprove={(hash) =>
                  approveMutation.mutate([hash])
                }
                onPromote={(hash) => promoteMutation.mutate([hash])}
                onViewDetail={setDetailDocument}
                showDelete
              />
              <Pagination
                page={page}
                pageSize={pageSize}
                total={sortedDocuments.length}
                onPageChange={setPage}
                onPageSizeChange={(size) => {
                  setPageSize(size);
                  setPage(1);
                }}
                label="documents"
              />
            </div>
            <MobileDocumentQueueList
              stage="inventory"
              documents={displayDocuments}
              isLoading={isLoading}
              searchQuery={searchQuery}
              onSearchChange={setSearchQuery}
              hasActiveFilters={activeFilters.length > 0}
              onClearFilters={clearAllFilters}
              onViewDetail={setDetailDocument}
              onPrimaryAction={(hash) => summarizeMutation.mutate([hash])}
              primaryActionLabel="Summarize"
              primaryActionIcon="sparkles"
              canPrimaryAction={(document) => document.workflow_state.source_state === 'ready'}
            />
          </TabsContent>

          <TabsContent value="review" className="m-0 flex min-h-0 flex-1 flex-col">
            <div className="hidden md:block">
              <StageToolbar
                searchQuery={searchQuery}
                onSearchChange={setSearchQuery}
                filterLabel="Review state"
                filterValue={reviewStateFilter}
                onFilterChange={setReviewStateFilter}
                filters={REVIEW_FILTERS}
                scoreFilter={scoreFilter}
                onScoreFilterChange={setScoreFilter}
                minScore={minScore}
                onMinScoreChange={setMinScore}
                maxScore={maxScore}
                onMaxScoreChange={setMaxScore}
                rightSlot={
                  <div className="flex flex-wrap items-center gap-2">
                    <Input
                      placeholder="Reviewer"
                      value={approvedBy}
                      onChange={(event) => setApprovedBy(event.target.value)}
                      className="w-36 bg-background dark:bg-input/30"
                    />
                    <Input
                      placeholder="Score"
                      value={summaryScore}
                      onChange={(event) => setSummaryScore(event.target.value)}
                      className="w-24 bg-background dark:bg-input/30"
                    />
                    <Input
                      placeholder="Review notes"
                      value={reviewNotes}
                      onChange={(event) => setReviewNotes(event.target.value)}
                      className="w-48 bg-background dark:bg-input/30"
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
            </div>
            <MobileDocumentReviewList
              documents={displayDocuments}
              isLoading={isLoading}
              selection={visibleSelection}
              onToggleSelection={toggleSelection}
              onApprove={(hash) => approveMutation.mutate([hash])}
              onHold={(hash) => holdMutation.mutate([hash])}
              onReject={(hash) => rejectMutation.mutate([hash])}
              onViewDetail={setDetailDocument}
              searchQuery={searchQuery}
              onSearchChange={setSearchQuery}
              reviewStateFilter={reviewStateFilter}
              onReviewStateFilterChange={setReviewStateFilter}
              hasActiveFilters={activeFilters.length > 0}
              onClearFilters={clearAllFilters}
            />
            <div className="hidden min-h-0 flex-1 flex-col md:flex">
              <DocumentStageTable
                documents={displayDocuments}
                isLoading={isLoading}
                selection={visibleSelection}
                onToggleSelection={toggleSelection}
                onToggleAll={toggleAll}
                sortKey={sortKey}
                sortDir={sortDir}
                onSort={handleSort}
                hasActiveFilters={activeFilters.length > 0}
                onClearFilters={clearAllFilters}
                onApprove={(hash) => approveMutation.mutate([hash])}
                onHold={(hash) => holdMutation.mutate([hash])}
                onReject={(hash) => rejectMutation.mutate([hash])}
                onViewDetail={setDetailDocument}
                inlineScorecard
              />
            </div>
            <div className="hidden md:block">
              <Pagination
                page={page}
                pageSize={pageSize}
                total={sortedDocuments.length}
                onPageChange={setPage}
                onPageSizeChange={(size) => {
                  setPageSize(size);
                  setPage(1);
                }}
                label="documents"
              />
            </div>
          </TabsContent>

          <TabsContent value="promotion" className="m-0 flex min-h-0 flex-1 flex-col">
            <div className="hidden border-b bg-amber-100/40 px-6 py-4 dark:bg-amber-500/10 md:block">
              <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
                <div className="flex flex-1 flex-wrap items-center gap-2">
                  <Input
                    placeholder="Search approved documents"
                    value={searchQuery}
                    onChange={(event) => setSearchQuery(event.target.value)}
                    className="w-full bg-background dark:bg-input/30 xl:w-72"
                  />
                  <select
                    value={promotionReviewFilter}
                    onChange={(event) => setPromotionReviewFilter(event.target.value)}
                    className="h-9 rounded-md border border-border bg-background px-3 text-sm text-foreground dark:bg-input/30"
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
                    className="h-9 rounded-md border border-border bg-background px-3 text-sm text-foreground dark:bg-input/30"
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
            <div className="hidden min-h-0 flex-1 flex-col md:flex">
              <DocumentStageTable
                documents={displayDocuments}
                isLoading={isLoading}
                selection={visibleSelection}
                onToggleSelection={toggleSelection}
                onToggleAll={toggleAll}
                sortKey={sortKey}
                sortDir={sortDir}
                onSort={handleSort}
                hasActiveFilters={activeFilters.length > 0}
                onClearFilters={clearAllFilters}
                onPromote={(hash) => promoteMutation.mutate([hash])}
                onViewDetail={setDetailDocument}
              />
              <Pagination
                page={page}
                pageSize={pageSize}
                total={sortedDocuments.length}
                onPageChange={setPage}
                onPageSizeChange={(size) => {
                  setPageSize(size);
                  setPage(1);
                }}
                label="documents"
              />
            </div>
            <MobileDocumentQueueList
              stage="promotion"
              documents={displayDocuments}
              isLoading={isLoading}
              searchQuery={searchQuery}
              onSearchChange={setSearchQuery}
              hasActiveFilters={activeFilters.length > 0}
              onClearFilters={clearAllFilters}
              onViewDetail={setDetailDocument}
              onPrimaryAction={(hash) => promoteMutation.mutate([hash])}
              primaryActionLabel="Promote"
              primaryActionIcon="check"
              canPrimaryAction={(document) => document.workflow_state.review_state === 'approved'}
            />
          </TabsContent>
        </Tabs>
      </CardContent>

      {stageView === 'review' && selectedDocuments.length > 0 ? (
        <div className="border-t bg-muted/40 px-6 py-3 text-xs text-muted-foreground dark:bg-muted/20">
          Selected review set: {selectedDocuments.map((document) => document.stem).join(', ')}
        </div>
      ) : null}

      <BatchBar count={selectedHashes.length} onClear={() => setSelection({})} itemLabel="documents selected" className="bottom-20 md:bottom-4">
        {stageView === 'inventory' && (
          <Button
            size="sm"
            onClick={() => summarizeMutation.mutate(summarizableSelectedHashes)}
            disabled={summarizeMutation.isPending || !summarizableSelectedHashes.length}
          >
            {summarizeMutation.isPending ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Sparkles className="mr-1.5 h-3.5 w-3.5" />}
            Summarize
          </Button>
        )}
        {stageView === 'review' && (
          <>
            <Button
              size="sm"
              onClick={() => approveMutation.mutate(selectedHashes)}
              disabled={approveMutation.isPending}
              className="bg-emerald-600 text-white hover:bg-emerald-700"
            >
              <ShieldCheck className="mr-1.5 h-3.5 w-3.5" />
              Approve
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => holdMutation.mutate(selectedHashes)}
              disabled={holdMutation.isPending}
            >
              Hold
            </Button>
            <Button
              size="sm"
              variant="destructive"
              onClick={() => rejectMutation.mutate(selectedHashes)}
              disabled={rejectMutation.isPending}
            >
              Reject
            </Button>
          </>
        )}
        {stageView === 'promotion' && (
          <Button
            size="sm"
            onClick={() => promoteMutation.mutate(selectedHashes)}
            disabled={promoteMutation.isPending}
          >
            {promoteMutation.isPending ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="mr-1.5 h-3.5 w-3.5" />}
            Promote
          </Button>
        )}
      </BatchBar>

      <DocumentDetailDialog
        document={detailDocument}
        onOpenChange={(open) => !open && setDetailDocument(null)}
        onNavigateToWiki={(path) => {
          setDetailDocument(null);
          onNavigateToWiki?.(path);
        }}
        onOpenRawSource={openRawSource}
      />
      <RawSourcePreviewDialog
        document={rawPreviewDocument}
        kbDir={kbDir}
        onOpenChange={(open) => !open && setRawPreviewDocument(null)}
      />
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
  scoreFilter,
  onScoreFilterChange,
  minScore,
  onMinScoreChange,
  maxScore,
  onMaxScoreChange,
  rightSlot,
}: {
  searchQuery: string;
  onSearchChange: (value: string) => void;
  filterLabel: string;
  filterValue: string;
  onFilterChange: (value: string) => void;
  filters: Array<{ label: string; value: string }>;
  scoreFilter: string;
  onScoreFilterChange: (value: string) => void;
  minScore: string;
  onMinScoreChange: (value: string) => void;
  maxScore: string;
  onMaxScoreChange: (value: string) => void;
  rightSlot?: React.ReactNode;
}) {
  return (
    <div className="border-b bg-muted/30 px-6 py-4 dark:bg-muted/15">
      <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
        <div className="flex flex-1 flex-wrap items-center gap-2">
          <Input
            placeholder="Search documents, paths, or source kind"
            value={searchQuery}
            onChange={(event) => onSearchChange(event.target.value)}
            className="w-full bg-background dark:bg-input/30 xl:w-80"
          />
          <label className="text-xs font-medium uppercase tracking-[0.16em] text-muted-foreground">
            {filterLabel}
          </label>
          <select
            value={filterValue}
            onChange={(event) => onFilterChange(event.target.value)}
            className="h-9 rounded-md border border-border bg-background px-3 text-sm text-foreground dark:bg-input/30"
          >
            {filters.map((filter) => (
              <option key={filter.label} value={filter.value}>
                {filter.label}
              </option>
            ))}
          </select>
          <label className="text-xs font-medium uppercase tracking-[0.16em] text-muted-foreground">
            Score
          </label>
          <select
            value={scoreFilter}
            onChange={(event) => onScoreFilterChange(event.target.value)}
            className="h-9 rounded-md border border-border bg-background px-3 text-sm text-foreground dark:bg-input/30"
          >
            {SCORE_FILTERS.map((filter) => (
              <option key={filter.label} value={filter.value}>
                {filter.label}
              </option>
            ))}
          </select>
          <Input
            placeholder="Min"
            inputMode="numeric"
            value={minScore}
            onChange={(event) => onMinScoreChange(event.target.value)}
            className="w-20 bg-background dark:bg-input/30"
          />
          <Input
            placeholder="Max"
            inputMode="numeric"
            value={maxScore}
            onChange={(event) => onMaxScoreChange(event.target.value)}
            className="w-20 bg-background dark:bg-input/30"
          />
        </div>
        {rightSlot}
      </div>
    </div>
  );
}

function InventoryToolbar({
  searchQuery,
  onSearchChange,
  statusFilters,
  onToggleStatusFilter,
  importDate,
  onImportDateChange,
  scoreFilter,
  onScoreFilterChange,
  minScore,
  onMinScoreChange,
  maxScore,
  onMaxScoreChange,
  rightSlot,
}: {
  searchQuery: string;
  onSearchChange: (value: string) => void;
  statusFilters: string[];
  onToggleStatusFilter: (value: string) => void;
  importDate: string;
  onImportDateChange: (value: string) => void;
  scoreFilter: string;
  onScoreFilterChange: (value: string) => void;
  minScore: string;
  onMinScoreChange: (value: string) => void;
  maxScore: string;
  onMaxScoreChange: (value: string) => void;
  rightSlot?: React.ReactNode;
}) {
  return (
    <div className="border-b bg-muted/30 px-6 py-4 dark:bg-muted/15">
      <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
        <div className="flex flex-1 flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <Input
              placeholder="Search documents, paths, or source kind"
              value={searchQuery}
              onChange={(event) => onSearchChange(event.target.value)}
              className="w-full bg-background dark:bg-input/30 xl:w-80"
            />
            <label className="text-xs font-medium uppercase tracking-[0.16em] text-muted-foreground">
              Imported
            </label>
            <Input
              type="date"
              value={importDate}
              onChange={(event) => onImportDateChange(event.target.value)}
              className="w-40 bg-background dark:bg-input/30"
            />
            <label className="text-xs font-medium uppercase tracking-[0.16em] text-muted-foreground">
              Score
            </label>
            <select
              value={scoreFilter}
              onChange={(event) => onScoreFilterChange(event.target.value)}
              className="h-9 rounded-md border border-border bg-background px-3 text-sm text-foreground dark:bg-input/30"
            >
              {SCORE_FILTERS.map((filter) => (
                <option key={filter.label} value={filter.value}>
                  {filter.label}
                </option>
              ))}
            </select>
            <Input
              placeholder="Min"
              inputMode="numeric"
              value={minScore}
              onChange={(event) => onMinScoreChange(event.target.value)}
              className="w-20 bg-background dark:bg-input/30"
            />
            <Input
              placeholder="Max"
              inputMode="numeric"
              value={maxScore}
              onChange={(event) => onMaxScoreChange(event.target.value)}
              className="w-20 bg-background dark:bg-input/30"
            />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-medium uppercase tracking-[0.16em] text-muted-foreground">
              Status
            </span>
            {INVENTORY_STATUS_FILTERS.map((filter) => {
              const selected = statusFilters.includes(filter.value);
              return (
                <Button
                  key={filter.value}
                  type="button"
                  size="xs"
                  variant={selected ? 'default' : 'outline'}
                  onClick={() => onToggleStatusFilter(filter.value)}
                  aria-pressed={selected}
                  className={selected ? 'bg-[oklch(0.34_0.06_165)] text-white hover:bg-[oklch(0.29_0.06_165)]' : ''}
                >
                  {filter.label}
                </Button>
              );
            })}
          </div>
        </div>
        {rightSlot}
      </div>
    </div>
  );
}

function MobileDocumentReviewList({
  documents,
  isLoading,
  selection,
  onToggleSelection,
  onApprove,
  onHold,
  onReject,
  onViewDetail,
  searchQuery,
  onSearchChange,
  reviewStateFilter,
  onReviewStateFilterChange,
  hasActiveFilters,
  onClearFilters,
}: {
  documents: DocumentItem[];
  isLoading: boolean;
  selection: SelectionState;
  onToggleSelection: (hash: string) => void;
  onApprove: (hash: string) => void;
  onHold: (hash: string) => void;
  onReject: (hash: string) => void;
  onViewDetail: (document: DocumentItem) => void;
  searchQuery: string;
  onSearchChange: (value: string) => void;
  reviewStateFilter: string;
  onReviewStateFilterChange: (value: string) => void;
  hasActiveFilters: boolean;
  onClearFilters: () => void;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col md:hidden">
      <div className="shrink-0 border-b bg-muted/20 px-1.5 py-1.5 dark:bg-muted/10">
        <Input
          placeholder="Search review queue"
          value={searchQuery}
          onChange={(event) => onSearchChange(event.target.value)}
          className="h-8 bg-background dark:bg-input/30 text-sm"
        />
        <div className="mt-1.5 grid grid-cols-3 gap-1 rounded-lg bg-muted/90 p-0.5 dark:bg-muted/40">
          {REVIEW_FILTERS.map((filter) => {
            const active = filter.value === reviewStateFilter;
            return (
              <button
                key={filter.value}
                type="button"
                onClick={() => onReviewStateFilterChange(filter.value)}
                className={`h-7 rounded-md px-1.5 text-[10px] font-bold transition-colors ${
                  active
                    ? 'bg-background text-primary shadow-sm'
                    : 'text-muted-foreground hover:bg-background/60 hover:text-foreground'
                }`}
                aria-pressed={active}
              >
                {filter.label.replace('Needs review', 'Review')}
              </button>
            );
          })}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-1.5 py-1.5">
        {isLoading ? (
          <div className="space-y-3">
            {Array.from({ length: 4 }).map((_, index) => (
              <div key={index} className="h-44 animate-pulse rounded-xl border bg-muted/30" />
            ))}
          </div>
        ) : documents.length === 0 ? (
          <div className="flex min-h-[36vh] flex-col items-center justify-center gap-3 rounded-xl border border-dashed bg-background/70 px-5 text-center">
            <AlertCircle className="h-7 w-7 text-muted-foreground" />
            <div>
              <p className="font-semibold">No documents to review</p>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                {hasActiveFilters ? 'Clear filters or widen the search.' : 'Reviewed summaries will appear here when ready.'}
              </p>
            </div>
            {hasActiveFilters ? (
              <Button type="button" size="sm" variant="outline" onClick={onClearFilters}>
                Clear filters
              </Button>
            ) : null}
          </div>
        ) : (
          <div className="space-y-1.5 pb-2">
            {documents.map((document) => (
              <MobileReviewCard
                key={document.hash}
                document={document}
                selected={!!selection[document.hash]}
                onToggleSelection={() => onToggleSelection(document.hash)}
                onApprove={() => onApprove(document.hash)}
                onHold={() => onHold(document.hash)}
                onReject={() => onReject(document.hash)}
                onViewDetail={() => onViewDetail(document)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function MobileDocumentQueueList({
  stage,
  documents,
  isLoading,
  searchQuery,
  onSearchChange,
  hasActiveFilters,
  onClearFilters,
  onViewDetail,
  onPrimaryAction,
  primaryActionLabel,
  primaryActionIcon,
  canPrimaryAction,
}: {
  stage: 'inventory' | 'promotion';
  documents: DocumentItem[];
  isLoading: boolean;
  searchQuery: string;
  onSearchChange: (value: string) => void;
  hasActiveFilters: boolean;
  onClearFilters: () => void;
  onViewDetail: (document: DocumentItem) => void;
  onPrimaryAction: (hash: string) => void;
  primaryActionLabel: string;
  primaryActionIcon: 'sparkles' | 'check';
  canPrimaryAction: (document: DocumentItem) => boolean;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col md:hidden">
      <div className="shrink-0 border-b bg-muted/20 px-3 py-3 dark:bg-muted/10">
        <Input
          placeholder={stage === 'inventory' ? 'Search inventory' : 'Search approved documents'}
          value={searchQuery}
          onChange={(event) => onSearchChange(event.target.value)}
          className="h-10 bg-background dark:bg-input/30"
        />
      </div>
      <div className="flex-1 overflow-y-auto px-3 py-3">
        {isLoading ? (
          <div className="space-y-3">
            {Array.from({ length: 4 }).map((_, index) => (
              <div key={index} className="h-36 animate-pulse rounded-xl border bg-muted/30" />
            ))}
          </div>
        ) : documents.length === 0 ? (
          <div className="flex min-h-[45vh] flex-col items-center justify-center gap-3 rounded-xl border border-dashed bg-background/70 px-5 text-center">
            <AlertCircle className="h-7 w-7 text-muted-foreground" />
            <div>
              <p className="font-semibold">No matching documents</p>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                {hasActiveFilters ? 'Clear filters or widen the search.' : 'Documents will appear here when available.'}
              </p>
            </div>
            {hasActiveFilters ? (
              <Button type="button" size="sm" variant="outline" onClick={onClearFilters}>
                Clear filters
              </Button>
            ) : null}
          </div>
        ) : (
          <div className="space-y-3 pb-6">
            {documents.map((document) => {
              const primaryEnabled = canPrimaryAction(document);
              return (
                <article key={document.hash} className="rounded-xl border bg-background/90 p-3 shadow-sm dark:bg-card/60">
                  <button type="button" onClick={() => onViewDetail(document)} className="w-full text-left">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <h3 className="line-clamp-2 text-sm font-semibold leading-snug text-foreground">{document.name}</h3>
                        <div className="mt-2 flex flex-wrap items-center gap-1.5">
                          <StatusPill value={document.workflow_state.source_state} label="source" />
                          <StatusPill value={document.workflow_state.review_state} label="review" />
                          {document.review.summary_score !== null ? (
                            <span className="rounded-full border bg-muted/40 px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                              Score {document.review.summary_score}
                            </span>
                          ) : null}
                        </div>
                      </div>
                      <MoreHorizontal className="mt-1 h-4 w-4 shrink-0 text-muted-foreground" />
                    </div>
                    <div className="mt-2 grid grid-cols-2 gap-1.5 text-[10px] text-muted-foreground">
                      <div className="truncate rounded-lg bg-muted/30 px-2 py-1">{document.type || document.source_kind || 'source'}</div>
                      <div className="truncate rounded-lg bg-muted/30 px-2 py-1 text-right">{document.pages ? `${document.pages} pages` : `${document.related_count} related`}</div>
                    </div>
                  </button>
                  <div className="mt-3 flex items-center justify-between gap-2">
                    <Button type="button" size="sm" variant="outline" onClick={() => onViewDetail(document)}>
                      <Eye className="h-3.5 w-3.5" />
                      View
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      onClick={() => onPrimaryAction(document.hash)}
                      disabled={!primaryEnabled}
                      className={primaryActionIcon === 'check' ? 'bg-emerald-600 text-white hover:bg-emerald-700' : 'bg-amber-600 text-white hover:bg-amber-700'}
                    >
                      {primaryActionIcon === 'check' ? <CheckCircle2 className="h-3.5 w-3.5" /> : <Sparkles className="h-3.5 w-3.5" />}
                      {primaryActionLabel}
                    </Button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function MobileReviewCard({
  document,
  selected,
  onToggleSelection,
  onApprove,
  onHold,
  onReject,
  onViewDetail,
}: {
  document: DocumentItem;
  selected: boolean;
  onToggleSelection: () => void;
  onApprove: () => void;
  onHold: () => void;
  onReject: () => void;
  onViewDetail: () => void;
}) {
  const score = document.review.summary_score;
  const assessment = document.review.summary_scorecard?.overall_assessment || document.review.review_notes || '';

  return (
    <article className="rounded-xl border bg-background/90 p-3 shadow-sm dark:bg-card/60">
      <div className="flex items-start gap-3">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggleSelection}
          aria-label={`Select ${document.name}`}
          className="mt-0.5 h-4 w-4 shrink-0 accent-primary"
        />
        <button type="button" onClick={onViewDetail} className="min-w-0 flex-1 text-left">
          <h3 className="line-clamp-2 text-sm font-semibold leading-snug text-foreground">{document.name}</h3>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <StatusPill value={document.workflow_state.review_state} />
            <StatusPill value={document.workflow_state.summary_state} label="summary" />
            <span className="rounded-full border bg-muted/40 px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
              {score === null ? 'No score' : `Score ${score}`}
            </span>
          </div>
        </button>
        <Button type="button" variant="ghost" size="icon-sm" onClick={onViewDetail} aria-label="Open document detail">
          <MoreHorizontal className="h-4 w-4" />
        </Button>
      </div>

      <button type="button" onClick={onViewDetail} className="mt-2.5 w-full text-left">
        {assessment ? (
          <p className="line-clamp-3 text-xs leading-snug text-muted-foreground">{assessment}</p>
        ) : (
          <p className="text-xs italic text-muted-foreground">Open detail to inspect the staged summary and related pages.</p>
        )}
        <div className="mt-2 grid grid-cols-2 gap-1.5 text-[10px] text-muted-foreground">
          <div className="truncate rounded-lg bg-muted/30 px-2 py-1">{document.type || document.source_kind || 'source'}</div>
          <div className="truncate rounded-lg bg-muted/30 px-2 py-1 text-right">{document.related_count} related</div>
        </div>
      </button>

      <div className="mt-2.5 grid grid-cols-[1fr_1fr_auto] gap-1.5">
        <Button type="button" size="sm" onClick={onApprove} className="bg-emerald-600 text-white hover:bg-emerald-700">
          <ShieldCheck className="h-3.5 w-3.5" />
          Approve
        </Button>
        <Button type="button" size="sm" variant="secondary" onClick={onHold}>
          Hold
        </Button>
        <Button type="button" size="icon-sm" variant="ghost" onClick={onReject} aria-label="Reject document" title="Reject">
          <Trash2 className="h-4 w-4 text-red-600 dark:text-red-300" />
        </Button>
      </div>
    </article>
  );
}

function StatusPill({ value, label }: { value: string; label?: string }) {
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${toneClass(value)}`}>
      {label ? `${label}: ${value}` : value}
    </span>
  );
}

function DocumentStageTable({
  documents,
  isLoading,
  selection,
  onToggleSelection,
  onToggleAll,
  sortKey,
  sortDir,
  onSort,
  hasActiveFilters = false,
  onClearFilters,
  onDelete,
  onRetryImport,
  onSummarize,
  onApprove,
  onHold,
  onReject,
  onPromote,
  onViewDetail,
  inlineScorecard = false,
  showDelete = false,
}: {
  documents: DocumentItem[];
  isLoading: boolean;
  selection: SelectionState;
  onToggleSelection: (hash: string) => void;
  onToggleAll: () => void;
  sortKey?: SortKey | null;
  sortDir?: SortDir;
  onSort?: (key: SortKey) => void;
  hasActiveFilters?: boolean;
  onClearFilters?: () => void;
  onDelete?: (selector: string) => void;
  onRetryImport?: (selector: string) => void;
  onSummarize?: (hash: string) => void;
  onApprove?: (hash: string) => void;
  onHold?: (hash: string) => void;
  onReject?: (hash: string) => void;
  onPromote?: (hash: string) => void;
  onViewDetail?: (document: DocumentItem) => void;
  inlineScorecard?: boolean;
  showDelete?: boolean;
}) {
  const allSelected = documents.length > 0 && documents.every((document) => selection[document.hash]);

  return (
    <div className="min-w-0 flex-1 overflow-auto px-6 py-4">
      {isLoading ? (
        <div className="overflow-hidden rounded-2xl border bg-background/85 p-4 shadow-sm dark:bg-card/40">
          <TableSkeleton rows={6} columns={7} />
        </div>
      ) : documents.length === 0 ? (
        <div className="flex h-full flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-border bg-background/65 px-6 py-12 text-center dark:bg-card/40">
          <AlertCircle className="h-8 w-8 text-muted-foreground" />
          <div>
            <p className="font-medium">No matching documents</p>
            <p className="mt-1 max-w-md text-sm text-muted-foreground">
              {hasActiveFilters
                ? 'Try clearing the active filters or widening the search.'
                : 'Import files into inventory using the controls above to get started.'}
            </p>
          </div>
          {hasActiveFilters && onClearFilters ? (
            <Button type="button" size="sm" variant="outline" onClick={onClearFilters}>
              Clear filters
            </Button>
          ) : null}
        </div>
      ) : (
        <div className="min-w-0 overflow-x-auto overflow-y-hidden rounded-2xl border bg-background/85 shadow-sm custom-scrollbar dark:bg-card/40">
          <Table className={inlineScorecard ? "min-w-[1080px] table-fixed" : "min-w-[980px] table-fixed"}>
            <TableHeader>
              <TableRow className="bg-muted/40 dark:bg-muted/20">
                <TableHead className="w-10">
                  <input type="checkbox" checked={allSelected} onChange={onToggleAll} aria-label="Select all visible rows" />
                </TableHead>
                <TableHead className={inlineScorecard ? "w-[260px]" : "w-[300px]"}>
                  <SortableHeader label="Name" sortKey="name" current={sortKey ?? null} dir={sortDir ?? 'asc'} onSort={onSort} />
                </TableHead>
                <TableHead className="w-[96px]">
                  <SortableHeader label="Score" sortKey="summary_score" current={sortKey ?? null} dir={sortDir ?? 'asc'} onSort={onSort} />
                </TableHead>
                <TableHead className={inlineScorecard ? "w-[180px]" : "w-[220px]"}>
                  <SortableHeader label="Source" sortKey="source_state" current={sortKey ?? null} dir={sortDir ?? 'asc'} onSort={onSort} />
                </TableHead>
                <TableHead className="w-[220px]">
                  <SortableHeader label="Workflow" sortKey="summary_state" current={sortKey ?? null} dir={sortDir ?? 'asc'} onSort={onSort} />
                </TableHead>
                <TableHead className={inlineScorecard ? "w-[150px]" : "w-[180px]"}>
                  <SortableHeader label="Review" sortKey="review_state" current={sortKey ?? null} dir={sortDir ?? 'asc'} onSort={onSort} />
                </TableHead>
                <TableHead className={inlineScorecard ? "w-[150px]" : "w-[220px]"}>Execution</TableHead>
                <TableHead className="w-[128px] text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {documents.map((document) => (
                <Fragment key={document.hash}>
                  <TableRow key={document.hash}>
                    <TableCell>
                      <input
                        type="checkbox"
                        checked={!!selection[document.hash]}
                        onChange={() => onToggleSelection(document.hash)}
                        aria-label={`Select ${document.name}`}
                      />
                    </TableCell>
                    <TableCell className="whitespace-normal">
                      <div className="font-medium">{document.name}</div>
                      <div className="mt-1 break-words text-xs text-muted-foreground">{document.raw_path || document.stem}</div>
                    </TableCell>
                    <TableCell className="whitespace-normal text-xs">
                      {document.review.summary_score !== null ? (
                        <div>
                          <div className="font-semibold text-sm">{document.review.summary_score}</div>
                          <div className="mt-1 text-muted-foreground">
                            {document.review.summary_score_source || 'auto'}
                          </div>
                        </div>
                      ) : (
                        <div className="text-muted-foreground">n/a</div>
                      )}
                    </TableCell>
                    <TableCell className="whitespace-normal text-xs">
                      <div>{document.type}</div>
                      <div className="mt-1 text-muted-foreground">
                        {document.source_path || document.source_kind || 'source pending'}
                      </div>
                      {document.pages ? <div className="mt-1 text-muted-foreground">{document.pages} pages</div> : null}
                    </TableCell>
                    <TableCell className="whitespace-normal text-xs">
                      <WorkflowPipeline state={document.workflow_state} />
                    </TableCell>
                    <TableCell className="whitespace-normal text-xs">
                      <div className="font-medium">{document.workflow_state.review_state}</div>
                      {document.review.summary_score !== null ? (
                        <div className="mt-1 text-muted-foreground">score {document.review.summary_score}</div>
                      ) : null}
                      {document.review.review_notes ? (
                        <div className="mt-1 text-muted-foreground">{document.review.review_notes}</div>
                      ) : null}
                    </TableCell>
                    <TableCell className="whitespace-normal text-xs">
                      {document.execution.last_error ? (
                        <div className="rounded-lg bg-red-50 px-2 py-1 text-red-700 dark:bg-red-500/10 dark:text-red-300">
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
                            <Sparkles className="h-4 w-4 text-amber-700 dark:text-amber-300" />
                          </Button>
                        ) : null}
                        {onRetryImport && canRetryImport(document) ? (
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            onClick={() => onRetryImport(document.hash)}
                            title="Retry import"
                          >
                            <RefreshCcw className="h-4 w-4 text-red-700 dark:text-red-300" />
                          </Button>
                        ) : null}
                        {onApprove && document.workflow_state.summary_state === 'ready' ? (
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            onClick={() => onApprove(document.hash)}
                            title="Approve"
                          >
                            <ShieldCheck className="h-4 w-4 text-emerald-700 dark:text-emerald-300" />
                          </Button>
                        ) : null}
                        {onHold && document.workflow_state.summary_state === 'ready' ? (
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            onClick={() => onHold(document.hash)}
                            title="Hold"
                          >
                            <AlertCircle className="h-4 w-4 text-amber-700 dark:text-amber-300" />
                          </Button>
                        ) : null}
                        {onReject && document.workflow_state.summary_state === 'ready' ? (
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            onClick={() => onReject(document.hash)}
                            title="Reject"
                          >
                            <Trash2 className="h-4 w-4 text-red-700 dark:text-red-300" />
                          </Button>
                        ) : null}
                        {onPromote && document.workflow_state.review_state === 'approved' ? (
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            onClick={() => onPromote(document.hash)}
                            title="Promote"
                          >
                            <CheckCircle2 className="h-4 w-4 text-emerald-700 dark:text-emerald-300" />
                          </Button>
                        ) : null}
                        {showDelete ? (
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            className="text-red-600 hover:bg-red-50 hover:text-red-700 dark:text-red-300 dark:hover:bg-red-500/10 dark:hover:text-red-200"
                            onClick={async () => {
                              const ok = await confirmDialog({
                                title: 'Remove document?',
                                description: `"${document.name}" will be removed from inventory.`,
                                confirmLabel: 'Remove',
                                variant: 'danger',
                              });
                              if (ok) onDelete?.(document.hash);
                            }}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        ) : null}
                      </div>
                    </TableCell>
                  </TableRow>
                  {inlineScorecard && document.review.summary_scorecard ? (
                    <TableRow key={`${document.hash}-scorecard`} className="bg-muted/10">
                      <TableCell colSpan={8} className="min-w-0 whitespace-normal px-4 py-3">
                        <CompactSummaryScorecard scorecard={document.review.summary_scorecard} />
                      </TableCell>
                    </TableRow>
                  ) : null}
                </Fragment>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}

type WorkflowStateRecord = DocumentItem['workflow_state'];

const PIPELINE_STAGES: Array<{ key: keyof WorkflowStateRecord; short: string; label: string }> = [
  { key: 'ingest_state', short: 'IG', label: 'Ingest' },
  { key: 'ocr_state', short: 'OC', label: 'OCR' },
  { key: 'source_state', short: 'SR', label: 'Source' },
  { key: 'summary_state', short: 'SM', label: 'Summary' },
  { key: 'review_state', short: 'RV', label: 'Review' },
  { key: 'promotion_state', short: 'PR', label: 'Promotion' },
];

function toneClass(value: string): string {
  if (!value) return 'bg-stone-100 text-stone-700 border-stone-200 dark:bg-stone-500/15 dark:text-stone-300 dark:border-stone-500/30';
  if (value === 'ready' || value === 'approved' || value === 'promoted' || value === 'imported') {
    return 'bg-emerald-100 text-emerald-700 border-emerald-200 dark:bg-emerald-500/15 dark:text-emerald-300 dark:border-emerald-500/30';
  }
  if (value === 'failed' || value === 'rejected') {
    return 'bg-red-100 text-red-700 border-red-200 dark:bg-red-500/15 dark:text-red-300 dark:border-red-500/30';
  }
  if (value === 'running' || value === 'queued' || value === 'held') {
    return 'bg-amber-100 text-amber-700 border-amber-200 dark:bg-amber-500/15 dark:text-amber-300 dark:border-amber-500/30';
  }
  return 'bg-stone-100 text-stone-700 border-stone-200 dark:bg-stone-500/15 dark:text-stone-300 dark:border-stone-500/30';
}

/**
 * Active stage = the first non-terminal one we haven't completed yet.
 * Falls back to the last stage when everything is done.
 */
function findActiveStageIndex(state: WorkflowStateRecord): number {
  const TERMINAL_OK = new Set(['ready', 'approved', 'promoted', 'imported', 'not_needed']);
  for (let i = 0; i < PIPELINE_STAGES.length; i += 1) {
    const value = state[PIPELINE_STAGES[i].key];
    if (!TERMINAL_OK.has(value)) return i;
  }
  return PIPELINE_STAGES.length - 1;
}

function WorkflowPipeline({ state }: { state: WorkflowStateRecord }) {
  const activeIndex = findActiveStageIndex(state);
  const activeStage = PIPELINE_STAGES[activeIndex];
  const activeValue = state[activeStage.key];
  const titleText = PIPELINE_STAGES.map((s) => `${s.label}: ${state[s.key] || '—'}`).join('\n');

  return (
    <div className="flex flex-col gap-1.5" title={titleText}>
      <div className="flex h-1.5 items-center gap-0.5" role="img" aria-label={`Workflow progress, current stage ${activeStage.label}: ${activeValue}`}>
        {PIPELINE_STAGES.map((stage) => {
          const value = state[stage.key];
          return (
            <div
              key={stage.key}
              className={`h-full flex-1 rounded-sm border ${toneClass(value)}`}
            />
          );
        })}
      </div>
      <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
        <span className="font-semibold uppercase tracking-wider text-foreground/70">
          {activeStage.label}
        </span>
        <span className="text-muted-foreground/80">·</span>
        <span className="lowercase">{activeValue || '—'}</span>
      </div>
    </div>
  );
}

function DocumentDetailDialog({
  document,
  onOpenChange,
  onNavigateToWiki,
  onOpenRawSource,
}: {
  document: DocumentItem | null;
  onOpenChange: (open: boolean) => void;
  onNavigateToWiki?: (path: string) => void;
  onOpenRawSource?: (document: DocumentItem) => void;
}) {
  const relatedGroups: Array<{ key: 'summaries' | 'companies' | 'industries' | 'concepts'; label: string }> = [
    { key: 'summaries', label: 'Summaries' },
    { key: 'companies', label: 'Companies' },
    { key: 'industries', label: 'Industries' },
    { key: 'concepts', label: 'Concepts' },
  ];

  return (
    <Dialog open={!!document} onOpenChange={onOpenChange}>
      <DialogContent className="!left-0 !top-0 !translate-x-0 !translate-y-0 inset-0 h-dvh !w-screen !max-w-none overflow-y-auto overflow-x-hidden rounded-none border-0 p-4 sm:!left-1/2 sm:!top-1/2 sm:h-[calc(100dvh-1rem)] sm:max-h-none sm:!w-[min(98vw,88rem)] sm:!max-w-none sm:!-translate-x-1/2 sm:!-translate-y-1/2 sm:rounded-xl sm:border sm:p-6">
        <DialogHeader className="pr-8 text-left">
          <DialogTitle className="break-all leading-snug">{document?.name || 'Document detail'}</DialogTitle>
          <DialogDescription>
            Workflow state, review metadata, and source references for the selected document.
          </DialogDescription>
        </DialogHeader>

        {document ? (
          <div className="min-w-0 space-y-3 overflow-x-hidden sm:space-y-4">
            <div className="grid min-w-0 gap-3 md:grid-cols-2 sm:gap-4">
              <DetailBlock
                title="Identity"
                lines={[
                  `hash: ${document.hash}`,
                  `stem: ${document.stem}`,
                  `raw: ${document.raw_path || 'n/a'}`,
                  `source: ${document.source_path || 'n/a'}`,
                  `formal summary: ${document.summary_exists ? document.source_summary || 'n/a' : 'not promoted'}`,
                  `review summary: ${document.review_summary_exists ? document.review_summary_path || 'n/a' : 'missing'}`,
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
                  `score source: ${document.review.summary_score_source || 'n/a'}`,
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

            {document.raw_exists && document.raw_path ? (
              <div className="min-w-0 overflow-hidden rounded-xl border bg-muted/30 p-4">
                <div className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                  Raw Source
                </div>
                <button
                  type="button"
                  className="group flex w-full min-w-0 items-start gap-2 rounded-md px-2 py-2 text-left text-sm transition-colors hover:bg-muted/70"
                  onClick={() => onOpenRawSource?.(document)}
                  disabled={!onOpenRawSource}
                  title={document.raw_path}
                >
                  <ExternalLink className="mt-0.5 h-3.5 w-3.5 shrink-0 opacity-50 group-hover:opacity-100" />
                  <span className="min-w-0 flex-1 overflow-hidden">
                    <div className="font-medium">Open raw source file</div>
                    <div className="break-all text-xs text-muted-foreground">{document.raw_path}</div>
                  </span>
                </button>
              </div>
            ) : null}

            {document.review_summary_exists && document.review_summary_path ? (
              <div className="min-w-0 overflow-hidden rounded-xl border bg-muted/30 p-4">
                <div className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                  Review Summary
                </div>
                <button
                  type="button"
                  className="group flex w-full min-w-0 items-start gap-2 rounded-md px-2 py-2 text-left text-sm transition-colors hover:bg-muted/70"
                  onClick={() => onNavigateToWiki?.(document.review_summary_path!)}
                  disabled={!onNavigateToWiki}
                  title={document.review_summary_path}
                >
                  <ExternalLink className="mt-0.5 h-3.5 w-3.5 shrink-0 opacity-50 group-hover:opacity-100" />
                  <span className="min-w-0 flex-1 overflow-hidden">
                    <div className="font-medium">Open staged review summary</div>
                    <div className="break-all text-xs text-muted-foreground">{document.review_summary_path}</div>
                  </span>
                </button>
              </div>
            ) : null}

            {document.review.summary_scorecard ? (
              <SummaryScorecardPanel scorecard={document.review.summary_scorecard} />
            ) : null}

            <RelatedPagesPanel
              groups={relatedGroups.map((group) => ({
                ...group,
                pages: document.related_pages?.[group.key] ?? [],
              }))}
              onNavigate={onNavigateToWiki}
            />
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function RawSourcePreviewDialog({
  document,
  kbDir,
  onOpenChange,
}: {
  document: DocumentItem | null;
  kbDir: string;
  onOpenChange: (open: boolean) => void;
}) {
  const [isFullscreen, setIsFullscreen] = useState(false);
  const fileUrl = document?.raw_path ? rawFileUrl(kbDir, document.raw_path) : '';
  const previewKind = document?.raw_path ? rawPreviewKind(document.raw_path) : 'unsupported';

  return (
    <Dialog open={!!document} onOpenChange={onOpenChange}>
      <DialogContent
        className={isFullscreen
          ? "!left-0 !top-0 !translate-x-0 !translate-y-0 inset-0 flex h-dvh !w-screen !max-w-none flex-col overflow-hidden rounded-none border-0 p-0"
          : "!left-0 !top-0 !translate-x-0 !translate-y-0 inset-0 flex h-dvh !w-screen !max-w-none flex-col overflow-hidden rounded-none border-0 p-0 sm:!left-1/2 sm:!top-1/2 sm:h-[calc(100dvh-1rem)] sm:!w-[min(96vw,72rem)] sm:!max-w-none sm:!-translate-x-1/2 sm:!-translate-y-1/2 sm:rounded-xl sm:border"
        }
      >
        <DialogHeader className="shrink-0 border-b px-4 py-3 pr-12 text-left sm:px-5">
          <DialogTitle className="break-all text-base leading-snug">
            {document?.name || 'Raw source preview'}
          </DialogTitle>
          <DialogDescription className="break-all text-xs">
            {document?.raw_path || 'Source file'}
          </DialogDescription>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            className="absolute right-10 top-3"
            onClick={() => setIsFullscreen((value) => !value)}
            aria-label={isFullscreen ? 'Exit full screen' : 'Full screen'}
            title={isFullscreen ? 'Exit full screen' : 'Full screen'}
          >
            {isFullscreen ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
          </Button>
        </DialogHeader>

        <div className="min-h-0 flex-1 overflow-hidden bg-background">
          {document && fileUrl ? (
            <RawSourcePreviewBody kind={previewKind} url={fileUrl} path={document.raw_path} />
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function RawSourcePreviewBody({
  kind,
  url,
  path,
}: {
  kind: RawPreviewKind;
  url: string;
  path: string;
}) {
  const textPreviewQuery = useQuery({
    queryKey: ['raw-preview-text', url],
    queryFn: async () => {
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      return response.text();
    },
    enabled: kind === 'text',
    staleTime: 60_000,
  });

  if (kind === 'pdf') {
    return (
      <iframe
        src={url}
        title={path}
        className="h-full w-full border-0 bg-muted"
      />
    );
  }

  if (kind === 'image') {
    return (
      <div className="flex h-full w-full items-center justify-center overflow-auto bg-muted/30 p-3">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={url} alt={path} className="max-h-full max-w-full object-contain" />
      </div>
    );
  }

  if (kind === 'text') {
    if (textPreviewQuery.isPending) {
      return (
        <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          Loading preview
        </div>
      );
    }

    if (textPreviewQuery.isError) {
      return (
        <UnsupportedRawPreview
          title="Preview failed"
          description={`Could not load this source file: ${errorMessage(textPreviewQuery.error) ?? 'unknown error'}`}
        />
      );
    }

    return (
      <pre className="h-full overflow-auto whitespace-pre-wrap break-words p-4 font-mono text-xs leading-relaxed sm:p-5 sm:text-sm">
        {textPreviewQuery.data || ' '}
      </pre>
    );
  }

  return (
    <UnsupportedRawPreview
      title="Preview unavailable"
      description="This file type cannot be previewed in the browser yet."
    />
  );
}

function UnsupportedRawPreview({ title, description }: { title: string; description: string }) {
  return (
    <div className="flex h-full items-center justify-center p-6 text-center">
      <div className="max-w-sm">
        <AlertCircle className="mx-auto mb-3 h-8 w-8 text-muted-foreground" />
        <div className="text-sm font-medium">{title}</div>
        <div className="mt-1 text-sm text-muted-foreground">{description}</div>
      </div>
    </div>
  );
}

function RelatedPagesPanel({
  groups,
  onNavigate,
}: {
  groups: Array<{ key: string; label: string; pages: RelatedPageEntry[] }>;
  onNavigate?: (path: string) => void;
}) {
  const total = groups.reduce((sum, group) => sum + group.pages.length, 0);
  if (total === 0) {
    return (
      <div className="rounded-xl border bg-muted/30 p-4 text-sm text-muted-foreground">
        <div className="mb-1 text-xs font-semibold uppercase tracking-[0.16em]">Related Wiki Pages</div>
        No generated pages reference this document yet.
      </div>
    );
  }
  return (
    <div className="min-w-0 overflow-hidden rounded-xl border bg-muted/30 p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
          Related Wiki Pages
        </div>
        <div className="text-xs text-muted-foreground">{total} page(s)</div>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        {groups.map((group) =>
          group.pages.length === 0 ? null : (
            <div key={group.key} className="space-y-1">
              <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                <span>{group.label}</span>
                <span className="text-[10px] rounded-full bg-muted px-1.5 py-0.5">{group.pages.length}</span>
              </div>
              <ul className="space-y-0.5">
                {group.pages.map((entry) => (
                  <li key={entry.path}>
                    <button
                      type="button"
                      className="group flex w-full min-w-0 items-start gap-1.5 rounded-md px-2 py-1 text-left text-xs transition-colors hover:bg-muted/70"
                      onClick={() => onNavigate?.(entry.path)}
                      disabled={!onNavigate}
                      title={entry.path}
                    >
                      <ExternalLink className="h-3 w-3 mt-0.5 shrink-0 opacity-50 group-hover:opacity-100" />
                      <span className="flex-1 min-w-0">
                        <div className="break-all font-medium">{entry.title || entry.page}</div>
                        <div className="break-all text-[10px] text-muted-foreground">{entry.path}</div>
                      </span>
                      {entry.shared && (
                        <span className="text-[9px] uppercase tracking-wider text-amber-600 dark:text-amber-400 shrink-0">
                          shared
                        </span>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ),
        )}
      </div>
    </div>
  );
}

function DetailBlock({ title, lines }: { title: string; lines: string[] }) {
  return (
    <div className="min-w-0 overflow-hidden rounded-xl border bg-muted/30 p-4">
      <div className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
        {title}
      </div>
      <div className="space-y-2 text-sm">
        {lines.map((line) => (
          <div key={line} className="min-w-0 break-all">
            {line}
          </div>
        ))}
      </div>
    </div>
  );
}

function SummaryScorecardPanel({ scorecard }: { scorecard: NonNullable<DocumentItem['review']['summary_scorecard']> }) {
  const dimensions = Object.entries(scorecard.dimensions);
  return (
    <div className="min-w-0 overflow-hidden rounded-xl border bg-muted/30 p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
            Summary Scorecard
          </div>
          <div className="mt-1 text-sm text-muted-foreground">{scorecard.method || 'scoring rubric'}</div>
        </div>
        <div className="text-right">
          <div className="text-2xl font-semibold">{scorecard.total_score ?? 'n/a'}</div>
          <div className="text-xs text-muted-foreground">/ 100</div>
        </div>
      </div>
      {scorecard.overall_assessment ? (
        <div className="mb-3 text-sm text-muted-foreground">{scorecard.overall_assessment}</div>
      ) : null}
      <div className="grid gap-2 md:grid-cols-2">
        {dimensions.map(([key, dimension]) => (
          <div key={key} className="rounded-lg border bg-background/70 p-3 dark:bg-background/20">
            <div className="flex items-center justify-between gap-2">
              <div className="text-sm font-medium">{dimension.label}</div>
              <div className="text-xs text-muted-foreground">
                {dimension.score ?? 'n/a'} / {dimension.max ?? 'n/a'}
              </div>
            </div>
            {dimension.reason ? (
              <div className="mt-1 break-words text-xs text-muted-foreground">{dimension.reason}</div>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

function CompactSummaryScorecard({ scorecard }: { scorecard: NonNullable<DocumentItem['review']['summary_scorecard']> }) {
  const dimensions = Object.entries(scorecard.dimensions);
  return (
    <div className="rounded-xl border bg-background/70 p-4 dark:bg-background/20">
      <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
            Auto Scorecard
          </div>
          <div className="mt-1 text-sm text-muted-foreground">
            {scorecard.overall_assessment || scorecard.method || 'Scored during summary generation.'}
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className="text-xl font-semibold">{scorecard.total_score ?? 'n/a'}</div>
          <div className="text-[11px] text-muted-foreground">/ 100</div>
        </div>
      </div>
      <div className="mt-3 grid gap-2 lg:grid-cols-3">
        {dimensions.map(([key, dimension]) => (
          <div key={key} className="rounded-lg border bg-muted/30 p-3">
            <div className="flex items-center justify-between gap-2">
              <div className="text-xs font-medium">{dimension.label}</div>
              <div className="text-[11px] text-muted-foreground">
                {dimension.score ?? 'n/a'} / {dimension.max ?? 'n/a'}
              </div>
            </div>
            {dimension.reason ? (
              <div className="mt-1 break-words text-[11px] text-muted-foreground">{dimension.reason}</div>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

function parseOptionalScore(value: string): number | null {
  const text = value.trim();
  if (!text) return null;
  const parsed = Number(text);
  if (!Number.isFinite(parsed)) return null;
  return parsed;
}

function matchesScoreFilters(
  document: DocumentItem,
  {
    scoreFilter,
    minScore,
    maxScore,
  }: {
    scoreFilter: string;
    minScore: number | null;
    maxScore: number | null;
  },
): boolean {
  const score = document.review.summary_score;
  if (scoreFilter === 'scored' && score === null) return false;
  if (scoreFilter === 'unscored' && score !== null) return false;
  if (scoreFilter === 'high' && (score === null || score < 85)) return false;
  if (scoreFilter === 'strong' && (score === null || score < 70 || score > 84)) return false;
  if (scoreFilter === 'attention' && (score === null || score >= 70)) return false;
  if (minScore !== null && (score === null || score < minScore)) return false;
  if (maxScore !== null && (score === null || score > maxScore)) return false;
  return true;
}

function matchesInventoryFilters(document: DocumentItem, statusFilters: string[], importDate: string): boolean {
  if (importDate && document.ingested_date !== importDate) return false;
  if (!statusFilters.length) return true;
  return statusFilters.some((filter) => matchesInventoryStatusFilter(document, filter));
}

function matchesInventoryStatusFilter(document: DocumentItem, filter: string): boolean {
  if (filter === 'new') {
    const states = Object.values(document.workflow_state);
    return (
      !states.includes('failed') &&
      document.workflow_state.ingest_state === 'imported' &&
      document.workflow_state.summary_state === 'not_started' &&
      document.workflow_state.review_state === 'unreviewed' &&
      document.workflow_state.promotion_state === 'not_selected'
    );
  }
  if (filter === 'ready_to_summarize') {
    return (
      document.workflow_state.source_state === 'ready' &&
      ['not_started', 'failed'].includes(document.workflow_state.summary_state)
    );
  }
  if (filter === 'needs_summary') {
    return ['not_started', 'failed'].includes(document.workflow_state.summary_state);
  }
  if (filter === 'has_summary_page') {
    return (document.related_pages?.summaries?.length ?? 0) > 0 || document.summary_exists;
  }
  if (filter === 'has_company_page') {
    return (document.related_pages?.companies?.length ?? 0) > 0;
  }
  if (filter === 'has_industry_page') {
    return (document.related_pages?.industries?.length ?? 0) > 0;
  }
  if (filter === 'has_concept_page') {
    return (document.related_pages?.concepts?.length ?? 0) > 0;
  }
  if (filter === 'no_related_pages') {
    return document.related_count === 0;
  }
  if (filter === 'failed') {
    return (
      Object.values(document.workflow_state).includes('failed') ||
      !!document.execution.last_error.trim()
    );
  }
  return true;
}

function canRetryImport(document: DocumentItem): boolean {
  return (
    !!document.raw_path &&
    (document.workflow_state.source_state === 'failed' || document.workflow_state.ocr_state === 'failed')
  );
}

function rawPreviewKind(path: string): RawPreviewKind {
  const cleanPath = path.split(/[?#]/, 1)[0]?.toLowerCase() ?? '';
  const extension = cleanPath.includes('.') ? cleanPath.slice(cleanPath.lastIndexOf('.') + 1) : '';
  if (extension === 'pdf') return 'pdf';
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg'].includes(extension)) return 'image';
  if ([
    'txt',
    'md',
    'markdown',
    'csv',
    'tsv',
    'json',
    'jsonl',
    'yaml',
    'yml',
    'xml',
    'html',
    'htm',
    'log',
    'py',
    'js',
    'jsx',
    'ts',
    'tsx',
    'css',
    'scss',
    'toml',
    'ini',
    'cfg',
  ].includes(extension)) {
    return 'text';
  }
  return 'unsupported';
}

function errorMessage(error: unknown): string | undefined {
  if (!error) return undefined;
  if (error instanceof Error) return error.message;
  if (typeof error === 'string') return error;
  if (typeof error === 'object' && error !== null) {
    const maybe = error as { message?: unknown };
    if (typeof maybe.message === 'string') return maybe.message;
  }
  return undefined;
}

function SortableHeader({
  label,
  sortKey,
  current,
  dir,
  onSort,
}: {
  label: string;
  sortKey: SortKey;
  current: SortKey | null;
  dir: SortDir;
  onSort?: (key: SortKey) => void;
}) {
  if (!onSort) return <span>{label}</span>;
  const active = current === sortKey;
  const Icon = active ? (dir === 'asc' ? ArrowUp : ArrowDown) : ChevronsUpDown;
  return (
    <button
      type="button"
      onClick={() => onSort(sortKey)}
      className="group inline-flex items-center gap-1 text-left font-medium text-foreground transition-colors hover:text-primary focus-visible:outline-none focus-visible:text-primary"
      aria-label={`Sort by ${label}${active ? ` (${dir === 'asc' ? 'ascending' : 'descending'})` : ''}`}
    >
      {label}
      <Icon
        className={`h-3 w-3 ${active ? 'text-primary opacity-100' : 'opacity-40 group-hover:opacity-70'}`}
      />
    </button>
  );
}
