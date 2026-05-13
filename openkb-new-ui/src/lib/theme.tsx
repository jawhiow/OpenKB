'use client';

import { createContext, useCallback, useContext, useMemo, useSyncExternalStore } from 'react';
import { Moon, Sun, Monitor } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { usePersistentState } from '@/lib/use-persistent-state';

export type Theme = 'light' | 'dark' | 'system';
export type ResolvedTheme = 'light' | 'dark';

interface ThemeContextValue {
  theme: Theme;
  resolvedTheme: ResolvedTheme;
  setTheme: (theme: Theme) => void;
}

const STORAGE_KEY = 'openkb:theme';
const ThemeContext = createContext<ThemeContextValue | null>(null);

function subscribeMedia(listener: () => void): () => void {
  if (typeof window === 'undefined') return () => undefined;
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  media.addEventListener('change', listener);
  return () => media.removeEventListener('change', listener);
}

function getSystemTheme(): ResolvedTheme {
  if (typeof window === 'undefined') return 'light';
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function getServerSystemTheme(): ResolvedTheme {
  return 'light';
}

function useSystemTheme(): ResolvedTheme {
  return useSyncExternalStore(subscribeMedia, getSystemTheme, getServerSystemTheme);
}

function isValidTheme(value: unknown): value is Theme {
  return value === 'light' || value === 'dark' || value === 'system';
}

function deserializeTheme(raw: string): Theme {
  return isValidTheme(raw) ? raw : 'system';
}

function serializeTheme(value: Theme): string {
  return value;
}

function applyTheme(resolved: ResolvedTheme) {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  if (resolved === 'dark') root.classList.add('dark');
  else root.classList.remove('dark');
  root.style.colorScheme = resolved;
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = usePersistentState<Theme>(STORAGE_KEY, 'system', {
    serialize: serializeTheme,
    deserialize: deserializeTheme,
  });
  const systemTheme = useSystemTheme();
  const resolvedTheme: ResolvedTheme = theme === 'system' ? systemTheme : theme;

  // Side effect: keep DOM in sync. Reading on render so it tracks resolvedTheme without a setState-in-effect.
  if (typeof document !== 'undefined') {
    applyTheme(resolvedTheme);
  }

  const value = useMemo<ThemeContextValue>(
    () => ({ theme, resolvedTheme, setTheme }),
    [theme, resolvedTheme, setTheme],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    return {
      theme: 'system',
      resolvedTheme: 'light',
      setTheme: () => {
        /* noop */
      },
    };
  }
  return ctx;
}

/**
 * 3-state segmented toggle: light / system / dark.
 */
export function ThemeToggle({ className }: { className?: string }) {
  const { theme, setTheme } = useTheme();

  const options: Array<{ value: Theme; icon: React.ReactNode; label: string }> = [
    { value: 'light', icon: <Sun className="h-3.5 w-3.5" />, label: 'Light theme' },
    { value: 'system', icon: <Monitor className="h-3.5 w-3.5" />, label: 'System theme' },
    { value: 'dark', icon: <Moon className="h-3.5 w-3.5" />, label: 'Dark theme' },
  ];

  const handleClick = useCallback(
    (next: Theme) => () => setTheme(next),
    [setTheme],
  );

  return (
    <div
      role="radiogroup"
      aria-label="Color theme"
      className={cn(
        'inline-flex items-center gap-0.5 rounded-md border bg-muted/40 p-0.5',
        className,
      )}
    >
      {options.map((option) => {
        const isActive = theme === option.value;
        return (
          <Button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={isActive}
            aria-label={option.label}
            title={option.label}
            variant={isActive ? 'default' : 'ghost'}
            size="icon-xs"
            className={cn(!isActive && 'text-muted-foreground hover:text-foreground')}
            onClick={handleClick(option.value)}
          >
            {option.icon}
          </Button>
        );
      })}
    </div>
  );
}
