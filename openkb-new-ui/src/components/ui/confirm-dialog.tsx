'use client';

import { useEffect, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';

export interface ConfirmOptions {
  title: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: 'default' | 'danger';
}

interface PendingConfirm extends ConfirmOptions {
  id: number;
  resolve: (value: boolean) => void;
}

type Subscriber = (pending: PendingConfirm | null) => void;

const subscribers = new Set<Subscriber>();
let current: PendingConfirm | null = null;
let counter = 0;

function emit() {
  for (const sub of subscribers) sub(current);
}

export function confirm(options: ConfirmOptions): Promise<boolean> {
  // If a previous confirm is open, reject it (resolve false) so the new one wins.
  if (current) {
    current.resolve(false);
    current = null;
  }
  counter += 1;
  return new Promise<boolean>((resolve) => {
    current = { id: counter, resolve, ...options };
    emit();
  });
}

export function ConfirmDialogHost() {
  const [pending, setPending] = useState<PendingConfirm | null>(current);

  useEffect(() => {
    const sub: Subscriber = (next) => setPending(next);
    subscribers.add(sub);
    return () => {
      subscribers.delete(sub);
    };
  }, []);

  const handleResolve = (value: boolean) => {
    if (!pending) return;
    pending.resolve(value);
    current = null;
    emit();
  };

  const isOpen = !!pending;
  const isDanger = pending?.variant === 'danger';

  return (
    <Dialog
      open={isOpen}
      onOpenChange={(open) => {
        if (!open) handleResolve(false);
      }}
    >
      <DialogContent showCloseButton={false} className="max-w-md">
        <DialogHeader>
          <div className="flex items-start gap-3">
            <div
              className={
                isDanger
                  ? 'flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-destructive/10 text-destructive'
                  : 'flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary'
              }
            >
              <AlertTriangle className="h-4 w-4" />
            </div>
            <div className="min-w-0 flex-1">
              <DialogTitle>{pending?.title ?? 'Confirm'}</DialogTitle>
              {pending?.description ? (
                <DialogDescription className="mt-1.5">
                  {pending.description}
                </DialogDescription>
              ) : null}
            </div>
          </div>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => handleResolve(false)}>
            {pending?.cancelLabel ?? 'Cancel'}
          </Button>
          <Button
            variant={isDanger ? 'destructive' : 'default'}
            onClick={() => handleResolve(true)}
            autoFocus
          >
            {pending?.confirmLabel ?? 'Confirm'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
