"use client";

import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { API_BASE_URL } from "@/lib/api";
import { api } from "@/lib/client";
import { clearSession, loadSession, saveSession, type Session } from "@/lib/session";
import type { Access, Me } from "@/lib/types";

interface SessionContextValue {
  session: Session;
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
  const [session, setSession] = useState<Session | null>(null);
  const [access, setAccess] = useState<Access | null>(null);
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    const stored = loadSession();
    if (!stored) {
      const next = encodeURIComponent(window.location.pathname);
      router.replace(`/login?next=${next}`);
      return;
    }
    setSession(stored);
  }, [router]);

  useEffect(() => {
    if (!session) return;
    setAccess(null);
    api<Access>("/organizations/current/access").then(setAccess).catch(() => setAccess(null));
    api<Me>("/auth/me").then(setMe).catch(() => setMe(null));
  }, [session]);

  const can = useCallback(
    (permission: string) => Boolean(access?.permissions.includes(permission)),
    [access],
  );

  const switchOrganization = useCallback(
    (organizationId: string) => {
      const current = loadSession();
      if (!current || current.organizationId === organizationId) return;
      const updated = { ...current, organizationId };
      saveSession(updated);
      setSession(updated);
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
