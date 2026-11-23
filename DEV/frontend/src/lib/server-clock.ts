export type ServerClock = {
  /** Server time in epoch milliseconds, moved forward only by the monotonic timer. */
  now: () => number;
  /** Milliseconds until the deadline, never below zero. */
  remainingMs: (deadlineIso: string) => number;
};

/**
 * A clock anchored to a server timestamp. After this call it reads `performance.now()` only, so
 * changing the device clock, or the device clock being wrong, cannot move the deadline.
 */
export function serverClock(serverNowIso: string): ServerClock {
  const base = Date.parse(serverNowIso);
  const anchor = performance.now();
  const now = () => base + (performance.now() - anchor);
  return { now, remainingMs: (deadlineIso) => Math.max(0, Date.parse(deadlineIso) - now()) };
}

export function formatRemaining(ms: number): string {
  const total = Math.ceil(ms / 1000);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(Math.floor(total / 3600))}:${pad(Math.floor((total % 3600) / 60))}:${pad(total % 60)}`;
}
