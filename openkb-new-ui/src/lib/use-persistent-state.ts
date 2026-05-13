'use client';

import { useCallback, useSyncExternalStore } from 'react';

type Updater<T> = T | ((prev: T) => T);

const channels = new Map<string, Set<() => void>>();

function subscribeKey(key: string, listener: () => void): () => void {
  let bucket = channels.get(key);
  if (!bucket) {
    bucket = new Set();
    channels.set(key, bucket);
  }
  bucket.add(listener);

  const onStorage = (event: StorageEvent) => {
    if (event.key === key) listener();
  };
  if (typeof window !== 'undefined') {
    window.addEventListener('storage', onStorage);
  }

  return () => {
    bucket?.delete(listener);
    if (bucket && bucket.size === 0) channels.delete(key);
    if (typeof window !== 'undefined') {
      window.removeEventListener('storage', onStorage);
    }
  };
}

function notifyKey(key: string) {
  const bucket = channels.get(key);
  if (!bucket) return;
  for (const listener of bucket) listener();
}

/**
 * Module-level snapshot cache.
 *
 * `useSyncExternalStore` requires `getSnapshot` to return the same reference when the
 * underlying data has not changed — otherwise React detects a new value on every render
 * and loops. Because `JSON.parse(raw)` always produces a fresh object/array (and callers
 * frequently pass fresh literals like `[]` as `initialValue`), we have to memoize per key.
 *
 * Cache key: storage key. Cache hit when the raw string is unchanged.
 * When the localStorage entry doesn't exist yet (`raw === null`), the very first call
 * stores the caller's `initialValue` and subsequent renders reuse that exact reference.
 */
interface CacheEntry {
  raw: string | null;
  value: unknown;
}
const SNAPSHOT_CACHE = new Map<string, CacheEntry>();

function readSnapshot<T>(
  key: string,
  initialValue: T,
  deserialize: (raw: string) => T,
): T {
  if (typeof window === 'undefined') return initialValue;
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(key);
  } catch {
    return initialValue;
  }
  const cached = SNAPSHOT_CACHE.get(key);
  if (cached && cached.raw === raw) {
    return cached.value as T;
  }
  let parsed: T;
  try {
    parsed = raw === null ? initialValue : deserialize(raw);
  } catch {
    parsed = initialValue;
  }
  SNAPSHOT_CACHE.set(key, { raw, value: parsed });
  return parsed;
}

/**
 * Persistent state synced to localStorage.
 *
 * - SSR safe: returns `initialValue` on the server, hydrates on first client read.
 * - Cross-tab sync via the `storage` event.
 * - Snapshot reference is cached so `useSyncExternalStore` doesn't infinite-loop on
 *   object/array values.
 */
export function usePersistentState<T>(
  key: string,
  initialValue: T,
  options?: { serialize?: (value: T) => string; deserialize?: (raw: string) => T },
): [T, (value: Updater<T>) => void] {
  const serialize = options?.serialize ?? JSON.stringify;
  const deserialize = options?.deserialize ?? (JSON.parse as (raw: string) => T);

  const subscribe = useCallback(
    (listener: () => void) => subscribeKey(key, listener),
    [key],
  );

  const getSnapshot = useCallback(
    (): T => readSnapshot(key, initialValue, deserialize),
    // initialValue is intentionally excluded — see readSnapshot: when raw is null we cache
    // the first-seen initialValue and ignore subsequent identity changes so React's
    // useSyncExternalStore sees a stable reference even when callers pass `[]` literals.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key],
  );

  const getServerSnapshot = useCallback((): T => initialValue, [initialValue]);

  const value = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  const update = useCallback(
    (next: Updater<T>) => {
      if (typeof window === 'undefined') return;
      try {
        const current = readSnapshot(key, initialValue, deserialize);
        const computed =
          typeof next === 'function' ? (next as (prev: T) => T)(current) : next;
        const serialized = serialize(computed);
        window.localStorage.setItem(key, serialized);
        // Eagerly refresh the cache so subscribers reading right now see the new value.
        SNAPSHOT_CACHE.set(key, { raw: serialized, value: computed });
        notifyKey(key);
      } catch {
        // quota / private mode — silently ignore
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key],
  );

  return [value, update];
}
