'use client';

import { useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';
import { AlertCircle, CheckCircle2, Info, Loader2, X } from 'lucide-react';
import { cn } from '@/lib/utils';

export type ToastVariant = 'success' | 'error' | 'info' | 'loading';

export interface ToastItem {
  id: string;
  title: string;
  description?: string;
  variant: ToastVariant;
  duration: number;
  createdAt: number;
}

type Listener = () => void;

const listeners = new Set<Listener>();
let toasts: ToastItem[] = [];
let counter = 0;

function emit() {
  for (const l of listeners) l();
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): ToastItem[] {
  return toasts;
}

const emptySnapshot: ToastItem[] = [];
function getServerSnapshot(): ToastItem[] {
  return emptySnapshot;
}

function nextId(): string {
  counter += 1;
  return `toast-${Date.now()}-${counter}`;
}

function push(item: Omit<ToastItem, 'id' | 'createdAt'> & { id?: string }): string {
  const id = item.id ?? nextId();
  const existingIndex = toasts.findIndex((t) => t.id === id);
  const next: ToastItem = {
    id,
    title: item.title,
    description: item.description,
    variant: item.variant,
    duration: item.duration,
    createdAt: Date.now(),
  };

  toasts = existingIndex >= 0
    ? toasts.map((t, i) => (i === existingIndex ? next : t))
    : [...toasts, next];
  emit();

  if (next.duration > 0) {
    setTimeout(() => dismiss(id), next.duration);
  }
  return id;
}

export function dismiss(id: string) {
  if (!toasts.some((t) => t.id === id)) return;
  toasts = toasts.filter((t) => t.id !== id);
  emit();
}

export const toast = {
  success(title: string, description?: string, duration = 4000) {
    return push({ title, description, variant: 'success', duration });
  },
  error(title: string, description?: string, duration = 6000) {
    return push({ title, description, variant: 'error', duration });
  },
  info(title: string, description?: string, duration = 4000) {
    return push({ title, description, variant: 'info', duration });
  },
  loading(title: string, description?: string) {
    return push({ title, description, variant: 'loading', duration: 0 });
  },
  dismiss,
  update(
    id: string,
    item: Partial<Omit<ToastItem, 'id' | 'createdAt'>> & {
      variant: ToastVariant;
      title: string;
      duration?: number;
    },
  ) {
    return push({
      id,
      title: item.title,
      description: item.description,
      variant: item.variant,
      duration: item.duration ?? (item.variant === 'error' ? 6000 : 4000),
    });
  },
};

const variantStyles: Record<ToastVariant, { tone: string; icon: React.ReactNode }> = {
  success: {
    tone: 'border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-100',
    icon: <CheckCircle2 className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />,
  },
  error: {
    tone: 'border-red-200 bg-red-50 text-red-900 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-100',
    icon: <AlertCircle className="h-4 w-4 text-red-600 dark:text-red-400" />,
  },
  info: {
    tone: 'border-border bg-background text-foreground',
    icon: <Info className="h-4 w-4 text-primary" />,
  },
  loading: {
    tone: 'border-border bg-background text-foreground',
    icon: <Loader2 className="h-4 w-4 animate-spin text-primary" />,
  },
};

function useMounted(): boolean {
  // useSyncExternalStore lets us safely report SSR=false / client=true without an effect that triggers a render.
  return useSyncExternalStore(
    () => () => undefined,
    () => true,
    () => false,
  );
}

export function Toaster() {
  const items = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  const mounted = useMounted();
  if (!mounted) return null;

  return createPortal(
    <div
      role="region"
      aria-label="Notifications"
      className="pointer-events-none fixed bottom-4 right-4 z-[100] flex w-full max-w-sm flex-col gap-2 sm:right-6 sm:bottom-6"
    >
      {items.map((item) => {
        const styles = variantStyles[item.variant];
        return (
          <div
            key={item.id}
            role={item.variant === 'error' ? 'alert' : 'status'}
            className={cn(
              'pointer-events-auto flex w-full gap-3 rounded-lg border px-4 py-3 shadow-lg backdrop-blur-sm transition-all',
              'animate-in slide-in-from-bottom-2 fade-in-0',
              styles.tone,
            )}
          >
            <div className="mt-0.5 shrink-0">{styles.icon}</div>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium leading-tight">{item.title}</p>
              {item.description ? (
                <p className="mt-1 text-xs opacity-80 break-words">{item.description}</p>
              ) : null}
            </div>
            {item.variant !== 'loading' && (
              <button
                type="button"
                onClick={() => dismiss(item.id)}
                aria-label="Dismiss notification"
                className="shrink-0 self-start rounded-sm p-0.5 opacity-60 transition-opacity hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        );
      })}
    </div>,
    document.body,
  );
}
