'use client';

import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertCircle,
  CheckCircle2,
  Gauge,
  Loader2,
  RefreshCcw,
  Save,
  ShieldAlert,
  ShieldCheck,
} from 'lucide-react';
import {
  getIngestGate,
  IngestGateConfig,
  IngestGateDecision,
  IngestGateDimensionScore,
  saveIngestGateConfig,
} from '@/lib/api';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';

const DIMENSION_LABELS: Array<[string, string]> = [
  ['relevance', 'Relevance'],
  ['authority_traceability', 'Authority / Traceability'],
  ['signal_density', 'Signal Density'],
  ['novelty_vs_kb', 'Novelty vs KB'],
  ['durability', 'Durability'],
  ['compilation_yield', 'Compilation Yield'],
  ['actionability', 'Actionability'],
];

const DEFAULT_CONFIG: IngestGateConfig = {
  enabled: false,
  pass_threshold: 75,
  hold_threshold: 60,
  hard_reject_enabled: true,
  log_all_decisions: true,
  allow_force_pass: true,
  allow_force_reject: true,
};

function decisionId(decision: IngestGateDecision): string {
  return String(
    decision.id ||
      decision.line_number ||
      `${decision.timestamp || ''}-${decision.doc_title || ''}` ||
      Math.random(),
  );
}

function decisionTone(value?: string): { className: string; label: string } {
  const label = String(value || 'UNKNOWN').toUpperCase();
  if (label === 'PASS' || label === 'FORCE_PASS') {
    return {
      className: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300',
      label,
    };
  }
  if (label === 'REJECT' || label === 'FORCE_REJECT') {
    return { className: 'bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300', label };
  }
  if (label === 'HOLD') {
    return { className: 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300', label };
  }
  return { className: 'bg-muted text-muted-foreground', label };
}

function formatDateTime(value?: string | null): string {
  if (!value) return '';
  try {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString();
  } catch {
    return value;
  }
}

export function ScoringTab({ kbDir }: { kbDir: string | null }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<IngestGateConfig>(DEFAULT_CONFIG);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string>('');
  const [saveOk, setSaveOk] = useState(false);

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['ingest-gate', kbDir],
    queryFn: () => getIngestGate(kbDir!, 250),
    enabled: !!kbDir,
  });

  const decisions = useMemo<IngestGateDecision[]>(() => data?.decisions ?? [], [data?.decisions]);
  const summary = data?.summary ?? {};
  const remoteConfig = data?.config;

  // Sync remote config to draft when first loaded or kb changes
  useEffect(() => {
    if (remoteConfig) {
      setDraft({ ...DEFAULT_CONFIG, ...remoteConfig });
    }
  }, [remoteConfig]);

  // Auto-select first decision
  useEffect(() => {
    if (!decisions.length) {
      setSelectedId(null);
      return;
    }
    if (selectedId && decisions.some((d) => decisionId(d) === selectedId)) return;
    setSelectedId(decisionId(decisions[0]));
  }, [decisions, selectedId]);

  const selectedDecision = useMemo(
    () => decisions.find((d) => decisionId(d) === selectedId) ?? null,
    [decisions, selectedId],
  );

  const saveMutation = useMutation({
    mutationFn: () => saveIngestGateConfig(kbDir!, draft),
    onSuccess: () => {
      setErrorMessage('');
      setSaveOk(true);
      setTimeout(() => setSaveOk(false), 2000);
      queryClient.invalidateQueries({ queryKey: ['ingest-gate', kbDir] });
      queryClient.invalidateQueries({ queryKey: ['config', kbDir] });
    },
    onError: (error: Error) => setErrorMessage(error.message),
  });

  if (!kbDir) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground text-sm">
        Select a knowledge base to view ingest scoring.
      </div>
    );
  }

  return (
    <Card className="h-full flex flex-col overflow-hidden py-0 gap-0 border-border/70 shadow-sm">
      <CardHeader className="shrink-0 border-b bg-muted/20 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary/10 text-primary">
              <Gauge className="h-4 w-4" />
            </div>
            <div>
              <CardTitle className="text-base">Ingest Scoring</CardTitle>
              <CardDescription className="text-xs">
                {summary.latest_at ? `Latest ${formatDateTime(summary.latest_at)}` : 'No scoring history yet'}
              </CardDescription>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={`text-[11px] uppercase tracking-wider px-2 py-1 rounded-md ${
                draft.enabled
                  ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300'
                  : 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300'
              }`}
            >
              {draft.enabled ? 'Enabled' : 'Disabled'}
            </span>
            <Button variant="outline" size="sm" onClick={() => refetch()} disabled={isFetching}>
              {isFetching ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCcw className="h-3.5 w-3.5" />}
              Refresh
            </Button>
            <Button size="sm" onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>
              {saveMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
              Save Settings
            </Button>
          </div>
        </div>

        <div className="mt-4 grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2">
          <StatChip label="Total" value={summary.total ?? 0} />
          <StatChip label="Pass" value={summary.pass ?? 0} tone="emerald" />
          <StatChip label="Hold" value={summary.hold ?? 0} tone="amber" />
          <StatChip label="Reject" value={summary.reject ?? 0} tone="rose" />
          <StatChip label="Force Pass" value={summary.force_pass ?? 0} />
          <StatChip label="Force Reject" value={summary.force_reject ?? 0} />
          <StatChip label="Avg Score" value={summary.average_score ?? '—'} />
        </div>

        {saveOk ? (
          <Alert className="mt-3 border-emerald-300 bg-emerald-50 dark:border-emerald-500/40 dark:bg-emerald-500/10">
            <CheckCircle2 className="h-4 w-4 text-emerald-600 dark:text-emerald-300" />
            <AlertTitle>Saved</AlertTitle>
            <AlertDescription>Gate configuration updated.</AlertDescription>
          </Alert>
        ) : null}

        {errorMessage ? (
          <Alert variant="destructive" className="mt-3">
            <AlertCircle className="h-4 w-4" />
            <AlertTitle>Save failed</AlertTitle>
            <AlertDescription>{errorMessage}</AlertDescription>
          </Alert>
        ) : null}
      </CardHeader>

      <CardContent className="flex-1 overflow-hidden p-0 flex flex-col min-h-0">
        <div className="grid grid-cols-1 lg:grid-cols-[280px_320px_1fr] flex-1 overflow-hidden min-h-0">
          {/* Settings column */}
          <div className="flex flex-col border-r min-h-0 overflow-hidden">
            <div className="px-4 py-2 border-b bg-muted/10 text-xs font-semibold uppercase tracking-wider text-muted-foreground shrink-0">
              Gate Settings
            </div>
            <ScrollArea className="flex-1 min-h-0 overflow-hidden">
              <div className="p-4 space-y-4">
                <ToggleField
                  label="Gate Enabled"
                  description="Score new documents before ingestion"
                  value={draft.enabled}
                  onChange={(v) => setDraft({ ...draft, enabled: v })}
                />
                <NumberField
                  label="Pass Threshold"
                  description="Score ≥ this passes automatically"
                  value={draft.pass_threshold}
                  onChange={(v) => setDraft({ ...draft, pass_threshold: v })}
                />
                <NumberField
                  label="Hold Threshold"
                  description="Score ≥ this enters hold state"
                  value={draft.hold_threshold}
                  onChange={(v) => setDraft({ ...draft, hold_threshold: v })}
                />
                <ToggleField
                  label="Hard Reject Enforced"
                  value={draft.hard_reject_enabled}
                  onChange={(v) => setDraft({ ...draft, hard_reject_enabled: v })}
                />
                <ToggleField
                  label="Log All Decisions"
                  value={draft.log_all_decisions}
                  onChange={(v) => setDraft({ ...draft, log_all_decisions: v })}
                />
                <ToggleField
                  label="Allow Force Pass"
                  value={draft.allow_force_pass}
                  onChange={(v) => setDraft({ ...draft, allow_force_pass: v })}
                />
                <ToggleField
                  label="Allow Force Reject"
                  value={draft.allow_force_reject}
                  onChange={(v) => setDraft({ ...draft, allow_force_reject: v })}
                />
              </div>
            </ScrollArea>
          </div>

          {/* Decisions list */}
          <div className="flex flex-col border-r min-h-0 overflow-hidden">
            <div className="px-4 py-2 border-b bg-muted/10 text-xs font-semibold uppercase tracking-wider text-muted-foreground shrink-0 flex items-center justify-between">
              <span>Scoring History</span>
              <span className="text-[10px] normal-case tracking-normal text-muted-foreground/70">
                {decisions.length} recent
              </span>
            </div>
            <ScrollArea className="flex-1 min-h-0 overflow-hidden">
              {isLoading ? (
                <div className="p-6 flex justify-center">
                  <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                </div>
              ) : decisions.length === 0 ? (
                <div className="p-6 text-xs text-muted-foreground text-center">No scoring decisions yet.</div>
              ) : (
                <ul className="divide-y">
                  {decisions.map((decision) => {
                    const id = decisionId(decision);
                    const active = id === selectedId;
                    const tone = decisionTone(decision.final_decision || decision.raw_decision);
                    const score =
                      decision.total_score === null || decision.total_score === undefined
                        ? 'unscored'
                        : `${decision.total_score}/100`;
                    return (
                      <li key={id}>
                        <button
                          type="button"
                          onClick={() => setSelectedId(id)}
                          className={`w-full text-left px-4 py-3 hover:bg-muted/50 transition-colors ${
                            active ? 'bg-muted' : ''
                          }`}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <strong className="text-xs truncate flex-1">
                              {decision.doc_title || 'Untitled document'}
                            </strong>
                            <span className={`text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded ${tone.className}`}>
                              {tone.label}
                            </span>
                          </div>
                          <div className="flex items-center justify-between gap-2 mt-1">
                            <span className="text-[10px] text-muted-foreground">
                              {formatDateTime(decision.timestamp)}
                            </span>
                            <span className="text-[11px] font-medium tabular-nums">{score}</span>
                          </div>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </ScrollArea>
          </div>

          {/* Decision detail */}
          <div className="flex flex-col min-h-0 overflow-hidden">
            <div className="px-4 py-2 border-b bg-muted/10 text-xs font-semibold uppercase tracking-wider text-muted-foreground shrink-0">
              Decision Detail
            </div>
            <ScrollArea className="flex-1 min-h-0 overflow-hidden">
              {selectedDecision ? (
                <DecisionDetail decision={selectedDecision} />
              ) : (
                <div className="p-6 text-xs text-muted-foreground text-center">
                  Select a decision to view full scoring breakdown.
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
  value: number | string;
  tone?: 'default' | 'emerald' | 'amber' | 'rose';
}) {
  const toneClass =
    tone === 'emerald'
      ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300'
      : tone === 'amber'
        ? 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300'
        : tone === 'rose'
          ? 'bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300'
          : 'bg-muted text-foreground';
  return (
    <div className="rounded-md border bg-background/60 px-3 py-2 flex items-center justify-between gap-2">
      <span className="text-[11px] uppercase tracking-wider text-muted-foreground truncate">{label}</span>
      <span className={`text-sm font-semibold px-1.5 py-0.5 rounded ${toneClass} tabular-nums`}>{value}</span>
    </div>
  );
}

function ToggleField({
  label,
  description,
  value,
  onChange,
}: {
  label: string;
  description?: string;
  value: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex items-start gap-2 cursor-pointer">
      <input
        type="checkbox"
        className="mt-1 h-4 w-4 rounded border-input"
        checked={value}
        onChange={(event) => onChange(event.target.checked)}
      />
      <div className="flex-1">
        <div className="text-sm font-medium">{label}</div>
        {description ? <div className="text-xs text-muted-foreground">{description}</div> : null}
      </div>
    </label>
  );
}

function NumberField({
  label,
  description,
  value,
  onChange,
}: {
  label: string;
  description?: string;
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <div>
      <label className="text-sm font-medium">{label}</label>
      {description ? <div className="text-xs text-muted-foreground mb-1">{description}</div> : null}
      <Input
        type="number"
        min={0}
        max={100}
        value={value}
        onChange={(event) => {
          const next = Number(event.target.value);
          if (!Number.isNaN(next)) onChange(next);
        }}
        className="mt-1 h-8"
      />
    </div>
  );
}

function DecisionDetail({ decision }: { decision: IngestGateDecision }) {
  const tone = decisionTone(decision.final_decision || decision.raw_decision);
  const score =
    decision.total_score === null || decision.total_score === undefined
      ? 'unscored'
      : `${decision.total_score}/100`;
  const dims = decision.dimension_scores || {};
  return (
    <div className="p-4 space-y-4">
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-semibold text-sm truncate">{decision.doc_title || 'Untitled document'}</h3>
          <div className="text-xs text-muted-foreground">{formatDateTime(decision.timestamp)}</div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className={`text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded ${tone.className}`}>
            {tone.label}
          </span>
          <strong className="text-sm tabular-nums">{score}</strong>
        </div>
      </header>

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
        <SmallStat label="Raw" value={decision.raw_decision || ''} />
        <SmallStat label="Mode" value={decision.recommended_ingest_mode || ''} />
        <SmallStat label="Type" value={decision.doc_type || ''} />
        <SmallStat label="Hard Reject" value={decision.hard_reject ? 'yes' : 'no'} />
        <SmallStat label="Force Pass" value={decision.force_pass ? 'yes' : 'no'} />
        <SmallStat label="Force Reject" value={decision.force_reject ? 'yes' : 'no'} />
      </div>

      {decision.one_line_verdict ? (
        <div className="rounded-md border bg-muted/30 px-3 py-2 text-sm italic">{decision.one_line_verdict}</div>
      ) : null}
      {decision.source_info ? (
        <div className="text-xs text-muted-foreground break-all">{decision.source_info}</div>
      ) : null}

      <DimensionList scores={dims} />

      <DetailListGroup
        items={[
          { label: 'Primary Reasons', values: decision.primary_reasons },
          { label: 'Hard Reject Reasons', values: decision.hard_reject_reasons },
          { label: 'Overlap With Existing KB', values: decision.overlap_with_existing_kb },
          { label: 'Suggested Outputs', values: decision.suggested_outputs_if_ingested },
        ]}
      />

      <AuditTrailBlock decision={decision} />
    </div>
  );
}

function SmallStat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-md border bg-background/60 px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="text-sm font-medium truncate">{value || '—'}</div>
    </div>
  );
}

function DimensionList({ scores }: { scores: Record<string, IngestGateDimensionScore> }) {
  return (
    <div className="space-y-3">
      <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Dimensions</div>
      <div className="space-y-3">
        {DIMENSION_LABELS.map(([key, label]) => {
          const item = scores[key] || {};
          const max = Math.max(Number(item.max || 0), 1);
          const score = Math.max(Math.min(Number(item.score || 0), max), 0);
          const width = Math.round((score / max) * 100);
          return (
            <div key={key} className="space-y-1">
              <div className="flex items-center justify-between text-xs">
                <span className="font-medium">{label}</span>
                <span className="tabular-nums text-muted-foreground">
                  {score}/{max}
                </span>
              </div>
              <div className="h-1.5 w-full rounded bg-muted overflow-hidden">
                <div className="h-full bg-primary" style={{ width: `${width}%` }} />
              </div>
              {item.reason ? <p className="text-xs text-muted-foreground">{item.reason}</p> : null}
              {item.evidence?.length ? (
                <details className="text-xs">
                  <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                    Evidence ({item.evidence.length})
                  </summary>
                  <ul className="mt-1 pl-4 list-disc space-y-0.5 text-muted-foreground">
                    {item.evidence.map((e, i) => (
                      <li key={i}>{e}</li>
                    ))}
                  </ul>
                </details>
              ) : null}
              {item.deductions?.length ? (
                <details className="text-xs">
                  <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                    Deductions ({item.deductions.length})
                  </summary>
                  <ul className="mt-1 pl-4 list-disc space-y-0.5 text-muted-foreground">
                    {item.deductions.map((d, i) => (
                      <li key={i}>{d}</li>
                    ))}
                  </ul>
                </details>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function DetailListGroup({ items }: { items: Array<{ label: string; values?: string[] }> }) {
  const visible = items.filter((item) => (item.values?.length ?? 0) > 0);
  if (!visible.length) return null;
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      {visible.map((item) => (
        <div key={item.label} className="rounded-md border bg-muted/30 p-3">
          <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-1.5">
            {item.label}
          </div>
          <ul className="space-y-1 text-xs">
            {item.values!.map((v, i) => (
              <li key={i} className="flex items-start gap-1">
                <ShieldCheck className="h-3 w-3 mt-0.5 shrink-0 opacity-50" />
                <span className="flex-1">{v}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function AuditTrailBlock({ decision }: { decision: IngestGateDecision }) {
  const audit = decision.audit_trail || {};
  const rows: Array<[string, string]> = [
    ['Why this decision', audit.why_this_decision || ''],
    ['Why not higher', audit.why_not_higher || ''],
    ['Why not lower', audit.why_not_lower || ''],
    ['Operator', decision.operator || ''],
    ['Force reason', decision.force_reason || ''],
  ].filter(([, v]) => Boolean(v)) as Array<[string, string]>;
  if (!rows.length) return null;
  return (
    <div className="rounded-md border bg-muted/30 p-3">
      <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2 flex items-center gap-1.5">
        <ShieldAlert className="h-3 w-3" />
        Audit Trail
      </div>
      <dl className="space-y-2 text-xs">
        {rows.map(([k, v]) => (
          <div key={k}>
            <dt className="font-medium">{k}</dt>
            <dd className="text-muted-foreground">{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
