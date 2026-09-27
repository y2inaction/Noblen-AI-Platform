"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useSession } from "@/components/session-context";
import { api } from "@/lib/client";
import { when } from "@/lib/format";
import type { Notification, Page } from "@/lib/types";

const NAV = [
  { href: "/dashboard", label: "Dashboard", permission: null },
  { href: "/workforce", label: "Workforce", permission: "agent:view" },
  { href: "/approvals", label: "Approvals", permission: "agent:approve_actions" },
  { href: "/runs", label: "Runs", permission: "run:view" },
  { href: "/workflows", label: "Workflows", permission: "workflow:view" },
  { href: "/tasks", label: "Tasks", permission: "task:view" },
  { href: "/integrations", label: "Integrations", permission: "integration:view" },
] as const;

function linkFor(n: Notification): string | null {
  const id = n.link?.id;
  switch (n.link?.type) {
    case "approval":
      return "/approvals";
    case "agent_run":
      return id ? `/runs/${id}` : "/runs";
    case "workflow_run":
      return id ? `/workflow-runs/${id}` : "/runs?tab=workflows";
    case "task":
      return "/tasks";
    default:
      return null;
  }
}

function Notifications() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Notification[]>([]);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api<Page<Notification>>("/notifications?limit=20")
        .then((page) => alive && setItems(page.items))
        .catch(() => undefined);
    load();
    const timer = window.setInterval(load, 30_000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  const unread = items.filter((n) => !n.read_at).length;

  async function openItem(n: Notification) {
    if (!n.read_at) {
      await api(`/notifications/${n.id}/read`, { method: "POST" }).catch(() => undefined);
      setItems((prev) => prev.map((x) => (x.id === n.id ? { ...x, read_at: new Date().toISOString() } : x)));
    }
    const target = linkFor(n);
    setOpen(false);
    if (target) router.push(target);
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="relative rounded-lg px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100"
        aria-label={`Notifications (${unread} unread)`}
      >
        Notifications
        {unread > 0 && (
          <span className="ml-1.5 rounded-full bg-noblen-600 px-1.5 py-0.5 text-xs font-semibold text-white">
            {unread}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 z-20 mt-2 w-80 rounded-xl border border-slate-200 bg-white shadow-lg">
          <ul className="max-h-96 divide-y divide-slate-100 overflow-auto">
            {items.length === 0 && <li className="p-4 text-sm text-slate-500">Nothing yet.</li>}
            {items.map((n) => (
              <li key={n.id}>
                <button
                  type="button"
                  onClick={() => openItem(n)}
                  className={`block w-full px-4 py-3 text-left hover:bg-slate-50 ${n.read_at ? "" : "bg-noblen-50/60"}`}
                >
                  <p className="text-sm font-medium text-slate-800">{n.title}</p>
                  {n.body && <p className="mt-0.5 line-clamp-2 text-xs text-slate-500">{n.body}</p>}
                  <p className="mt-1 text-xs text-slate-400">{when(n.created_at)}</p>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { session, access, me, can, switchOrganization, signOut } = useSession();
  const [pending, setPending] = useState<number | null>(null);

  useEffect(() => {
    if (!can("agent:approve_actions")) return;
    api<Page<unknown>>("/approvals?status=PENDING&limit=1")
      .then((page) => setPending(page.total))
      .catch(() => setPending(null));
  }, [can, pathname]);

  const memberships = (me?.memberships ?? []).filter((m) => m.status === "ACTIVE");

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-slate-200 bg-white lg:flex">
        <div className="flex items-center gap-2 border-b border-slate-200 px-5 py-4">
          <div className="h-8 w-8 rounded-lg bg-noblen-600" aria-hidden />
          <span className="font-semibold text-noblen-900">Noblen AI</span>
        </div>
        <nav className="flex-1 space-y-0.5 px-3 py-4" aria-label="Main">
          {NAV.filter((item) => item.permission === null || can(item.permission)).map((item) => {
            const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex items-center justify-between rounded-lg px-3 py-2 text-sm ${
                  active ? "bg-noblen-50 font-medium text-noblen-800" : "text-slate-700 hover:bg-slate-50"
                }`}
              >
                {item.label}
                {item.href === "/approvals" && pending ? (
                  <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-800">
                    {pending}
                  </span>
                ) : null}
              </Link>
            );
          })}
        </nav>
        <div className="border-t border-slate-200 px-5 py-4 text-xs text-slate-500">
          <p className="truncate font-medium text-slate-700">{session.user.email}</p>
          <p>{access ? access.role_name : "…"}</p>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-white px-6 py-3">
          <div className="flex items-center gap-2 text-sm">
            {memberships.length > 1 ? (
              <select
                aria-label="Organization"
                value={session.organizationId}
                onChange={(e) => switchOrganization(e.target.value)}
                className="rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
              >
                {memberships.map((m) => (
                  <option key={m.organization_id} value={m.organization_id}>
                    {m.organization_name ?? m.organization_id} ({m.role_name})
                  </option>
                ))}
              </select>
            ) : (
              <span className="font-medium text-slate-800">{access?.organization_name ?? "…"}</span>
            )}
          </div>
          <div className="flex items-center gap-1">
            <Notifications />
            <button
              type="button"
              onClick={() => signOut()}
              className="rounded-lg px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100"
            >
              Sign out
            </button>
          </div>
        </header>
        <nav className="flex gap-1 overflow-x-auto border-b border-slate-200 bg-white px-4 py-2 lg:hidden" aria-label="Sections">
          {NAV.filter((item) => item.permission === null || can(item.permission)).map((item) => (
            <Link key={item.href} href={item.href} className="whitespace-nowrap rounded-lg px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50">
              {item.label}
            </Link>
          ))}
        </nav>
        <main className="flex-1 p-6">{children}</main>
      </div>
    </div>
  );
}
