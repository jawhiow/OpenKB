import { cva, type VariantProps } from 'class-variance-authority';
import { X } from 'lucide-react';
import { cn } from '@/lib/utils';

const badgeVariants = cva(
  'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium transition-colors',
  {
    variants: {
      variant: {
        default: 'border-border bg-muted text-foreground',
        outline: 'border-border bg-transparent text-foreground',
        secondary: 'border-transparent bg-secondary text-secondary-foreground',
        primary: 'border-primary/20 bg-primary/10 text-primary',
        success: 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300',
        warning: 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300',
        danger: 'border-red-200 bg-red-50 text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-300',
        info: 'border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-300',
      },
    },
    defaultVariants: { variant: 'default' },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {
  asChild?: boolean;
}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <span data-slot="badge" className={cn(badgeVariants({ variant }), className)} {...props} />;
}

interface RemovableBadgeProps extends BadgeProps {
  onRemove?: () => void;
  removeLabel?: string;
}

function RemovableBadge({
  children,
  onRemove,
  removeLabel = 'Remove filter',
  className,
  variant,
  ...props
}: RemovableBadgeProps) {
  return (
    <Badge variant={variant} className={cn('pr-1', className)} {...props}>
      <span className="truncate">{children}</span>
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          aria-label={removeLabel}
          className="ml-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full opacity-60 transition-opacity hover:bg-foreground/10 hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
        >
          <X className="h-3 w-3" />
        </button>
      )}
    </Badge>
  );
}

export { Badge, RemovableBadge, badgeVariants };
