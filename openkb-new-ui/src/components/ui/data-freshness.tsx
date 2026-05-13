'use client';

import { useEffect, useState } from 'react';
import { RefreshCcw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

/**
 * Formats a moment as a short relative-time string: "just now", "12s ago", "3m ago", "2h ago".
 * Returns the empty string for non-dates so callers can render nothing safely.
 */
export function formatRelativeTime(value: Date | number | string | null | undefined): string {
  if (value === null || value === undefined) return '';
  const ts = typeof value === 'number' ? value : new Date(value).getTime();
  if (!Number.isFinite(ts)) return '';
  const diff = Math.max(0, Date.now() - ts);
  const seconds = Math.floor(diff / 1000);
  if (seconds < 5) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  // Fall back to a stable absolute date for older values.
  return new Date(ts).toLocaleDateString();
}

/**
 * Re-renders on a schedule so a relative timestamp stays fresh.
 * Returns the formatted label. Tick interval scales with age to avoid burning CPU.
 */
export function useRelativeTime(value: Date | number | string | null | undefined): string {
  const [, force] = useState(0);

  useEffect(() => {
    if (value === null || value === undefined) return;
    const ts = typeof value === 'number' ? value : new Date(value).getTime();
    if (!Number.isFinite(ts)) return;
    const ageSec = Math.max(0, (Date.now() - ts) / 1000);
    const interval = ageSec < 60 ? 1000 : ageSec < 3600 ? 30_000 : 5 * 60_000;
    const id = setInterval(() => force((n) => n + 1), interval);
    return () => clearInterval(id);
  }, [value]);

  return formatRelativeTime(value);
}

interface DataFreshnessProps {
  /** Most recent successful fetch timestamp (e.g. `dataUpdatedAt` from useQuery). */
  updatedAt?: number | null;
  /** True while a background refetch is in flight. */
  isFetching?: boolean;
  /** Manual refresh handler. Hides the button if omitted. */
  onRefresh?: () => void;
  className?: string;
  label?: string;
}

/**
 * Compact freshness indicator + refresh button. Drop next to a list / card title.
 */
export function DataFreshness({
  updatedAt,
  isFetching,
  onRefresh,
  className,
  label = 'Updated',
}: DataFreshnessProps) {
  const relative = useRelativeTime(updatedAt ?? null);
  const tooltip = updatedAt ? new Date(updatedAt).toLocaleString() : 'No data yet';

  return (
    <div className={cn('flex items-center gap-1 text-[11px] text-muted-foreground', className)}>
      <span title={tooltip} className="tabular-nums">
        {updatedAt ? `${label} ${relative}` : 'No data'}
      </span>
      {onRefresh ? (
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          onClick={onRefresh}
          disabled={isFetching}
          aria-label="Refresh"
          title={isFetching ? 'Refreshing…' : 'Refresh now'}
          className="text-muted-foreground hover:text-foreground"
        >
          <RefreshCcw className={cn('h-3 w-3', isFetching && 'animate-spin')} />
        </Button>
      ) : null}
    </div>
  );
}
