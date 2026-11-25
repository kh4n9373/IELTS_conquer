import type { GoogleResult } from './api';

/** The verified Google identity waiting for its first-time setup. Kept per tab so a refresh survives. */
export type Pending = Extract<GoogleResult, { next: 'onboarding' }> & { inviteCode?: string };

const KEY = 'panxg.onboarding';

export function savePending(value: Pending) {
  sessionStorage.setItem(KEY, JSON.stringify(value));
}

export function loadPending(): Pending | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Pending) : null;
  } catch {
    return null;
  }
}

export function clearPending() {
  sessionStorage.removeItem(KEY);
}
