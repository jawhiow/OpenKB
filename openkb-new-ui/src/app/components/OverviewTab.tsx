'use client';

import React, { useMemo } from 'react';
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
import { Button } from '@/components/ui/button';
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
    <div className="h-full overflow-y-auto pr-1 custom-scrollbar">
      {/* Stats Grid */}
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-4 mb-6">
        <StatTile icon={<FileText />} label="Tracked" value={kbStats?.total_indexed ?? 0} tone="blue" />
        <StatTile icon={<Inbox />} label="Raw" value={dirs.raw ?? 0} tone="amber" />
        <StatTile icon={<Layers />} label="Summaries" value={dirs.summaries ?? 0} tone="emerald" />
        <StatTile icon={<Files />} label="Reports" value={dirs.reports ?? 0} tone="violet" />
        <StatTile icon={<Building2 />} label="Companies" value={dirs.companies ?? 0} tone="sky" />
        <StatTile icon={<Landmark />} label="Industries" value={dirs.industries ?? 0} tone="teal" />
        <StatTile icon={<Lightbulb />} label="Concepts" value={dirs.concepts ?? 0} tone="rose" />
      </div>

      {/* Three section grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Corpus / Recent Documents */}
        <Card className="lg:col-span-2 border-none bg-accent/20 shadow-none overflow-hidden rounded-[2rem]">
          <CardHeader className="px-8 pt-8 pb-4">
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="text-xl font-bold tracking-tight">Corpus</CardTitle>
                <CardDescription className="text-sm font-medium opacity-60">Recent documents in workspace</CardDescription>
              </div>
              <Button
                variant="secondary"
                size="sm"
                className="rounded-xl font-bold text-xs uppercase tracking-wider"
                onClick={() => onOpenTab?.('documents')}
              >
                View All
              </Button>
            </div>
          </CardHeader>
          <CardContent className="px-6 pb-8">
            {isLoadingDocs && documents.length === 0 ? (
              <div className="space-y-3">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Skeleton key={i} className="h-16 w-full rounded-2xl" />
                ))}
              </div>
            ) : recentDocuments.length === 0 ? (
              <div className="py-12 flex flex-col items-center justify-center opacity-40">
                <FileText className="h-12 w-12 mb-2" />
                <p className="text-sm font-bold">No documents indexed</p>
              </div>
            ) : (
              <div className="grid gap-3">
                {recentDocuments.map((doc) => (
                  <div key={doc.hash} className="group/doc px-5 py-4 rounded-2xl bg-background/50 border border-transparent hover:border-primary/20 hover:shadow-xl hover:shadow-primary/5 transition-all flex items-center justify-between gap-4">
                    <div className="min-w-0 flex items-center gap-4">
                      <div className="h-10 w-10 shrink-0 rounded-xl bg-primary/5 flex items-center justify-center text-primary group-hover/doc:bg-primary group-hover/doc:text-primary-foreground transition-colors">
                        <FileText className="h-5 w-5" />
                      </div>
                      <div className="min-w-0">
                        <div className="text-sm font-bold truncate tracking-tight" title={doc.name}>{doc.name}</div>
                        <div className="text-[10px] uppercase font-black tracking-widest opacity-40 mt-0.5">
                          {doc.type || 'unknown'} · {doc.pages || 0} PG · {doc.related_count} REL
                        </div>
                      </div>
                    </div>
                    <div className="shrink-0 text-[10px] font-bold opacity-30">
                      {doc.ingested_date || '—'}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <div className="space-y-6">
          {/* Quality */}
          <Card className="border-none bg-primary/5 shadow-none overflow-hidden rounded-[2rem]">
            <CardHeader className="px-8 pt-8 pb-4">
              <CardTitle className="text-lg font-bold flex items-center gap-2">
                <ShieldCheck className="h-5 w-5 text-primary" /> Quality
              </CardTitle>
              <CardDescription className="text-xs font-medium opacity-60">
                {kbStats?.last_lint ? `LINTED ${formatDate(kbStats.last_lint).toUpperCase()}` : 'NEVER LINTED'}
              </CardDescription>
            </CardHeader>
            <CardContent className="px-8 pb-8 space-y-4">
              <div className="p-4 rounded-2xl bg-background/40 space-y-3">
                <MetricRow label="Total Reports" value={reports.length} />
                <MetricRow label="Last Report" value={reports.length ? reports[reports.length - 1] : 'None'} />
                <MetricRow label="Index Count" value={kbStats?.total_indexed ?? 0} />
              </div>
              <Button
                variant="default"
                className="w-full rounded-xl font-bold text-xs uppercase tracking-widest shadow-lg shadow-primary/20"
                onClick={() => onOpenTab?.('quality')}
              >
                Run Diagnostics
              </Button>
            </CardContent>
          </Card>

          {/* Runtime */}
          <Card className="border-none bg-accent/30 shadow-none overflow-hidden rounded-[2rem]">
            <CardHeader className="px-8 pt-8 pb-4">
              <div className="flex items-center justify-between">
                <CardTitle className="text-lg font-bold flex items-center gap-2">
                  <Server className="h-5 w-5 text-muted-foreground" /> Runtime
                </CardTitle>
                <div className={cn(
                  "h-2 w-2 rounded-full animate-pulse",
                  pool?.enabled ? "bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.5)]" : "bg-muted-foreground/30"
                )} />
              </div>
              <CardDescription className="text-xs font-medium opacity-60">
                {pool ? `${healthyRoutes} ACTIVE ROUTES` : 'CONNECTING...'}
              </CardDescription>
            </CardHeader>
            <CardContent className="px-8 pb-8">
              <div className="grid grid-cols-2 gap-3 mb-4">
                <MetricBox label="Profiles" value={pool?.summary?.total ?? pool?.profiles?.length ?? 0} />
                <MetricBox label="Healthy" value={healthyRoutes} />
              </div>
              <div className="p-4 rounded-2xl bg-background/40 space-y-2">
                <div className="flex justify-between items-center">
                  <span className="text-[10px] font-black uppercase tracking-widest opacity-40">Strategy</span>
                  <span className="text-xs font-bold">{pool?.strategy || '—'}</span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-[10px] font-black uppercase tracking-widest opacity-40">Active</span>
                  <span className="text-xs font-bold truncate max-w-[100px]">{pool?.active_profile || '—'}</span>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
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
        'group relative rounded-3xl border-none p-5 transition-all hover:scale-[1.02] active:scale-[0.98] cursor-default bg-card shadow-sm hover:shadow-xl hover:shadow-foreground/5',
      )}
    >
      <div
        className={cn(
          'flex h-10 w-10 items-center justify-center rounded-2xl mb-4 transition-transform group-hover:rotate-6',
          styles.chip,
        )}
      >
        {React.cloneElement(icon as React.ReactElement<any>, { className: "h-5 w-5" })}
      </div>
      <div>
        <p className="text-3xl font-black tracking-tighter tabular-nums">{value}</p>
        <p className="text-[10px] font-bold uppercase tracking-widest opacity-40 mt-1">{label}</p>
      </div>
    </div>
  );
}

function MetricRow({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-[11px] font-bold uppercase tracking-wider opacity-50">{label}</span>
      <span className="text-xs font-bold tabular-nums truncate ml-4" title={String(value)}>
        {value}
      </span>
    </div>
  );
}

function MetricBox({
  label,
  value,
}: {
  label: string;
  value: string | number;
}) {
  return (
    <div className="rounded-2xl bg-background/40 p-4 border border-transparent hover:border-primary/10 transition-colors">
      <div className="text-[9px] font-black uppercase tracking-widest opacity-40 mb-1">
        {label}
      </div>
      <div className="text-sm font-black truncate" title={String(value)}>
        {value}
      </div>
    </div>
  );
}
