'use client';

/**
 * Tiny wrapper around the Web Notification API.
 *
 * Design notes:
 * - **Silent fail** on unsupported browsers (Safari iOS, sandboxed iframes, etc.).
 * - **Only fires when the tab is hidden** — if the user is already looking at the page,
 *   the toast is enough and a desktop popup would be redundant.
 * - Permission is requested lazily on the first attempt so we don't ambush the user on
 *   page load. The browser itself requires a user gesture, so this only succeeds when
 *   triggered downstream of a click (e.g. user just started a long-running job).
 */
export function isNotificationSupported(): boolean {
  return typeof window !== 'undefined' && 'Notification' in window;
}

export function getNotificationPermission(): NotificationPermission | null {
  if (!isNotificationSupported()) return null;
  return Notification.permission;
}

/**
 * Send a desktop notification when the page is not visible. No-ops when:
 *   - the API is unsupported,
 *   - permission has been denied,
 *   - the user is currently looking at the tab.
 *
 * On first attempt with `default` permission, kicks off a `requestPermission()` and
 * sends the notification when granted. Subsequent calls reuse the cached permission.
 */
export async function notifyIfBackground(
  title: string,
  options?: NotificationOptions,
): Promise<void> {
  if (!isNotificationSupported()) return;
  if (typeof document !== 'undefined' && !document.hidden) return;

  let permission = Notification.permission;
  if (permission === 'denied') return;
  if (permission === 'default') {
    try {
      permission = await Notification.requestPermission();
    } catch {
      return;
    }
  }
  if (permission !== 'granted') return;

  try {
    const notification = new Notification(title, {
      icon: '/favicon.ico',
      badge: '/favicon.ico',
      ...options,
    });
    // Focus the originating window when the user clicks the notification.
    notification.onclick = () => {
      try {
        window.focus();
        notification.close();
      } catch {
        /* ignore */
      }
    };
  } catch {
    /* fail silently — some browsers throw when icon is missing or in iframes */
  }
}
