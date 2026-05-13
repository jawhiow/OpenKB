'use client';

import { X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

interface BatchBarProps {
  count: number;
  onClear: () => void;
  itemLabel?: string;
  children?: React.ReactNode;
  className?: string;
}

/**
 * Floating action bar shown when one or more items are selected.
 * Hidden when count === 0. Positioned relative to its nearest positioned ancestor.
 */
export function BatchBar({
  count,
  onClear,
  itemLabel = 'selected',
  children,
  className,
}: BatchBarProps) {
  if (count === 0) return null;

  return (
    <div
      role="region"
      aria-label={`${count} ${itemLabel}`}
      className={cn(
        'pointer-events-none absolute inset-x-0 bottom-4 z-30 flex justify-center px-4',
        className,
      )}
    >
      <div
        className={cn(
          'pointer-events-auto flex max-w-full items-center gap-2 rounded-full border bg-background/95 px-2.5 py-1.5 shadow-lg backdrop-blur-sm',
          'animate-in fade-in-0 slide-in-from-bottom-2 duration-200',
        )}
      >
        <div className="flex items-center gap-2 pl-2 pr-1">
          <span className="flex h-5 min-w-5 items-center justify-center rounded-full bg-primary px-1.5 text-[11px] font-semibold tabular-nums text-primary-foreground">
            {count}
          </span>
          <span className="text-xs font-medium text-foreground">{itemLabel}</span>
        </div>
        {children ? (
          <>
            <div aria-hidden className="h-5 w-px shrink-0 bg-border" />
            <div className="flex flex-wrap items-center gap-1.5">{children}</div>
          </>
        ) : null}
        <div aria-hidden className="h-5 w-px shrink-0 bg-border" />
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label="Clear selection"
          title="Clear selection"
          onClick={onClear}
        >
          <X />
        </Button>
      </div>
    </div>
  );
}
