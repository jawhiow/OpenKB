'use client';

import { useMemo } from 'react';
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

interface PaginationProps {
  page: number;
  pageSize: number;
  total: number;
  pageSizeOptions?: number[];
  onPageChange: (page: number) => void;
  onPageSizeChange?: (size: number) => void;
  className?: string;
  label?: string;
}

export function Pagination({
  page,
  pageSize,
  total,
  pageSizeOptions = [50, 100, 200],
  onPageChange,
  onPageSizeChange,
  className,
  label = 'items',
}: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(Math.max(1, page), totalPages);
  const rangeStart = total === 0 ? 0 : (safePage - 1) * pageSize + 1;
  const rangeEnd = Math.min(total, safePage * pageSize);

  const canPrev = safePage > 1;
  const canNext = safePage < totalPages;

  const visiblePages = useMemo(() => buildPageWindow(safePage, totalPages), [safePage, totalPages]);

  return (
    <div
      className={cn(
        'flex flex-col gap-3 border-t bg-muted/20 px-4 py-2 text-xs text-muted-foreground sm:flex-row sm:items-center sm:justify-between',
        className,
      )}
    >
      <div className="flex items-center gap-3">
        <span className="tabular-nums">
          {total === 0 ? `0 ${label}` : `${rangeStart}–${rangeEnd} of ${total} ${label}`}
        </span>
        {onPageSizeChange ? (
          <label className="flex items-center gap-1.5">
            <span className="hidden sm:inline">Rows</span>
            <select
              value={pageSize}
              onChange={(event) => onPageSizeChange(Number(event.target.value))}
              className="h-7 rounded-md border border-border bg-background px-1.5 text-xs"
            >
              {pageSizeOptions.map((size) => (
                <option key={size} value={size}>
                  {size}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>

      <div className="flex items-center gap-1">
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="First page"
          disabled={!canPrev}
          onClick={() => onPageChange(1)}
        >
          <ChevronsLeft />
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Previous page"
          disabled={!canPrev}
          onClick={() => onPageChange(safePage - 1)}
        >
          <ChevronLeft />
        </Button>

        {visiblePages.map((p, i) =>
          p === '…' ? (
            <span key={`gap-${i}`} className="px-1.5 text-muted-foreground/70">
              …
            </span>
          ) : (
            <Button
              key={p}
              variant={p === safePage ? 'default' : 'ghost'}
              size="icon-sm"
              aria-label={`Page ${p}`}
              aria-current={p === safePage ? 'page' : undefined}
              onClick={() => onPageChange(p)}
              className="tabular-nums text-xs"
            >
              {p}
            </Button>
          ),
        )}

        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Next page"
          disabled={!canNext}
          onClick={() => onPageChange(safePage + 1)}
        >
          <ChevronRight />
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Last page"
          disabled={!canNext}
          onClick={() => onPageChange(totalPages)}
        >
          <ChevronsRight />
        </Button>
      </div>
    </div>
  );
}

function buildPageWindow(current: number, total: number): Array<number | '…'> {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);

  const pages: Array<number | '…'> = [1];
  const start = Math.max(2, current - 1);
  const end = Math.min(total - 1, current + 1);

  if (start > 2) pages.push('…');
  for (let i = start; i <= end; i += 1) pages.push(i);
  if (end < total - 1) pages.push('…');

  pages.push(total);
  return pages;
}
