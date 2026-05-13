'use client';

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { createPortal } from 'react-dom';
import { Search, CornerDownLeft } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface CommandItem {
  /** Stable id. */
  id: string;
  /** Visible primary text. */
  label: string;
  /** Optional secondary text (e.g. full path, description). */
  description?: string;
  /** Group label for visual sectioning. */
  group?: string;
  /** Optional leading icon. */
  icon?: React.ReactNode;
  /** Optional trailing keyboard hint (e.g. "⌘ /"). */
  hint?: string;
  /** Optional extra keywords for matching. */
  keywords?: string;
  /** Invoked on selection (Enter or click). Palette closes automatically afterwards. */
  perform: () => void | Promise<void>;
}

interface CommandPaletteProps {
  /** Called each time the palette opens; should return a fresh command list. */
  getCommands: () => CommandItem[];
  /** Placeholder for the search input. */
  placeholder?: string;
}

const SUBSCRIBERS = new Set<() => void>();
let currentOpen = false;

function emit() {
  for (const sub of SUBSCRIBERS) sub();
}

export function openCommandPalette() {
  if (currentOpen) return;
  currentOpen = true;
  emit();
}

export function closeCommandPalette() {
  if (!currentOpen) return;
  currentOpen = false;
  emit();
}

export function toggleCommandPalette() {
  currentOpen = !currentOpen;
  emit();
}

function subscribeOpen(listener: () => void): () => void {
  SUBSCRIBERS.add(listener);
  return () => {
    SUBSCRIBERS.delete(listener);
  };
}

function getOpenSnapshot(): boolean {
  return currentOpen;
}

function getOpenServerSnapshot(): boolean {
  return false;
}

function useExternalOpenState(): [boolean, (open: boolean) => void] {
  const open = useSyncExternalStore(subscribeOpen, getOpenSnapshot, getOpenServerSnapshot);
  const update = useCallback((next: boolean) => {
    if (next) openCommandPalette();
    else closeCommandPalette();
  }, []);
  return [open, update];
}

function score(haystack: string, needle: string): number {
  if (!needle) return 1;
  const lh = haystack.toLowerCase();
  const ln = needle.toLowerCase();
  if (lh === ln) return 1000;
  if (lh.startsWith(ln)) return 800;
  if (lh.includes(ln)) return 500;
  // Fuzzy: each char in needle must appear in haystack in order.
  let j = 0;
  for (let i = 0; i < lh.length && j < ln.length; i += 1) {
    if (lh[i] === ln[j]) j += 1;
  }
  return j === ln.length ? 100 : 0;
}

export function CommandPalette({
  getCommands,
  placeholder = 'Search commands, workspaces, tabs…',
}: CommandPaletteProps) {
  const [open, setOpen] = useExternalOpenState();
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const [lastOpen, setLastOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);

  // Reset query/cursor on each open transition (derived-state pattern — no setState in effect).
  if (open !== lastOpen) {
    setLastOpen(open);
    if (open) {
      setQuery('');
      setActiveIndex(0);
    }
  }

  // Global keyboard shortcut: Cmd/Ctrl+K toggles.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        toggleCommandPalette();
      } else if (event.key === 'Escape' && currentOpen) {
        event.preventDefault();
        closeCommandPalette();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // Focus input when opening.
  useEffect(() => {
    if (!open) return;
    const id = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.clearTimeout(id);
  }, [open]);

  // Commands computed only when open; otherwise empty array.
  const commands = useMemo<CommandItem[]>(() => (open ? getCommands() : []), [open, getCommands]);

  const filtered = useMemo(() => {
    const trimmed = query.trim();
    if (!trimmed) return commands;
    return commands
      .map((command) => {
        const text = `${command.label} ${command.description ?? ''} ${command.group ?? ''} ${command.keywords ?? ''}`;
        return { command, s: score(text, trimmed) };
      })
      .filter((entry) => entry.s > 0)
      .sort((a, b) => b.s - a.s)
      .map((entry) => entry.command);
  }, [commands, query]);

  // Group commands while preserving relevance order.
  const grouped = useMemo(() => {
    const seen = new Map<string, CommandItem[]>();
    for (const command of filtered) {
      const group = command.group ?? '';
      const bucket = seen.get(group);
      if (bucket) bucket.push(command);
      else seen.set(group, [command]);
    }
    return Array.from(seen.entries());
  }, [filtered]);

  const safeIndex = filtered.length === 0 ? 0 : Math.min(activeIndex, filtered.length - 1);

  const performAt = useCallback(
    async (index: number) => {
      const command = filtered[index];
      if (!command) return;
      closeCommandPalette();
      try {
        await command.perform();
      } catch (error) {
        console.error('[CommandPalette] command failed', error);
      }
    },
    [filtered],
  );

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActiveIndex((i) => (filtered.length === 0 ? 0 : (i + 1) % filtered.length));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActiveIndex((i) => (filtered.length === 0 ? 0 : (i - 1 + filtered.length) % filtered.length));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      void performAt(safeIndex);
    }
  };

  // Auto-scroll active row into view.
  useEffect(() => {
    if (!open || !listRef.current) return;
    const active = listRef.current.querySelector<HTMLElement>('[data-active="true"]');
    if (active) active.scrollIntoView({ block: 'nearest' });
  }, [open, safeIndex, filtered]);

  if (typeof document === 'undefined') return null;
  if (!open) return null;

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
      className="fixed inset-0 z-[200] flex items-start justify-center p-4 pt-[12vh] sm:pt-[15vh]"
    >
      <div
        aria-hidden
        onClick={() => setOpen(false)}
        className="absolute inset-0 bg-black/40 backdrop-blur-sm animate-in fade-in-0 duration-100"
      />
      <div className="relative z-10 flex w-full max-w-xl flex-col overflow-hidden rounded-xl border bg-popover text-popover-foreground shadow-2xl animate-in fade-in-0 zoom-in-95 slide-in-from-top-2 duration-150">
        <div className="flex items-center gap-2 border-b px-3">
          <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setActiveIndex(0);
            }}
            onKeyDown={onKeyDown}
            placeholder={placeholder}
            className="flex-1 bg-transparent py-3 text-sm outline-none placeholder:text-muted-foreground"
            aria-label="Command palette search"
            autoComplete="off"
            spellCheck={false}
          />
          <kbd className="hidden shrink-0 rounded border bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground sm:inline-block">
            Esc
          </kbd>
        </div>

        <div ref={listRef} className="max-h-[60vh] overflow-y-auto p-1">
          {filtered.length === 0 ? (
            <div className="px-3 py-8 text-center text-sm text-muted-foreground">
              No matches for &ldquo;{query}&rdquo;
            </div>
          ) : (
            grouped.map(([group, items]) => (
              <div key={group || 'default'} className="mb-1 last:mb-0">
                {group ? (
                  <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70">
                    {group}
                  </div>
                ) : null}
                {items.map((command) => {
                  const flatIndex = filtered.indexOf(command);
                  const isActive = flatIndex === safeIndex;
                  return (
                    <button
                      key={command.id}
                      type="button"
                      data-active={isActive}
                      onMouseEnter={() => setActiveIndex(flatIndex)}
                      onClick={() => void performAt(flatIndex)}
                      className={cn(
                        'flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm transition-colors',
                        isActive ? 'bg-primary text-primary-foreground' : 'hover:bg-muted',
                      )}
                    >
                      {command.icon ? (
                        <span className={cn('flex h-5 w-5 shrink-0 items-center justify-center', isActive ? 'text-primary-foreground' : 'text-muted-foreground')}>
                          {command.icon}
                        </span>
                      ) : (
                        <span className="h-5 w-5 shrink-0" />
                      )}
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium">{command.label}</span>
                        {command.description ? (
                          <span className={cn('block truncate text-xs', isActive ? 'text-primary-foreground/80' : 'text-muted-foreground')}>
                            {command.description}
                          </span>
                        ) : null}
                      </span>
                      {command.hint ? (
                        <kbd
                          className={cn(
                            'shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-medium',
                            isActive ? 'border-primary-foreground/30 text-primary-foreground' : 'border-border text-muted-foreground',
                          )}
                        >
                          {command.hint}
                        </kbd>
                      ) : null}
                      {isActive ? <CornerDownLeft className="h-3 w-3 shrink-0 opacity-70" /> : null}
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>

        <div className="flex items-center justify-between gap-3 border-t bg-muted/30 px-3 py-1.5 text-[11px] text-muted-foreground">
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-1">
              <kbd className="rounded border bg-background px-1.5 py-0.5 text-[10px] font-medium">↑↓</kbd>
              navigate
            </span>
            <span className="flex items-center gap-1">
              <kbd className="rounded border bg-background px-1.5 py-0.5 text-[10px] font-medium">↵</kbd>
              select
            </span>
          </div>
          <span className="tabular-nums">{filtered.length} result{filtered.length === 1 ? '' : 's'}</span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
