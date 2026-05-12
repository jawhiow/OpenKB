'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertCircle,
  CheckCircle2,
  Clock3,
  Loader2,
  PauseCircle,
  RefreshCcw,
  RotateCcw,
  Square,
} from 'lucide-react';
import { getJobs, JobPayload, retryJob, stopJob } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';

type JobFilter = 'active' | 'attention' | 'done' | 'all';

const FILTERS: Array<{ label: string; value: JobFilter }> = [
  { label: 'Active', value: 'active' },
  { label: 'Needs attention', value: 'attention' },
  { label: 'Done', value: 'done' },
  { label: 'All', value: 'all' },
];

const ACTIVE_STATUSES = new Set(['running', 'stopping']);
const ATTENTION_STATUSES = new Set(['failed', 'stopped']);
const DONE_STATUSES = new Set(['succeeded']);
const RETRYABLE_STATUSES = new Set(['failed', 'stopped', 'succeeded']);

export function JobsTab({ kbDir }: { kbDir: string | null }) {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<JobFilter>('active');
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const knownActiveJobsRef = useRef<Set<string>>(new Set());

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ['jobs'],
    queryFn: getJobs,
    refetchInterval: (query) => {
      const jobs = query.state.data?.jobs ?? [];
      return jobs.some((job) => isActiveJob(job)) ? 1500 : false;
    },
  });

  const jobs = useMemo(() => data?.jobs ?? [], [data?.jobs]);
  const filteredJobs = useMemo(() => jobs.filter((job) => matchesFilter(job, filter)), [filter, jobs]);
  const selectedJob = useMemo(
    () => jobs.find((job) => job.id === selectedJobId) ?? filteredJobs[0] ?? jobs[0] ?? null,
    [filteredJobs, jobs, selectedJobId],
  );

  useEffect(() => {
    for (const job of jobs) {
      if (isActiveJob(job)) {
        knownActiveJobsRef.current.add(job.id);
        continue;
      }
      if (!isTerminalJob(job) || !knownActiveJobsRef.current.has(job.id)) continue;
      knownActiveJobsRef.current.delete(job.id);
      if (kbDir) {
        queryClient.invalidateQueries({ queryKey: ['documents', kbDir] });
        queryClient.invalidateQueries({ queryKey: ['kbStats', kbDir] });
        queryClient.invalidateQueries({ queryKey: ['wikiTree', kbDir] });
        queryClient.invalidateQueries({ queryKey: ['wikiFile', kbDir] });
      }
    }
  }, [jobs, kbDir, queryClient]);

  const stopMutation = useMutation({
    mutationFn: stopJob,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
    },
  });

  const retryMutation = useMutation({
    mutationFn: retryJob,
    onSuccess: (data) => {
      const retriedId = data?.job?.id;
      if (retriedId) {
        setSelectedJobId(retriedId);
      }
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
    },
  });

  const counts = useMemo(
    () => ({
      active: jobs.filter(isActiveJob).length,
      attention: jobs.filter((job) => ATTENTION_STATUSES.has(job.status)).length,
      done: jobs.filter((job) => DONE_STATUSES.has(job.status)).length,
      all: jobs.length,
    }),
    [jobs],
  );

  return (
    <div className="grid h-full min-h-0 grid-cols-[minmax(320px,0.9fr)_minmax(420px,1.1fr)] gap-6">
      <Card className="flex min-h-0 flex-col overflow-hidden">
        <CardHeader className="shrink-0">
          <div className="flex items-start justify-between gap-4">
            <div>
              <CardTitle>Jobs</CardTitle>
              <CardDescription>
                Background import, summarization, review, promotion, and maintenance work.
              </CardDescription>
            </div>
            {isFetching && !isLoading && <Loader2 className="mt-1 h-4 w-4 animate-spin text-muted-foreground" />}
          </div>
          <div className="flex flex-wrap gap-2 pt-2">
            {FILTERS.map((item) => (
              <Button
                key={item.value}
                type="button"
                size="sm"
                variant={filter === item.value ? 'default' : 'outline'}
                onClick={() => setFilter(item.value)}
              >
                {item.label}
                <span className="ml-1 text-xs opacity-70">{counts[item.value]}</span>
              </Button>
            ))}
          </div>
        </CardHeader>
        <CardContent className="min-h-0 flex-1 overflow-y-auto">
          {isLoading ? (
            <div className="flex h-full items-center justify-center text-muted-foreground">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Loading jobs...
            </div>
          ) : filteredJobs.length === 0 ? (
            <div className="flex h-full items-center justify-center rounded-lg border border-dashed text-sm text-muted-foreground">
              No jobs in this view.
            </div>
          ) : (
            <div className="space-y-3">
              {filteredJobs.map((job) => (
                <button
                  key={job.id}
                  type="button"
                  onClick={() => setSelectedJobId(job.id)}
                  className={`w-full rounded-lg border p-3 text-left transition-colors hover:bg-muted/60 ${
                    selectedJob?.id === job.id ? 'border-primary bg-muted/70' : 'border-border bg-background'
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <StatusIcon job={job} />
                        <span className="truncate font-medium">{formatJobType(job.type)}</span>
                      </div>
                      <p className="mt-1 truncate text-xs text-muted-foreground" title={job.message || job.error || ''}>
                        {job.message || job.error || 'No status message'}
                      </p>
                    </div>
                    <StatusBadge status={job.status} />
                  </div>
                  <JobProgress job={job} />
                </button>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Card className="flex min-h-0 flex-col overflow-hidden">
        <CardHeader className="shrink-0">
          {selectedJob ? (
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <CardTitle className="truncate">{formatJobType(selectedJob.type)}</CardTitle>
                <CardDescription className="mt-1 truncate font-mono text-xs">{selectedJob.id}</CardDescription>
              </div>
              <div className="flex shrink-0 gap-2">
                {isActiveJob(selectedJob) && (
                  <Button
                    type="button"
                    variant="destructive"
                    size="sm"
                    disabled={stopMutation.isPending || selectedJob.status === 'stopping'}
                    onClick={() => stopMutation.mutate(selectedJob.id)}
                  >
                    {stopMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Square className="h-3.5 w-3.5" />}
                    Stop
                  </Button>
                )}
                {RETRYABLE_STATUSES.has(selectedJob.status) && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={retryMutation.isPending}
                    onClick={() => retryMutation.mutate(selectedJob.id)}
                  >
                    {retryMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
                    Retry
                  </Button>
                )}
              </div>
            </div>
          ) : (
            <>
              <CardTitle>Job Details</CardTitle>
              <CardDescription>Select a job to inspect status, timing, and logs.</CardDescription>
            </>
          )}
        </CardHeader>
        <CardContent className="min-h-0 flex-1 overflow-y-auto">
          {selectedJob ? (
            <div className="space-y-5">
              <section className="rounded-lg border bg-muted/30 p-4">
                <div className="grid grid-cols-2 gap-4 text-sm">
                  <DetailItem label="Status" value={selectedJob.status} />
                  <DetailItem label="Progress" value={formatProgress(selectedJob)} />
                  <DetailItem label="Created" value={formatDate(selectedJob.created_at)} />
                  <DetailItem label="Updated" value={formatDate(selectedJob.updated_at)} />
                  <DetailItem label="Stop requested" value={selectedJob.stop_requested ? 'Yes' : 'No'} />
                  <DetailItem label="Retry of" value={selectedJob.retry_of || 'None'} />
                </div>
                <div className="mt-4">
                  <JobProgress job={selectedJob} />
                </div>
              </section>

              {(selectedJob.message || selectedJob.error) && (
                <section className="rounded-lg border p-4">
                  <h3 className="mb-2 text-sm font-medium">Current Message</h3>
                  <p className="whitespace-pre-wrap text-sm text-muted-foreground">
                    {selectedJob.error || selectedJob.message}
                  </p>
                </section>
              )}

              <section>
                <div className="mb-2 flex items-center justify-between">
                  <h3 className="text-sm font-medium">Logs</h3>
                  <span className="text-xs text-muted-foreground">{selectedJob.logs?.length ?? 0} entries</span>
                </div>
                {selectedJob.logs?.length ? (
                  <div className="space-y-2 rounded-lg border bg-zinc-950 p-3 font-mono text-xs text-zinc-100">
                    {selectedJob.logs.map((log, index) => (
                      <div key={`${log.time}-${index}`} className="grid grid-cols-[150px_72px_1fr] gap-3">
                        <span className="text-zinc-500">{formatLogTime(log.time)}</span>
                        <span className={logLevelClass(log.level)}>{log.level || 'info'}</span>
                        <span className="whitespace-pre-wrap break-words">{log.message}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
                    No logs recorded for this job.
                  </div>
                )}
              </section>
            </div>
          ) : (
            <div className="flex h-full items-center justify-center rounded-lg border border-dashed text-sm text-muted-foreground">
              No jobs have been created yet.
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function matchesFilter(job: JobPayload, filter: JobFilter): boolean {
  if (filter === 'active') return isActiveJob(job);
  if (filter === 'attention') return ATTENTION_STATUSES.has(job.status);
  if (filter === 'done') return DONE_STATUSES.has(job.status);
  return true;
}

function isActiveJob(job: JobPayload): boolean {
  return ACTIVE_STATUSES.has(job.status);
}

function isTerminalJob(job: JobPayload): boolean {
  return ATTENTION_STATUSES.has(job.status) || DONE_STATUSES.has(job.status);
}

function JobProgress({ job }: { job: JobPayload }) {
  const current = job.progress?.current ?? 0;
  const total = job.progress?.total ?? 0;
  const value = total > 0 ? Math.min(100, Math.round((current / total) * 100)) : isActiveJob(job) ? null : 0;

  return (
    <div className="mt-3">
      <div className="mb-1 flex justify-between text-xs text-muted-foreground">
        <span>{formatProgress(job)}</span>
        {typeof value === 'number' && total > 0 && <span>{value}%</span>}
      </div>
      <Progress value={value} className="h-1.5" />
    </div>
  );
}

function StatusIcon({ job }: { job: JobPayload }) {
  if (job.status === 'succeeded') return <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />;
  if (job.status === 'failed') return <AlertCircle className="h-4 w-4 shrink-0 text-destructive" />;
  if (job.status === 'stopped') return <PauseCircle className="h-4 w-4 shrink-0 text-amber-600" />;
  if (job.status === 'stopping') return <RefreshCcw className="h-4 w-4 shrink-0 animate-spin text-amber-600" />;
  if (job.status === 'running') return <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary" />;
  return <Clock3 className="h-4 w-4 shrink-0 text-muted-foreground" />;
}

function StatusBadge({ status }: { status: string }) {
  const className =
    status === 'succeeded'
      ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
      : status === 'failed'
        ? 'border-red-200 bg-red-50 text-red-700'
        : status === 'stopped' || status === 'stopping'
          ? 'border-amber-200 bg-amber-50 text-amber-700'
          : 'border-blue-200 bg-blue-50 text-blue-700';

  return (
    <span className={`rounded-full border px-2 py-0.5 text-xs font-medium capitalize ${className}`}>
      {status || 'unknown'}
    </span>
  );
}

function DetailItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-1 truncate text-sm" title={value}>
        {value}
      </div>
    </div>
  );
}

function formatJobType(type: string): string {
  return type
    .split('_')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ') || 'Job';
}

function formatProgress(job: JobPayload): string {
  const current = job.progress?.current ?? 0;
  const total = job.progress?.total ?? 0;
  if (total <= 0) return isActiveJob(job) ? 'Working' : 'No progress';
  return `${current} / ${total}`;
}

function formatDate(value?: string): string {
  if (!value) return 'Unknown';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function formatLogTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString();
}

function logLevelClass(level: string): string {
  if (level === 'error') return 'text-red-300';
  if (level === 'warning') return 'text-amber-300';
  return 'text-sky-300';
}
