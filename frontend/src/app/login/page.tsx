"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { login, register, type AuthResponse } from "@/lib/api";
import { safeNext } from "@/lib/redirect";
import { saveSession } from "@/lib/session";

const input =
  "mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 focus:border-noblen-500 focus:outline-none focus:ring-2 focus:ring-noblen-200";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [organization, setOrganization] = useState("");
  const [fullName, setFullName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const auth: AuthResponse =
        mode === "signin"
          ? await login(email, password)
          : await register(email, password, organization, fullName || undefined);
      saveSession({
        accessToken: auth.tokens.access_token,
        refreshToken: auth.tokens.refresh_token,
        organizationId: auth.organization_id,
        user: { id: auth.user.id, email: auth.user.email, full_name: auth.user.full_name },
      });
      router.replace(safeNext(params.get("next"), window.location.origin));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-8 shadow-sm">
        <div className="mb-6 flex items-center gap-2">
          <div className="h-8 w-8 rounded-lg bg-noblen-600" aria-hidden />
          <span className="text-lg font-semibold text-noblen-900">Noblen AI</span>
        </div>
        <h1 className="text-2xl font-bold text-slate-900">
          {mode === "signin" ? "Sign in" : "Create your organization"}
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          {mode === "signin"
            ? "Access your AI operating environment."
            : "You will be its owner and can invite your team."}
        </p>

        <form onSubmit={onSubmit} className="mt-6 space-y-4">
          {mode === "signup" && (
            <>
              <label className="block text-sm font-medium text-slate-700">
                Organization name
                <input
                  required
                  minLength={2}
                  value={organization}
                  onChange={(e) => setOrganization(e.target.value)}
                  className={input}
                />
              </label>
              <label className="block text-sm font-medium text-slate-700">
                Your name
                <input value={fullName} onChange={(e) => setFullName(e.target.value)} className={input} />
              </label>
            </>
          )}
          <label className="block text-sm font-medium text-slate-700">
            Email
            <input
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className={input}
            />
          </label>
          <label className="block text-sm font-medium text-slate-700">
            Password
            <input
              type="password"
              required
              minLength={mode === "signup" ? 8 : undefined}
              autoComplete={mode === "signin" ? "current-password" : "new-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={input}
            />
          </label>

          {error && (
            <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-lg bg-noblen-600 px-4 py-2.5 font-medium text-white hover:bg-noblen-700 disabled:opacity-60"
          >
            {loading ? "Please wait…" : mode === "signin" ? "Sign in" : "Create organization"}
          </button>
        </form>

        <p className="mt-6 text-center text-sm text-slate-500">
          {mode === "signin" ? "New to Noblen AI? " : "Already have an account? "}
          <button
            type="button"
            className="text-noblen-700 hover:underline"
            onClick={() => {
              setMode(mode === "signin" ? "signup" : "signin");
              setError(null);
            }}
          >
            {mode === "signin" ? "Create an organization" : "Sign in"}
          </button>
        </p>
        <p className="mt-2 text-center text-sm text-slate-500">
          <Link href="/" className="text-noblen-700 hover:underline">
            ← Back home
          </Link>
        </p>
      </div>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}
