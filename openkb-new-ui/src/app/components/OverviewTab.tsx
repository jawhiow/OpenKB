'use client';

import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Activity,
  Building2,
  FileText,
  Files,
  Inbox,
  Landmark,
  Layers,
  Lightbulb,
  ShieldCheck,
  Server,
} from 'lucide-react';
import {
  DocumentItem,
  getDocuments,
  getKbStats,
  getModelPool,
  KbStatusResponse,
  ModelPoolData,
} from '@/lib/api';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';

type StatTone = 'blue' | 'amber' | 'emerald' | 'violet' | 'rose' | 'sky' | 'teal';

const toneStyles: Record<StatTone, { tile: string; chip: string }> = {
  blue: {
    tile: 'bg-blue-50/60 border-blue-100 dark:bg-blue-500/10 dark:border-blue-500/20',
    chip: 'bg-blue-100 text-blue-600 dark:bg-blue-500/20 dark:text-blue-300',
  },
  amber: {
    tile: 'bg-amber-50/60 border-amber-100 dark:bg-amber-500/10 dark:border-amber-500/20',
    chip: 'bg-amber-100 text-amber-600 dark:bg-amber-500/20 dark:text-amber-300',
  },
  emerald: {
    tile: 'bg-emerald-50/60 border-emerald-100 dark:bg-emerald-500/10 dark:border-emerald-500/20',
    chip: 'bg-emerald-100 text-emerald-600 dark:bg-emerald-500/20 dark:text-emerald-300',
  },
  violet: {
    tile: 'bg-violet-50/60 border-violet-100 dark:bg-violet-500/10 dark:border-violet-500/20',
    chip: 'bg-violet-100 text-violet-600 dark:bg-violet-500/20 dark:text-violet-300',
  },
  rose: {
    tile: 'bg-rose-50/60 border-rose-100 dark:bg-rose-500/10 dark:border-rose-500/20',
    chip: 'bg-rose-100 text-rose-600 dark:bg-rose-500/20 dark:text-rose-300',
  },
  sky: {
    tile: 'bg-sky-50/60 border-sky-100 dark:bg-sky-500/10 dark:border-sky-500/20',
    chip: 'bg-sky-100 text-sky-600 dark:bg-sky-500/20 dark:text-sky-300',
  },
  teal: {
    tile: 'bg-teal-50/60 border-teal-100 dark:bg-teal-500/10 dark:border-teal-500/20',
    chip: 'bg-teal-100 text-teal-600 dark:bg-teal-500/20 dark:text-teal-300',
  },
};

function formatDate(value?: string | null): string {
  if (!value) return '—';
  try {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString();
  } catch {
    return value;
  }
}

export function OverviewTab({
  kbDir,
  onOpenTab,
}: {
  kbDir: string | null;
  onOpenTab?: (tab: string) => void;
}) {
  const { data: kbStats, isLoading: isLoadingStats } = useQuery<KbStatusResponse | null>({
    queryKey: ['kbStats', kbDir],
    queryFn: () => getKbStats(kbDir!),
    enabled: !!kbDir,
  });

  const { data: documentsData, isLoading: isLoadingDocs } = useQuery({
    queryKey: ['documents', kbDir, 'overview'],
    queryFn: () => getDocuments(kbDir!, {}),
    enabled: !!kbDir,
  });

  const { data: pool, isLoading: isLoadingPool } = useQuery<ModelPoolData>({
    queryKey: ['modelPool', kbDir],
    queryFn: () => getModelPool(kbDir!),
    enabled: !!kbDir,
  });

  const dirs = kbStats?.directories || {};
  const reports = documentsData?.reports ?? [];
  const documents = documentsData?.documents ?? [];

  const recentDocuments = useMemo<DocumentItem[]>(
    () => [...documents].slice(0, 6),
    [documents],
  );

  const healthyRoutes = useMemo<number>(
    () =>
      (pool?.profiles ?? []).reduce(
        (count, profile) => count + (profile.routes || []).filter((route) => route.health === 'healthy').length,
        0,
      ),
    [pool?.profiles],
  );

  if (!kbDir) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground text-sm">
        Select a knowledge base to view stats.
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto pr-1">
      {/* Stats Grid */}
      <Card className="border-border/70 shadow-sm py-0 gap-0 overflow-hidden">
        <CardHeader className="border-b bg-muted/20 py-4">
          <CardTitle className="text-base">Knowledge Base Statistics</CardTitle>
          <CardDescription className="text-xs">
            {kbStats?.last_compile ? `Last compile: ${formatDate(kbStats.last_compile)}` : 'No compile yet'}
          </CardDescription>
        </CardHeader>
        <CardContent className="p-4 sm:p-6">
          {isLoadingStats && !kbStats ? (
            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">
              {Array.from({ length: 7 }).map((_, i) => (
                <div key={i} className="rounded-xl border border-border/60 p-4">
                  <Skeleton className="h-8 w-8 rounded-lg" />
                  <Skeleton className="mt-3 h-8 w-16" />
                  <Skeleton className="mt-1 h-3 w-20" />
                </div>
              ))}
            </div>
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">
              <StatTile icon={<FileText className="h-4 w-4" />} label="Tracked" value={kbStats?.total_indexed ?? 0} tone="blue" />
              <StatTile icon={<Inbox className="h-4 w-4" />} label="Raw" value={dirs.raw ?? 0} tone="amber" />
              <StatTile icon={<Layers className="h-4 w-4" />} label="Summaries" value={dirs.summaries ?? 0} tone="emerald" />
              <StatTile icon={<Files className="h-4 w-4" />} label="Reports" value={dirs.reports ?? 0} tone="violet" />
              <StatTile icon={<Building2 className="h-4 w-4" />} label="Companies" value={dirs.companies ?? 0} tone="sky" />
              <StatTile icon={<Landmark className="h-4 w-4" />} label="Industries" value={dirs.industries ?? 0} tone="teal" />
              <StatTile icon={<Lightbulb className="h-4 w-4" />} label="Concepts" value={dirs.concepts ?? 0} tone="rose" />
            </div>
          )}
        </CardContent>
      </Card>

      {/* Three section grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
        {/* Corpus / Recent Documents */}
        <Card className="border-border/70 shadow-sm py-0 gap-0 overflow-hidden">
          <CardHeader className="border-b bg-muted/20 py-3">
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="text-sm">Corpus</CardTitle>
                <CardDescription className="text-xs">Recent documents</CardDescription>
              </div>
              <button
                type="button"
                className="text-xs text-muted-foreground hover:text-foreground"
                onClick={() => onOpenTab?.('documents')}
              >
                Open →
              </button>
            </div>
          </CardHeader>
          <CardContent className="p-0">
            {isLoadingDocs && documents.length === 0 ? (
              <div className="p-4 space-y-2">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Skeleton key={i} className="h-8 w-full" />
                ))}
              </div>
            ) : recentDocuments.length === 0 ? (
              <div className="p-6 text-xs text-center text-muted-foreground">No documents yet.</div>
            ) : (
              <ul className="divide-y">
                {recentDocuments.map((doc) => (
                  <li key={doc.hash} className="px-4 py-2 flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <div className="text-sm truncate" title={doc.name}>{doc.name}</div>
                      <div className="text-[10px] text-muted-foreground">
                        {doc.type || 'unknown'} · {doc.pages || 0} page(s) · {doc.related_count} related
                      </div>
                    </div>
                    <span className="shrink-0 text-[10px] text-muted-foreground">
                      {doc.ingested_date || '—'}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        {/* Quality */}
        <Card className="border-border/70 shadow-sm py-0 gap-0 overflow-hidden">
          <CardHeader className="border-b bg-muted/20 py-3">
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="text-sm flex items-center gap-1.5">
                  <ShieldCheck className="h-3.5 w-3.5" /> Quality
                </CardTitle>
                <CardDescription className="text-xs">
                  {kbStats?.last_lint ? `Last lint: ${formatDate(kbStats.last_lint)}` : 'No lint yet'}
                </CardDescription>
              </div>
              <button
                type="button"
                className="text-xs text-muted-foreground hover:text-foreground"
                onClick={() => onOpenTab?.('quality')}
              >
                Open →
              </button>
            </div>
          </CardHeader>
          <CardContent className="p-4 space-y-2">
            <MetricRow label="Reports" value={reports.length} />
            <MetricRow label="Latest" value={reports.length ? reports[reports.length - 1] : 'None'} />
            <MetricRow label="Tracked Documents" value={kbStats?.total_indexed ?? 0} />
          </CardContent>
        </Card>

        {/* Runtime */}
        <Card className="border-border/70 shadow-sm py-0 gap-0 overflow-hidden lg:col-span-2">
          <CardHeader className="border-b bg-muted/20 py-3">
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="text-sm flex items-center gap-1.5">
                  <Server className="h-3.5 w-3.5" /> Runtime
                </CardTitle>
                <CardDescription className="text-xs">
                  {pool ? `${healthyRoutes} healthy route(s) in model pool` : 'Loading model pool…'}
                </CardDescription>
              </div>
              <span
                className={cn(
                  'text-[11px] uppercase tracking-wider px-2 py-1 rounded-md',
                  pool?.enabled
                    ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300'
                    : 'bg-muted text-muted-foreground',
                )}
              >
                {pool?.enabled ? 'Pool enabled' : 'Pool disabled'}
              </span>
            </div>
          </CardHeader>
          <CardContent className="p-4">
            {isLoadingPool && !pool ? (
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Skeleton key={i} className="h-10" />
                ))}
              </div>
            ) : (
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <MetricBox icon={<Activity className="h-3.5 w-3.5" />} label="Profiles" value={pool?.summary?.total ?? pool?.profiles?.length ?? 0} />
                <MetricBox label="Healthy Routes" value={healthyRoutes} />
                <MetricBox label="Strategy" value={pool?.strategy || '—'} />
                <MetricBox label="Active Profile" value={pool?.active_profile || '—'} />
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function StatTile({
  icon,
  label,
  value,
  tone,
}: {
  icon: React.ReactNode;
  label: string;
  value: number;
  tone: StatTone;
}) {
  const styles = toneStyles[tone];
  return (
    <div
      className={cn(
        'group relative rounded-xl border p-3 transition-all hover:shadow-md hover:-translate-y-0.5',
        styles.tile,
      )}
    >
      <div
        className={cn(
          'flex h-7 w-7 items-center justify-center rounded-lg',
          styles.chip,
        )}
      >
        {icon}
      </div>
      <div className="mt-2">
        <p className="text-2xl font-bold tracking-tight tabular-nums">{value}</p>
        <p className="text-[11px] text-muted-foreground mt-0.5">{label}</p>
      </div>
    </div>
  );
}

function MetricRow({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-muted-foreground">{label}</span>
      <strong className="tabular-nums truncate ml-2 max-w-[60%] text-right" title={String(value)}>
        {value}
      </strong>
    </div>
  );
}

function MetricBox({
  icon,
  label,
  value,
}: {
  icon?: React.ReactNode;
  label: string;
  value: string | number;
}) {
  return (
    <div className="rounded-md border bg-muted/30 px-3 py-2">
      <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted-foreground">
        {icon}
        <span>{label}</span>
      </div>
      <div className="mt-1 text-sm font-semibold truncate" title={String(value)}>
        {value}
      </div>
    </div>
  );
}
