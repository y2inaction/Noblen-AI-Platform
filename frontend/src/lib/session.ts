// Browser session for the operating environment (Noblen AI 3.0, M7).
//
// Tokens live in sessionStorage: they are gone when the tab closes and are not
// shared across tabs. They are still readable by scripts on this origin, which
// is why the app ships strict security headers (next.config.mjs) and never
// renders HTML from API data. The server enforces every permission; what the UI
// hides is a convenience, not a security boundary.

export interface SessionUser {
  id: string;
  email: string;
  full_name: string | null;
}

export interface Session {
  accessToken: string;
  refreshToken: string;
  organizationId: string;
  user: SessionUser;
}

const KEY = "noblen.session";
const CHANGED = "noblen:session-changed";

/** The stored session as a string (stable for useSyncExternalStore snapshots). */
export function readRawSession(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function parseSession(raw: string | null): Session | null {
  if (!raw) return null;
  try {
    return JSON.parse(raw) as Session;
  } catch {
    return null;
  }
}

export function loadSession(): Session | null {
  return parseSession(readRawSession());
}

function changed(): void {
  window.dispatchEvent(new Event(CHANGED));
}

export function saveSession(session: Session): void {
  window.sessionStorage.setItem(KEY, JSON.stringify(session));
  changed();
}

export function clearSession(): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.removeItem(KEY);
  changed();
}

/** Subscribe to session changes in this tab (useSyncExternalStore). */
export function subscribeSession(callback: () => void): () => void {
  window.addEventListener(CHANGED, callback);
  return () => window.removeEventListener(CHANGED, callback);
}
