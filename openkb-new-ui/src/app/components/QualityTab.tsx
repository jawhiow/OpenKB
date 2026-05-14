'use client';

import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  AlertCircle,
  CheckCircle2,
  CheckSquare,
  ClipboardList,
  FileText,
  GitMerge,
  Heading1,
  Loader2,
  Play,
  RefreshCcw,
  Sparkles,
  Square,
  Stethoscope,
  Wand2,
} from 'lucide-react';
import {
  applyConceptMerges,
  applyH1Fix,
  applyH1Rename,
  applyLintFixes,
  CompactResult,
  ConceptMergeApplyResult,
  ConceptMergeProposal,
  ConceptMergeProposalResult,
  generateLintFixPlan,
  getDocuments,
  getJob,
  getWikiFile,
  H1FixResult,
  H1RenameApplyResult,
  H1RenameSuggestion,
  H1RenameSuggestResult,
  LintApplyResult,
  LintFixCandidate,
  LintFixPlan,
  proposeConceptMerges,
  runCompact,
  runLint,
  suggestH1Rename,
} from '@/lib/api';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';

type JobPurpose =
  | 'lint'
  | 'plan'
  | 'apply'
  | 'compact'
  | 'scan-dupes'
  | 'merge-apply'
  | 'h1-fix'
  | 'h1-rename-suggest'
  | 'h1-rename-apply';

const TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'stopped']);

function candidateKey(item: LintFixCandidate): string {
  return String(item.id || item.path || item.name || '');
}

function isAutoFix(item: LintFixCandidate): boolean {
  const action = String(item.action || 'create');
  return action === 'create' && item.auto_applicable !== false;
}

function isManualReview(item: LintFixCandidate): boolean {
  return item.action === 'manual-review';
}

function isSelectable(item: LintFixCandidate): boolean {
  return isAutoFix(item) || isManualReview(item);
}

export function QualityTab({
  kbDir,
  onJobStarted,
}: {
  kbDir: string | null;
  onJobStarted?: (jobId: string) => void;
}) {
  const queryClient = useQueryClient();
  const [selectedReport, setSelectedReport] = useState<string | null>(null);
  const [fixPlan, setFixPlan] = useState<LintFixPlan | null>(null);
  const [selectedFixes, setSelectedFixes] = useState<Record<string, boolean>>({});
  const [applyResult, setApplyResult] = useState<LintApplyResult | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [jobPurpose, setJobPurpose] = useState<JobPurpose | null>(null);
  const [errorMessage, setErrorMessage] = useState<string>('');
  // Hygiene workflow state (concept dedupe / H1 / compact)
  const [duplicateClusters, setDuplicateClusters] = useState<ConceptMergeProposal[]>([]);
  const [selectedClusters, setSelectedClusters] = useState<Record<string, boolean>>({});
  const [hygieneNotice, setHygieneNotice] = useState<string>('');
  // AI-assisted H1 rename state
  const [renameSuggestions, setRenameSuggestions] = useState<H1RenameSuggestion[]>([]);
  const [renameDrafts, setRenameDrafts] = useState<Record<string, H1RenameSuggestion>>({});
  const [renameSelected, setRenameSelected] = useState<Record<string, boolean>>({});
  const [renameMeta, setRenameMeta] = useState<{ model: string; threshold: number } | null>(null);

  // Reports list from documents API
  const { data: documentsData, isLoading: isLoadingDocuments, refetch: refetchDocuments } = useQuery({
    queryKey: ['documents', kbDir, 'reports'],
    queryFn: () => getDocuments(kbDir!, {}),
    enabled: !!kbDir,
  });

  const reports = useMemo<string[]>(() => documentsData?.reports ?? [], [documentsData?.reports]);

  // Auto-select the newest report when list changes
  useEffect(() => {
    if (!reports.length) {
      setSelectedReport(null);
      return;
    }
    if (selectedReport && reports.some((name) => `reports/${name}` === selectedReport)) {
      return;
    }
    setSelectedReport(`reports/${reports[reports.length - 1]}`);
  }, [reports, selectedReport]);

  // Report preview content
  const { data: reportFile, isLoading: isLoadingReport } = useQuery({
    queryKey: ['wikiFile', kbDir, selectedReport],
    queryFn: () => getWikiFile(kbDir!, selectedReport!),
    enabled: !!kbDir && !!selectedReport,
  });

  // Active job polling
  const { data: activeJob } = useQuery({
    queryKey: ['job', activeJobId],
    queryFn: () => getJob(activeJobId!),
    enabled: !!activeJobId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status && TERMINAL_STATUSES.has(status)) return false;
      return 1500;
    },
  });

  // React to job completion
  useEffect(() => {
    if (!activeJob || !jobPurpose) return;
    const { status } = activeJob;
    if (!status || !TERMINAL_STATUSES.has(status)) return;

    if (status === 'succeeded') {
      if (jobPurpose === 'lint') {
        refetchDocuments();
        if (kbDir) {
          queryClient.invalidateQueries({ queryKey: ['kbStats', kbDir] });
        }
      } else if (jobPurpose === 'plan') {
        const plan = (activeJob.result || null) as LintFixPlan | null;
        setFixPlan(plan);
        setSelectedFixes({});
        setApplyResult(null);
      } else if (jobPurpose === 'apply') {
        const result = (activeJob.result || null) as LintApplyResult | null;
        setApplyResult(result);
        setSelectedFixes({});
        if (kbDir) {
          queryClient.invalidateQueries({ queryKey: ['documents', kbDir] });
        }
      } else if (jobPurpose === 'scan-dupes') {
        const result = (activeJob.result || null) as ConceptMergeProposalResult | null;
        const proposals = result?.proposals ?? [];
        setDuplicateClusters(proposals);
        // Default-select every cluster so user can quickly merge-all.
        const init: Record<string, boolean> = {};
        proposals.forEach((p) => {
          init[p.canonical] = true;
        });
        setSelectedClusters(init);
        setHygieneNotice(
          proposals.length === 0
            ? 'No duplicate concept clusters detected.'
            : `Found ${proposals.length} cluster(s), ${result?.total_duplicates ?? 0} duplicate page(s).`,
        );
      } else if (jobPurpose === 'merge-apply') {
        const result = (activeJob.result || null) as ConceptMergeApplyResult | null;
        setDuplicateClusters([]);
        setSelectedClusters({});
        setHygieneNotice(
          result
            ? `Merged ${result.clusters_merged} cluster(s); deleted ${result.files_deleted} file(s); rewrote refs in ${result.files_rewritten} file(s).`
            : 'Merge applied.',
        );
        if (kbDir) {
          queryClient.invalidateQueries({ queryKey: ['wikiTree', kbDir] });
          queryClient.invalidateQueries({ queryKey: ['documents', kbDir] });
          queryClient.invalidateQueries({ queryKey: ['kbStats', kbDir] });
        }
      } else if (jobPurpose === 'h1-fix') {
        const result = (activeJob.result || null) as H1FixResult | null;
        setHygieneNotice(
          result
            ? `Auto-fixed H1 in ${result.fixed_count} of ${result.scanned} flagged file(s).`
            : 'H1 auto-fix completed.',
        );
        if (kbDir) {
          queryClient.invalidateQueries({ queryKey: ['wikiTree', kbDir] });
        }
      } else if (jobPurpose === 'h1-rename-suggest') {
        const result = (activeJob.result || null) as H1RenameSuggestResult | null;
        const list = result?.suggestions ?? [];
        setRenameSuggestions(list);
        const drafts: Record<string, H1RenameSuggestion> = {};
        const sel: Record<string, boolean> = {};
        list.forEach((s) => {
          drafts[s.path] = s;
          sel[s.path] = s.auto_applicable;
        });
        setRenameDrafts(drafts);
        setRenameSelected(sel);
        setRenameMeta({
          model: result?.model ?? '',
          threshold: result?.confidence_threshold ?? 0.7,
        });
        setHygieneNotice(
          list.length === 0
            ? 'No concept pages need LLM-assisted rename.'
            : `LLM produced ${list.length} suggestion(s); ${result?.auto_applicable_count ?? 0} auto-applicable at conf≥${result?.confidence_threshold ?? 0.7}.`,
        );
      } else if (jobPurpose === 'h1-rename-apply') {
        const result = (activeJob.result || null) as H1RenameApplyResult | null;
        setRenameSuggestions([]);
        setRenameDrafts({});
        setRenameSelected({});
        setRenameMeta(null);
        if (result) {
          setHygieneNotice(
            `Rewrote H1 in ${result.h1_rewritten.length} file(s); ` +
              `renamed ${result.renamed.length} file(s); ` +
              `rewrote refs in ${result.files_rewritten} file(s); ` +
              `skipped ${result.skipped.length}; errors ${result.errors.length}.`,
          );
        } else {
          setHygieneNotice('H1 rename apply completed.');
        }
        if (kbDir) {
          queryClient.invalidateQueries({ queryKey: ['wikiTree', kbDir] });
          queryClient.invalidateQueries({ queryKey: ['documents', kbDir] });
        }
      } else if (jobPurpose === 'compact') {
        const result = (activeJob.result || null) as CompactResult | null;
        if (result?.report_path) {
          setSelectedReport(result.report_path);
          setHygieneNotice(
            `Compact report ready: ${result.report_path} (H1 issues: ${result.h1_issue_count}, duplicate clusters: ${result.cluster_count}).`,
          );
        } else {
          setHygieneNotice('Compact audit complete.');
        }
        refetchDocuments();
      }
    } else {
      setErrorMessage(activeJob.error || `${jobPurpose} job ${status}`);
    }
    setActiveJobId(null);
    setJobPurpose(null);
  }, [activeJob, jobPurpose, kbDir, queryClient, refetchDocuments]);

  const lintMutation = useMutation({
    mutationFn: () => runLint(kbDir!),
    onSuccess: ({ job }) => {
      setErrorMessage('');
      setActiveJobId(job.id);
      setJobPurpose('lint');
      onJobStarted?.(job.id);
    },
    onError: (error: Error) => setErrorMessage(error.message),
  });

  const planMutation = useMutation({
    mutationFn: () => generateLintFixPlan(kbDir!, selectedReport),
    onSuccess: ({ job }) => {
      setErrorMessage('');
      setActiveJobId(job.id);
      setJobPurpose('plan');
      onJobStarted?.(job.id);
    },
    onError: (error: Error) => setErrorMessage(error.message),
  });

  const applyMutation = useMutation({
    mutationFn: () => {
      const approved = (fixPlan?.candidates ?? [])
        .filter((item) => isSelectable(item) && selectedFixes[candidateKey(item)])
        .map((item) => ({ ...item, approved: true }));
      return applyLintFixes(kbDir!, approved);
    },
    onSuccess: ({ job }) => {
      setErrorMessage('');
      setActiveJobId(job.id);
      setJobPurpose('apply');
      onJobStarted?.(job.id);
    },
    onError: (error: Error) => setErrorMessage(error.message),
  });

  const compactMutation = useMutation({
    mutationFn: () => runCompact(kbDir!),
    onSuccess: ({ job }) => {
      setErrorMessage('');
      setHygieneNotice('');
      setActiveJobId(job.id);
      setJobPurpose('compact');
      onJobStarted?.(job.id);
    },
    onError: (error: Error) => setErrorMessage(error.message),
  });

  const scanDupesMutation = useMutation({
    mutationFn: () => proposeConceptMerges(kbDir!),
    onSuccess: ({ job }) => {
      setErrorMessage('');
      setHygieneNotice('');
      setActiveJobId(job.id);
      setJobPurpose('scan-dupes');
      onJobStarted?.(job.id);
    },
    onError: (error: Error) => setErrorMessage(error.message),
  });

  const mergeApplyMutation = useMutation({
    mutationFn: () => {
      const selected = duplicateClusters.filter((p) => selectedClusters[p.canonical]);
      return applyConceptMerges(kbDir!, selected);
    },
    onSuccess: ({ job }) => {
      setErrorMessage('');
      setHygieneNotice('');
      setActiveJobId(job.id);
      setJobPurpose('merge-apply');
      onJobStarted?.(job.id);
    },
    onError: (error: Error) => setErrorMessage(error.message),
  });

  const h1FixMutation = useMutation({
    mutationFn: () => applyH1Fix(kbDir!),
    onSuccess: ({ job }) => {
      setErrorMessage('');
      setHygieneNotice('');
      setActiveJobId(job.id);
      setJobPurpose('h1-fix');
      onJobStarted?.(job.id);
    },
    onError: (error: Error) => setErrorMessage(error.message),
  });

  const h1RenameSuggestMutation = useMutation({
    mutationFn: () => suggestH1Rename(kbDir!),
    onSuccess: ({ job }) => {
      setErrorMessage('');
      setHygieneNotice('');
      setRenameSuggestions([]);
      setRenameDrafts({});
      setRenameSelected({});
      setRenameMeta(null);
      setActiveJobId(job.id);
      setJobPurpose('h1-rename-suggest');
      onJobStarted?.(job.id);
    },
    onError: (error: Error) => setErrorMessage(error.message),
  });

  const h1RenameApplyMutation = useMutation({
    mutationFn: () => {
      const approved = renameSuggestions
        .map((s) => renameDrafts[s.path] || s)
        .filter((s) => renameSelected[s.path]);
      return applyH1Rename(kbDir!, approved);
    },
    onSuccess: ({ job }) => {
      setErrorMessage('');
      setHygieneNotice('');
      setActiveJobId(job.id);
      setJobPurpose('h1-rename-apply');
      onJobStarted?.(job.id);
    },
    onError: (error: Error) => setErrorMessage(error.message),
  });

  const candidates = fixPlan?.candidates ?? [];
  const selectableCandidates = candidates.filter(isSelectable);
  const approvedCount = selectableCandidates.filter((item) => selectedFixes[candidateKey(item)]).length;
  const selectedClusterCount = duplicateClusters.filter((p) => selectedClusters[p.canonical]).length;
  const totalDuplicateCount = duplicateClusters.reduce((acc, p) => acc + Math.max(p.merged.length - 1, 0), 0);
  const renameSelectedCount = renameSuggestions.filter((s) => renameSelected[s.path]).length;

  const isBusy = !!activeJobId;
  const busyLabel =
    jobPurpose === 'lint'
      ? 'Running lint…'
      : jobPurpose === 'plan'
        ? 'Generating plan…'
        : jobPurpose === 'apply'
          ? 'Applying fixes…'
          : jobPurpose === 'compact'
            ? 'Running compact audit…'
            : jobPurpose === 'scan-dupes'
              ? 'Scanning duplicates…'
              : jobPurpose === 'merge-apply'
                ? 'Merging concepts…'
                : jobPurpose === 'h1-fix'
                  ? 'Fixing H1…'
                  : jobPurpose === 'h1-rename-suggest'
                    ? 'Asking LLM to repair H1↔stem…'
                    : jobPurpose === 'h1-rename-apply'
                      ? 'Applying H1 rename…'
                      : '';

  const handleSelectAll = (value: boolean) => {
    const next: Record<string, boolean> = {};
    selectableCandidates.forEach((item) => {
      next[candidateKey(item)] = value;
    });
    setSelectedFixes(next);
  };

  if (!kbDir) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground text-sm">
        Select a knowledge base to run lint and review fix plans.
      </div>
    );
  }

  return (
    <Card className="h-full flex flex-col overflow-hidden py-0 gap-0 border-border/70 shadow-sm">
      {/* Action Bar */}
      <CardHeader className="shrink-0 border-b bg-muted/20 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary/10 text-primary">
              <ClipboardList className="h-4 w-4" />
            </div>
            <div>
              <CardTitle className="text-base">Quality Workflow</CardTitle>
              <CardDescription className="text-xs">
                {selectedReport ? selectedReport.replace(/^reports\//, '') : 'No report selected'}
              </CardDescription>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => lintMutation.mutate()}
              disabled={isBusy || lintMutation.isPending}
            >
              {jobPurpose === 'lint' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
              Run Lint
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => planMutation.mutate()}
              disabled={isBusy || !selectedReport || planMutation.isPending}
            >
              {jobPurpose === 'plan' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wand2 className="h-3.5 w-3.5" />}
              Generate Fix Plan
            </Button>
            <Button
              size="sm"
              onClick={() => applyMutation.mutate()}
              disabled={isBusy || approvedCount === 0 || applyMutation.isPending}
            >
              {jobPurpose === 'apply' ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <CheckCircle2 className="h-3.5 w-3.5" />
              )}
              Apply Approved ({approvedCount})
            </Button>
            <div className="mx-1 h-5 w-px bg-border" aria-hidden />
            <Button
              variant="outline"
              size="sm"
              onClick={() => compactMutation.mutate()}
              disabled={isBusy || compactMutation.isPending}
              title="One-shot KB hygiene: H1 audit + dedupe scan + structural lint"
            >
              {jobPurpose === 'compact' ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Stethoscope className="h-3.5 w-3.5" />
              )}
              KB Compact
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => scanDupesMutation.mutate()}
              disabled={isBusy || scanDupesMutation.isPending}
              title="Scan concepts/ for duplicate clusters (dry-run)"
            >
              {jobPurpose === 'scan-dupes' ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <GitMerge className="h-3.5 w-3.5" />
              )}
              Scan Duplicates
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                if (window.confirm('Apply safe H1 repairs across concepts/, companies/, industries/?')) {
                  h1FixMutation.mutate();
                }
              }}
              disabled={isBusy || h1FixMutation.isPending}
              title="Safe in-place H1 repairs (missing H1, prefix noise)"
            >
              {jobPurpose === 'h1-fix' ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Heading1 className="h-3.5 w-3.5" />
              )}
              Auto-fix H1
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => h1RenameSuggestMutation.mutate()}
              disabled={isBusy || h1RenameSuggestMutation.isPending}
              title="Use LLM to suggest H1/filename repairs for concepts/ (mismatch + english-slug)"
            >
              {jobPurpose === 'h1-rename-suggest' ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Sparkles className="h-3.5 w-3.5" />
              )}
              AI Suggest Rename
            </Button>
          </div>
        </div>

        {/* Stats Strip */}
        <div className="mt-4 grid grid-cols-2 sm:grid-cols-5 gap-2">
          <StatChip label="Reports" value={reports.length} />
          <StatChip label="Candidates" value={candidates.length} />
          <StatChip label="Approved" value={approvedCount} tone="primary" />
          <StatChip label="Created" value={applyResult?.created?.length ?? 0} tone="emerald" />
          <StatChip label="Review" value={applyResult?.reviewed?.length ?? 0} tone="amber" />
        </div>

        {busyLabel ? (
          <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" />
            {busyLabel}
          </div>
        ) : null}

        {hygieneNotice ? (
          <div className="mt-3 text-xs rounded-md border border-emerald-200/60 bg-emerald-50/40 dark:bg-emerald-500/5 dark:border-emerald-500/30 px-3 py-2 text-emerald-800 dark:text-emerald-200">
            {hygieneNotice}
          </div>
        ) : null}

        {errorMessage ? (
          <Alert variant="destructive" className="mt-3">
            <AlertCircle className="h-4 w-4" />
            <AlertTitle>Operation failed</AlertTitle>
            <AlertDescription>{errorMessage}</AlertDescription>
          </Alert>
        ) : null}
      </CardHeader>

      {/* Body */}
      <CardContent className="flex-1 overflow-hidden p-0 flex flex-col min-h-0">
        <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] flex-1 overflow-hidden min-h-0">
          {/* Reports list */}
          <div className="flex flex-col border-r min-h-0 overflow-hidden">
            <div className="flex items-center justify-between px-4 py-2 border-b bg-muted/10 shrink-0">
              <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Reports</div>
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={() => refetchDocuments()}
                title="Refresh"
                disabled={isLoadingDocuments}
              >
                <RefreshCcw className={isLoadingDocuments ? 'h-3 w-3 animate-spin' : 'h-3 w-3'} />
              </Button>
            </div>
            <ScrollArea className="flex-1 min-h-0 overflow-hidden">
              {isLoadingDocuments ? (
                <div className="p-6 flex justify-center">
                  <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                </div>
              ) : reports.length === 0 ? (
                <div className="p-6 text-center text-xs text-muted-foreground">
                  No reports yet. Run lint to generate one.
                </div>
              ) : (
                <div className="p-2 space-y-1">
                  {[...reports].reverse().map((name) => {
                    const path = `reports/${name}`;
                    const active = path === selectedReport;
                    return (
                      <Button
                        key={path}
                        variant={active ? 'secondary' : 'ghost'}
                        size="sm"
                        className="w-full justify-start font-normal h-8 px-2 text-left"
                        onClick={() => setSelectedReport(path)}
                        title={name}
                      >
                        <FileText className="h-3 w-3 mr-1.5 shrink-0 opacity-70" />
                        <span className="truncate text-xs">{name}</span>
                      </Button>
                    );
                  })}
                </div>
              )}
            </ScrollArea>
          </div>

          {/* Report preview + Fix plan */}
          <div className="flex flex-col min-h-0 overflow-hidden">
            <div className="flex items-center justify-between px-4 py-2 border-b bg-muted/10 shrink-0">
              <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                {renameSuggestions.length > 0
                  ? 'H1 Rename Suggestions'
                  : duplicateClusters.length > 0
                    ? 'Duplicate Clusters'
                    : fixPlan
                      ? 'Fix Plan'
                      : 'Report Preview'}
              </div>
              {fixPlan && selectableCandidates.length > 0 && duplicateClusters.length === 0 && renameSuggestions.length === 0 ? (
                <div className="flex gap-1">
                  <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => handleSelectAll(true)}>
                    <CheckSquare className="h-3 w-3 mr-1" />
                    All
                  </Button>
                  <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => handleSelectAll(false)}>
                    <Square className="h-3 w-3 mr-1" />
                    None
                  </Button>
                </div>
              ) : null}
            </div>

            <ScrollArea className="flex-1 min-h-0 overflow-hidden">
              {renameSuggestions.length > 0 ? (
                <H1RenameSuggestionPanel
                  suggestions={renameSuggestions}
                  drafts={renameDrafts}
                  selected={renameSelected}
                  meta={renameMeta}
                  onToggle={(path, value) =>
                    setRenameSelected((prev) => ({ ...prev, [path]: value }))
                  }
                  onSelectAll={(value) => {
                    const next: Record<string, boolean> = {};
                    renameSuggestions.forEach((s) => {
                      // manual / split can't be applied at all — never auto-tick
                      next[s.path] =
                        value &&
                        ((renameDrafts[s.path] || s).action === 'rewrite_h1' ||
                          (renameDrafts[s.path] || s).action === 'rename_file');
                    });
                    setRenameSelected(next);
                  }}
                  onDraftChange={(path, patch) =>
                    setRenameDrafts((prev) => ({
                      ...prev,
                      [path]: { ...(prev[path] || renameSuggestions.find((x) => x.path === path)!), ...patch },
                    }))
                  }
                  onApply={() => {
                    if (renameSelectedCount === 0) return;
                    if (
                      window.confirm(
                        `Apply ${renameSelectedCount} suggestion(s)? Files may be renamed and wikilinks rewritten.`,
                      )
                    ) {
                      h1RenameApplyMutation.mutate();
                    }
                  }}
                  onCancel={() => {
                    setRenameSuggestions([]);
                    setRenameDrafts({});
                    setRenameSelected({});
                    setRenameMeta(null);
                    setHygieneNotice('');
                  }}
                  selectedCount={renameSelectedCount}
                  disabled={isBusy}
                  busy={jobPurpose === 'h1-rename-apply'}
                />
              ) : duplicateClusters.length > 0 ? (
                <DuplicateClusterPanel
                  clusters={duplicateClusters}
                  selected={selectedClusters}
                  onToggle={(canonical, value) =>
                    setSelectedClusters((prev) => ({ ...prev, [canonical]: value }))
                  }
                  onSelectAll={(value) => {
                    const next: Record<string, boolean> = {};
                    duplicateClusters.forEach((p) => {
                      next[p.canonical] = value;
                    });
                    setSelectedClusters(next);
                  }}
                  onMerge={() => {
                    if (selectedClusterCount === 0) return;
                    const total = duplicateClusters
                      .filter((p) => selectedClusters[p.canonical])
                      .reduce((acc, p) => acc + Math.max(p.merged.length - 1, 0), 0);
                    if (
                      window.confirm(
                        `Merge ${selectedClusterCount} cluster(s)? This deletes ${total} duplicate page(s) and rewrites wikilinks.`,
                      )
                    ) {
                      mergeApplyMutation.mutate();
                    }
                  }}
                  onCancel={() => {
                    setDuplicateClusters([]);
                    setSelectedClusters({});
                    setHygieneNotice('');
                  }}
                  selectedCount={selectedClusterCount}
                  totalDuplicates={totalDuplicateCount}
                  disabled={isBusy}
                  busy={jobPurpose === 'merge-apply'}
                />
              ) : fixPlan ? (
                <FixPlanList
                  candidates={candidates}
                  selectedFixes={selectedFixes}
                  onToggle={(key, value) => setSelectedFixes((prev) => ({ ...prev, [key]: value }))}
                  applyResult={applyResult}
                />
              ) : isLoadingReport ? (
                <div className="p-6 flex justify-center">
                  <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                </div>
              ) : reportFile?.content ? (
                <div className="prose prose-slate max-w-none p-6 dark:prose-invert">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {reportFile.content}
                  </ReactMarkdown>
                </div>
              ) : selectedReport ? (
                <div className="p-6 text-sm text-muted-foreground italic text-center">
                  Empty report.
                </div>
              ) : (
                <div className="p-6 text-sm text-muted-foreground text-center">
                  Select a report to preview, then generate a fix plan.
                </div>
              )}
            </ScrollArea>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function DuplicateClusterPanel({
  clusters,
  selected,
  onToggle,
  onSelectAll,
  onMerge,
  onCancel,
  selectedCount,
  totalDuplicates,
  disabled,
  busy,
}: {
  clusters: ConceptMergeProposal[];
  selected: Record<string, boolean>;
  onToggle: (canonical: string, value: boolean) => void;
  onSelectAll: (value: boolean) => void;
  onMerge: () => void;
  onCancel: () => void;
  selectedCount: number;
  totalDuplicates: number;
  disabled: boolean;
  busy: boolean;
}) {
  return (
    <div className="flex flex-col">
      <div className="sticky top-0 z-10 flex flex-wrap items-center justify-between gap-2 border-b bg-background/95 backdrop-blur px-4 py-2.5">
        <div className="text-xs text-muted-foreground">
          {clusters.length} cluster(s), {totalDuplicates} duplicate page(s). Selected: {selectedCount}
        </div>
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => onSelectAll(true)} disabled={disabled}>
            <CheckSquare className="h-3 w-3 mr-1" />
            All
          </Button>
          <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => onSelectAll(false)} disabled={disabled}>
            <Square className="h-3 w-3 mr-1" />
            None
          </Button>
          <Button variant="outline" size="sm" className="h-7 px-2 text-xs" onClick={onCancel} disabled={disabled}>
            Close
          </Button>
          <Button
            size="sm"
            className="h-7 px-3 text-xs"
            onClick={onMerge}
            disabled={disabled || selectedCount === 0}
          >
            {busy ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : <GitMerge className="h-3 w-3 mr-1" />}
            Merge Selected ({selectedCount})
          </Button>
        </div>
      </div>
      <div className="divide-y">
        {clusters.map((cluster) => {
          const checked = !!selected[cluster.canonical];
          return (
            <div key={cluster.canonical} className={`p-4 ${checked ? '' : 'opacity-70'}`}>
              <div className="flex items-start gap-3">
                <input
                  type="checkbox"
                  className="mt-1 h-4 w-4 rounded border-input"
                  checked={checked}
                  disabled={disabled}
                  onChange={(event) => onToggle(cluster.canonical, event.target.checked)}
                />
                <div className="flex-1 min-w-0">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <strong className="text-sm truncate">{cluster.canonical}</strong>
                    <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-primary/10 text-primary">
                      canonical
                    </span>
                    <span className="text-[10px] text-muted-foreground">
                      will absorb {cluster.merged.length - 1} duplicate(s)
                    </span>
                  </div>
                  <ul className="mt-2 space-y-1">
                    {cluster.merged.slice(1).map((slug) => {
                      const sim = cluster.rationale[slug];
                      return (
                        <li key={slug} className="text-xs flex items-center gap-2">
                          <span className="text-muted-foreground">└─ merge:</span>
                          <code className="text-foreground/90 truncate">{slug}</code>
                          {typeof sim === 'number' ? (
                            <span className="text-[10px] text-muted-foreground tabular-nums">
                              sim={sim.toFixed(3)}
                            </span>
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>
                  {cluster.sources_union.length > 0 ? (
                    <div className="text-[11px] text-muted-foreground mt-2 truncate">
                      sources ({cluster.sources_union.length}): {cluster.sources_union.slice(0, 4).join(', ')}
                      {cluster.sources_union.length > 4 ? ` …+${cluster.sources_union.length - 4}` : ''}
                    </div>
                  ) : null}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function StatChip({
  label,
  value,
  tone = 'default',
}: {
  label: string;
  value: number;
  tone?: 'default' | 'primary' | 'emerald' | 'amber';
}) {
  const toneClass =
    tone === 'primary'
      ? 'bg-primary/10 text-primary'
      : tone === 'emerald'
        ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300'
        : tone === 'amber'
          ? 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300'
          : 'bg-muted text-foreground';
  return (
    <div className="rounded-md border bg-background/60 px-3 py-2 flex items-center justify-between gap-2">
      <span className="text-[11px] uppercase tracking-wider text-muted-foreground truncate">{label}</span>
      <span className={`text-sm font-semibold px-1.5 py-0.5 rounded ${toneClass} tabular-nums`}>{value}</span>
    </div>
  );
}

function FixPlanList({
  candidates,
  selectedFixes,
  onToggle,
  applyResult,
}: {
  candidates: LintFixCandidate[];
  selectedFixes: Record<string, boolean>;
  onToggle: (key: string, value: boolean) => void;
  applyResult: LintApplyResult | null;
}) {
  if (candidates.length === 0) {
    return (
      <div className="p-6 text-sm text-muted-foreground text-center">
        No fix candidates in the latest plan.
      </div>
    );
  }
  return (
    <div className="divide-y">
      {candidates.map((item) => {
        const key = candidateKey(item);
        const auto = isAutoFix(item);
        const review = isManualReview(item);
        const selectable = isSelectable(item);
        const checked = !!selectedFixes[key];
        const actionLabel = item.action || 'create';
        const badgeClass = auto
          ? 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300'
          : review
            ? 'bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-300'
            : 'bg-muted text-muted-foreground';
        return (
          <div key={key} className={`p-4 ${selectable ? '' : 'opacity-60'}`}>
            <div className="flex items-start gap-3">
              <input
                type="checkbox"
                className="mt-1 h-4 w-4 rounded border-input"
                checked={checked}
                disabled={!selectable}
                onChange={(event) => onToggle(key, event.target.checked)}
              />
              <div className="flex-1 min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <strong className="text-sm truncate">{item.title || item.name}</strong>
                  <span className={`text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded ${badgeClass}`}>
                    {actionLabel}
                  </span>
                  {item.status ? (
                    <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
                      {item.status}
                    </span>
                  ) : null}
                </div>
                <div className="text-xs text-muted-foreground truncate mt-0.5">
                  {item.path || `concepts/${item.name}.md`}
                </div>
                {item.source_section ? (
                  <div className="text-xs text-muted-foreground mt-1">Section: {item.source_section}</div>
                ) : null}
                {item.reason ? (
                  <p className="text-xs text-muted-foreground mt-2 leading-relaxed">{item.reason}</p>
                ) : null}
                {item.preview ? (
                  <details className="mt-2 group">
                    <summary className="text-xs text-muted-foreground cursor-pointer hover:text-foreground">
                      Planned content
                    </summary>
                    <pre className="mt-2 p-3 text-[11px] rounded bg-muted/50 whitespace-pre-wrap break-words font-mono leading-relaxed">
                      {item.preview}
                    </pre>
                  </details>
                ) : null}
              </div>
            </div>
          </div>
        );
      })}
      {applyResult ? <AppliedResultsBlock result={applyResult} /> : null}
    </div>
  );
}

function AppliedResultsBlock({ result }: { result: LintApplyResult }) {
  const { created, reviewed } = result;
  if (!created.length && !reviewed.length) return null;
  return (
    <div className="p-4 bg-emerald-50/30 dark:bg-emerald-500/5 border-t-2 border-emerald-200 dark:border-emerald-500/30 space-y-2">
      <div className="text-xs font-semibold uppercase tracking-wider text-emerald-700 dark:text-emerald-400">
        Apply Result
      </div>
      {created.map((item) => (
        <div key={`c-${item.path}`} className="text-xs flex gap-2">
          <CheckCircle2 className="h-3 w-3 mt-0.5 shrink-0 text-emerald-600 dark:text-emerald-400" />
          <div className="min-w-0">
            <span className="text-muted-foreground">Created draft — </span>
            <strong className="truncate">{item.title || item.name}</strong>
            <div className="text-muted-foreground truncate">{item.path}</div>
          </div>
        </div>
      ))}
      {reviewed.map((item) => (
        <div key={`r-${item.path}`} className="text-xs flex gap-2">
          <AlertCircle className="h-3 w-3 mt-0.5 shrink-0 text-amber-600 dark:text-amber-400" />
          <div className="min-w-0">
            <span className="text-muted-foreground">Approved review — </span>
            <strong className="truncate">{item.title || item.name}</strong>
            <div className="text-muted-foreground truncate">{item.path}</div>
            {item.reason ? <em className="text-muted-foreground">{item.reason}</em> : null}
          </div>
        </div>
      ))}
    </div>
  );
}

const ACTION_BADGE: Record<string, string> = {
  rewrite_h1: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300',
  rename_file: 'bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-300',
  split: 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300',
  manual: 'bg-muted text-muted-foreground',
};

function H1RenameSuggestionPanel({
  suggestions,
  drafts,
  selected,
  meta,
  onToggle,
  onSelectAll,
  onDraftChange,
  onApply,
  onCancel,
  selectedCount,
  disabled,
  busy,
}: {
  suggestions: H1RenameSuggestion[];
  drafts: Record<string, H1RenameSuggestion>;
  selected: Record<string, boolean>;
  meta: { model: string; threshold: number } | null;
  onToggle: (path: string, value: boolean) => void;
  onSelectAll: (value: boolean) => void;
  onDraftChange: (path: string, patch: Partial<H1RenameSuggestion>) => void;
  onApply: () => void;
  onCancel: () => void;
  selectedCount: number;
  disabled: boolean;
  busy: boolean;
}) {
  const executableCount = suggestions.filter((s) => {
    const draft = drafts[s.path] || s;
    return draft.action === 'rewrite_h1' || draft.action === 'rename_file';
  }).length;

  return (
    <div className="flex flex-col">
      <div className="sticky top-0 z-10 flex flex-wrap items-center justify-between gap-2 border-b bg-background/95 backdrop-blur px-4 py-2.5">
        <div className="text-xs text-muted-foreground">
          {suggestions.length} suggestion(s); {executableCount} executable. Selected: {selectedCount}
          {meta?.model ? <span className="ml-2 opacity-70">· model: {meta.model}</span> : null}
          {meta ? <span className="ml-2 opacity-70">· conf≥{meta.threshold}</span> : null}
        </div>
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => onSelectAll(true)} disabled={disabled}>
            <CheckSquare className="h-3 w-3 mr-1" />
            All Executable
          </Button>
          <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => onSelectAll(false)} disabled={disabled}>
            <Square className="h-3 w-3 mr-1" />
            None
          </Button>
          <Button variant="outline" size="sm" className="h-7 px-2 text-xs" onClick={onCancel} disabled={disabled}>
            Close
          </Button>
          <Button size="sm" className="h-7 px-3 text-xs" onClick={onApply} disabled={disabled || selectedCount === 0}>
            {busy ? <Loader2 className="h-3 w-3 mr-1 animate-spin" /> : <Sparkles className="h-3 w-3 mr-1" />}
            Apply Selected ({selectedCount})
          </Button>
        </div>
      </div>
      <div className="divide-y">
        {suggestions.map((original) => {
          const draft = drafts[original.path] || original;
          const checked = !!selected[original.path];
          const executable = draft.action === 'rewrite_h1' || draft.action === 'rename_file';
          return (
            <div key={original.path} className={`p-4 ${executable ? '' : 'opacity-70'}`}>
              <div className="flex items-start gap-3">
                <input
                  type="checkbox"
                  className="mt-1 h-4 w-4 rounded border-input"
                  checked={checked}
                  disabled={disabled || !executable}
                  onChange={(event) => onToggle(original.path, event.target.checked)}
                />
                <div className="flex-1 min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <code className="text-sm truncate font-mono">{draft.path}</code>
                    <span className={`text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded ${ACTION_BADGE[draft.action] || ACTION_BADGE.manual}`}>
                      {draft.action}
                    </span>
                    <span className="text-[10px] text-muted-foreground tabular-nums">
                      conf={draft.confidence.toFixed(2)}
                    </span>
                    {draft.auto_applicable ? (
                      <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-primary/10 text-primary">
                        auto
                      </span>
                    ) : null}
                  </div>

                  <div className="mt-2 grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
                    <div>
                      <div className="text-muted-foreground">Current H1</div>
                      <div className="font-medium truncate">{draft.h1 || <em className="text-muted-foreground">(missing)</em>}</div>
                    </div>
                    <div>
                      <div className="text-muted-foreground">Current stem</div>
                      <div className="font-medium truncate">{draft.stem}</div>
                    </div>
                  </div>

                  {draft.action === 'rewrite_h1' ? (
                    <div className="mt-2 text-xs">
                      <label className="text-muted-foreground block mb-1">Target H1 (editable)</label>
                      <input
                        type="text"
                        className="w-full rounded border border-input bg-background px-2 py-1 text-xs font-mono"
                        value={draft.target_h1}
                        disabled={disabled}
                        onChange={(event) => onDraftChange(original.path, { target_h1: event.target.value })}
                      />
                    </div>
                  ) : null}

                  {draft.action === 'rename_file' ? (
                    <div className="mt-2 text-xs">
                      <label className="text-muted-foreground block mb-1">Target stem (editable, file will become {`{stem}`}.md)</label>
                      <input
                        type="text"
                        className="w-full rounded border border-input bg-background px-2 py-1 text-xs font-mono"
                        value={draft.target_stem}
                        disabled={disabled}
                        onChange={(event) => onDraftChange(original.path, { target_stem: event.target.value })}
                      />
                    </div>
                  ) : null}

                  {draft.action === 'split' && draft.split_concepts?.length ? (
                    <div className="mt-2 text-xs">
                      <div className="text-muted-foreground mb-1">Suggested split (advisory, not executed):</div>
                      <ul className="space-y-1 ml-2">
                        {draft.split_concepts.map((c, idx) => (
                          <li key={idx} className="leading-relaxed">
                            <code className="text-foreground/90">{c.name}</code>
                            <span className="text-muted-foreground"> — {c.title}</span>
                            {c.summary ? <div className="text-muted-foreground mt-0.5">{c.summary}</div> : null}
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}

                  {draft.rationale ? (
                    <p className="text-xs text-muted-foreground mt-2 leading-relaxed">
                      <span className="font-semibold">Reason: </span>
                      {draft.rationale}
                    </p>
                  ) : null}

                  {draft.error ? (
                    <p className="text-xs text-red-600 dark:text-red-400 mt-1">{draft.error}</p>
                  ) : null}

                  {draft.brief ? (
                    <p className="text-[11px] text-muted-foreground mt-1 truncate">brief: {draft.brief}</p>
                  ) : null}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
