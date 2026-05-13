'use client';

import { RemovableBadge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

export interface FilterChip {
  /** Stable key. */
  key: string;
  /** Visible label, e.g. "State: ready". */
  label: string;
  /** Called when the user clicks the chip's X. */
  onRemove: () => void;
}

interface ActiveFiltersProps {
  filters: FilterChip[];
  onClearAll?: () => void;
  className?: string;
}

/**
 * Inline chip strip for the currently active filter set, with optional "Clear all".
 * Renders nothing when there are no active filters.
 */
export function ActiveFilters({ filters, onClearAll, className }: ActiveFiltersProps) {
  if (filters.length === 0) return null;

  return (
    <div className={cn('flex flex-wrap items-center gap-1.5', className)}>
      <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
        Active
      </span>
      {filters.map((filter) => (
        <RemovableBadge
          key={filter.key}
          variant="primary"
          onRemove={filter.onRemove}
          removeLabel={`Remove filter ${filter.label}`}
        >
          {filter.label}
        </RemovableBadge>
      ))}
      {onClearAll && filters.length > 1 ? (
        <Button
          type="button"
          size="xs"
          variant="ghost"
          onClick={onClearAll}
          className="h-6 px-2 text-[11px] text-muted-foreground hover:text-foreground"
        >
          Clear all
        </Button>
      ) : null}
    </div>
  );
}
