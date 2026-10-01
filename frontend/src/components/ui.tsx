import type { ReactNode } from "react";
import { titleCase } from "@/lib/format";
import type { WithheldReason } from "@/lib/types";

const TONES: Record<string, string> = {
  green: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  amber: "bg-amber-50 text-amber-800 ring-amber-200",
  orange: "bg-orange-50 text-orange-700 ring-orange-200",
  red: "bg-red-50 text-red-700 ring-red-200",
  blue: "bg-noblen-50 text-noblen-700 ring-noblen-200",
  slate: "bg-slate-100 text-slate-600 ring-slate-200",
};

const STATUS_TONE: Record<string, string> = {
  ACTIVE: "green",
  COMPLETED: "green",
  SUCCEEDED: "green",
  APPROVED: "green",
  DONE: "green",
  LOW: "green",
  AWAITING_APPROVAL: "amber",
  WAITING: "amber",
  PENDING: "amber",
  QUEUED: "amber",
  OPEN: "amber",
  MEDIUM: "amber",
  ESCALATED: "orange",
  HIGH: "red",
  FAILED: "red",
  REJECTED: "red",
  DENIED: "red",
  EXPIRED: "red",
  RUNNING: "blue",
  IN_PROGRESS: "blue",
};

export function Badge({ value, tone }: { value: string; tone?: string }) {
  const color = TONES[tone ?? STATUS_TONE[value] ?? "slate"];
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${color}`}
    >
      {titleCase(value)}
    </span>
  );
}

export function Card({
  title,
  action,
  children,
  className = "",
}: {
  title?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-xl border border-slate-200 bg-white shadow-sm ${className}`}>
      {(title || action) && (
        <header className="flex items-center justify-between gap-3 border-b border-slate-100 px-5 py-3">
          <h2 className="text-sm font-semibold text-slate-800">{title}</h2>
          {action}
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <p className="text-sm text-slate-500">{label}</p>
      <p className="mt-2 text-2xl font-bold text-slate-900">{value}</p>
      {hint && <p className="mt-1 text-xs text-slate-400">{hint}</p>}
    </div>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">{title}</h1>
        {description && <p className="mt-1 max-w-2xl text-sm text-slate-500">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger" | "ghost";
};

export function Button({ variant = "secondary", className = "", ...props }: ButtonProps) {
  const styles = {
    primary: "bg-noblen-600 text-white hover:bg-noblen-700",
    secondary: "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50",
    danger: "bg-red-600 text-white hover:bg-red-700",
    ghost: "text-noblen-700 hover:bg-noblen-50",
  }[variant];
  return (
    <button
      type="button"
      {...props}
      className={`rounded-lg px-3 py-1.5 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50 ${styles} ${className}`}
    />
  );
}

export function ErrorBanner({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
      {message}
    </p>
  );
}

const WITHHELD_MESSAGES: Record<WithheldReason, string> = {
  restricted_sources:
    "Some of this run's content comes from sources you don't currently have access to.",
  unknown_provenance:
    "This run's sources can't be verified, so its content is shown only to the person it acted for.",
};

/** Why a run's content is hidden from you. Never names the source involved. */
export function WithheldNotice({
  reason,
  children,
}: {
  reason: WithheldReason | null;
  children?: ReactNode;
}) {
  return (
    <p className="mb-4 rounded-lg bg-slate-100 px-4 py-3 text-sm text-slate-700">
      {WITHHELD_MESSAGES[reason ?? "unknown_provenance"]}
      {children ? <> {children}</> : null}
    </p>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return <p className="py-6 text-center text-sm text-slate-400">{label}</p>;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-slate-300 px-6 py-8 text-center">
      <p className="text-sm font-medium text-slate-700">{title}</p>
      {children && <div className="mt-1 text-sm text-slate-500">{children}</div>}
    </div>
  );
}

/** Data shown as text, never as HTML (it may come from models or external systems). */
export function JsonView({ value }: { value: unknown }) {
  return (
    <pre className="max-h-72 overflow-auto rounded-lg bg-slate-900 p-3 text-xs leading-relaxed text-slate-100">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="font-medium text-slate-700">{label}</span>
      <div className="mt-1">{children}</div>
    </label>
  );
}

export const inputClass =
  "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-noblen-500 focus:outline-none focus:ring-2 focus:ring-noblen-200";
