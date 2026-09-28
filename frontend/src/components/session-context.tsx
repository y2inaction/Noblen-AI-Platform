"use client";

import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
} from "react";
import { API_BASE_URL } from "@/lib/api";
import { api } from "@/lib/client";
import {
  clearSession,
  loadSession,
  parseSession,
  readRawSession,
  saveSession,
  subscribeSession,
} from "@/lib/session";
import type { Access, Me } from "@/lib/types";

interface SessionContextValue {
  session: NonNullable<ReturnType<typeof parseSession>>;
  access: Access | null;
  me: Me | null;
  can: (permission: string) => boolean;
  switchOrganization: (organizationId: string) => void;
  signOut: () => Promise<void>;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside <SessionProvider>.");
  return value;
}

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  // Read sessionStorage as an external store: null during server rendering and
  // hydration, the stored session afterwards, and updated on save/clear.
  const raw = useSyncExternalStore(subscribeSession, readRawSession, () => null);
  const session = useMemo(() => parseSession(raw), [raw]);
  const organizationId = session?.organizationId ?? null;
  // Access is fetched per organization; a stale answer for another one is ignored.
  const [accessFor, setAccessFor] = useState<{ org: string; access: Access | null } | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const access = accessFor && accessFor.org === organizationId ? accessFor.access : null;

  useEffect(() => {
    // Check storage itself: during hydration `session` is still the server
    // snapshot (null) even when the person is signed in.
    if (!loadSession()) {
      const next = encodeURIComponent(window.location.pathname);
      router.replace(`/login?next=${next}`);
    }
  }, [session, router]);

  useEffect(() => {
    if (!organizationId) return;
    let alive = true;
    api<Access>("/organizations/current/access")
      .then((value) => alive && setAccessFor({ org: organizationId, access: value }))
      .catch(() => alive && setAccessFor({ org: organizationId, access: null }));
    api<Me>("/auth/me")
      .then((value) => alive && setMe(value))
      .catch(() => alive && setMe(null));
    return () => {
      alive = false;
    };
  }, [organizationId]);

  const can = useCallback(
    (permission: string) => Boolean(access?.permissions.includes(permission)),
    [access],
  );

  const switchOrganization = useCallback(
    (organizationId: string) => {
      const current = loadSession();
      if (!current || current.organizationId === organizationId) return;
      saveSession({ ...current, organizationId });
      router.push("/dashboard");
    },
    [router],
  );

  const signOut = useCallback(async () => {
    const current = loadSession();
    if (current) {
      // Revoke the refresh token server-side; ignore network errors.
      await fetch(`${API_BASE_URL}/api/v1/auth/logout`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: current.refreshToken }),
      }).catch(() => undefined);
    }
    clearSession();
    router.replace("/login");
  }, [router]);

  const value = useMemo(
    () =>
      session ? { session, access, me, can, switchOrganization, signOut } : null,
    [session, access, me, can, switchOrganization, signOut],
  );

  if (!value) {
    return <p className="p-10 text-center text-sm text-slate-400">Loading your workspace…</p>;
  }
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}
