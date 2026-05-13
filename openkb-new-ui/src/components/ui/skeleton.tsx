import { cn } from '@/lib/utils';

function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      data-slot="skeleton"
      className={cn('animate-pulse rounded-md bg-muted/70 dark:bg-muted/40', className)}
      {...props}
    />
  );
}

interface TableSkeletonProps {
  rows?: number;
  columns?: number;
  className?: string;
}

function TableSkeleton({ rows = 6, columns = 5, className }: TableSkeletonProps) {
  return (
    <div className={cn('w-full space-y-3', className)}>
      <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>
        {Array.from({ length: columns }).map((_, i) => (
          <Skeleton key={`h-${i}`} className="h-3.5" />
        ))}
      </div>
      <div className="space-y-2">
        {Array.from({ length: rows }).map((_, rowIndex) => (
          <div
            key={`row-${rowIndex}`}
            className="grid gap-3 rounded-md border border-border/60 p-3"
            style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
          >
            {Array.from({ length: columns }).map((_, colIndex) => (
              <Skeleton
                key={`cell-${rowIndex}-${colIndex}`}
                className={cn('h-4', colIndex === 0 && 'h-4 w-3/4', colIndex === columns - 1 && 'h-4 w-1/2')}
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

function CardListSkeleton({ count = 4, className }: { count?: number; className?: string }) {
  return (
    <div className={cn('space-y-3', className)}>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="rounded-lg border border-border/60 p-4">
          <div className="flex items-center gap-3">
            <Skeleton className="h-4 w-4 rounded-full" />
            <Skeleton className="h-4 flex-1 max-w-[40%]" />
            <Skeleton className="ml-auto h-5 w-16 rounded-full" />
          </div>
          <Skeleton className="mt-3 h-3 w-2/3" />
          <Skeleton className="mt-2 h-1.5 w-full" />
        </div>
      ))}
    </div>
  );
}

export { Skeleton, TableSkeleton, CardListSkeleton };
