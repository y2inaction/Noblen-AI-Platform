// Session-aware API client: bearer token, active organization, one refresh retry.

import { API_BASE_URL } from "@/lib/api";
import { clearSession, loadSession, saveSession } from "@/lib/session";

export class ApiRequestError extends Error {
  constructor(
    message: string,
    public status: number,
    public code: string,
  ) {
    super(message);
  }
}

let refreshing: Promise<boolean> | null = null;

async function refresh(): Promise<boolean> {
  const session = loadSession();
  if (!session) return false;
  refreshing ??= (async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: session.refreshToken }),
      });
      if (!res.ok) return false;
      const tokens = (await res.json()) as { access_token: string; refresh_token: string };
      saveSession({
        ...session,
        accessToken: tokens.access_token,
        refreshToken: tokens.refresh_token,
      });
      return true;
    } catch {
      return false;
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

function signOutAndRedirect(): void {
  clearSession();
  if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    window.location.assign(`/login?next=${next}`);
  }
}

export interface RequestOptions {
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
}

export async function api<T>(path: string, options: RequestOptions = {}, retried = false): Promise<T> {
  const session = loadSession();
  if (!session) {
    signOutAndRedirect();
    throw new ApiRequestError("Not signed in.", 401, "unauthenticated");
  }
  const res = await fetch(`${API_BASE_URL}/api/v1${path}`, {
    method: options.method ?? "GET",
    signal: options.signal,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${session.accessToken}`,
      "X-Organization-Id": session.organizationId,
    },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  if (res.status === 401 && !retried) {
    if (await refresh()) return api<T>(path, options, true);
    signOutAndRedirect();
  }
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = (body as { error?: { code?: string; message?: string } }).error;
    throw new ApiRequestError(err?.message ?? `Request failed (${res.status}).`, res.status, err?.code ?? "error");
  }
  return body as T;
}
