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

export function loadSession(): Session | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

export function saveSession(session: Session): void {
  window.sessionStorage.setItem(KEY, JSON.stringify(session));
}

export function clearSession(): void {
  if (typeof window !== "undefined") window.sessionStorage.removeItem(KEY);
}
