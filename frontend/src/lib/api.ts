// Minimal typed API client for the Noblen backend.
// The base URL comes from a NEXT_PUBLIC_* variable (safe to expose); no secrets
// ever live in the frontend.

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export interface ApiError {
  code: string;
  message: string;
  details?: unknown;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface AuthResponse {
  user: {
    id: string;
    email: string;
    full_name: string | null;
    is_email_verified: boolean;
    is_superuser: boolean;
  };
  tokens: TokenPair;
  organization_id: string;
  role_name: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err: ApiError = body?.error ?? {
      code: "unknown",
      message: "Request failed",
    };
    throw new Error(err.message);
  }
  return body as T;
}

export function login(email: string, password: string): Promise<AuthResponse> {
  return request<AuthResponse>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function register(
  email: string,
  password: string,
  organization_name: string,
  full_name?: string,
): Promise<AuthResponse> {
  return request<AuthResponse>("/api/v1/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password, organization_name, full_name }),
  });
}
