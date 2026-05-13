'use client';

import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertCircle,
  CheckCircle2,
  CheckSquare,
  ClipboardList,
  FileText,
  Loader2,
  Play,
  RefreshCcw,
  Square,
  Wand2,
} from 'lucide-react';
import {
  applyLintFixes,
  generateLintFixPlan,
  getDocuments,
  getJob,
  getWikiFile,
  LintApplyResult,
  LintFixCandidate,
  LintFixPlan,
  runLint,
} from '@/lib/api';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';

type JobPurpose = 'lint' | 'plan' | 'apply';

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

  const candidates = fixPlan?.candidates ?? [];
  const selectableCandidates = candidates.filter(isSelectable);
  const approvedCount = selectableCandidates.filter((item) => selectedFixes[candidateKey(item)]).length;

  const isBusy = !!activeJobId;
  const busyLabel =
    jobPurpose === 'lint' ? 'Running lint…' : jobPurpose === 'plan' ? 'Generating plan…' : jobPurpose === 'apply' ? 'Applying fixes…' : '';

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
                {fixPlan ? 'Fix Plan' : 'Report Preview'}
              </div>
              {fixPlan && selectableCandidates.length > 0 ? (
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
              {fixPlan ? (
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
                <pre className="p-4 text-xs whitespace-pre-wrap break-words font-mono leading-relaxed">
                  {reportFile.content}
                </pre>
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
