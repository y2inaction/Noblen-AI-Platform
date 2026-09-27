"use client";

import Link from "next/link";
import { useSession } from "@/components/session-context";
import { Badge, Card, Empty, ErrorBanner, Loading, PageHeader, Stat } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { money, percent, short, when } from "@/lib/format";
import type { Overview, Page, Run, Task } from "@/lib/types";

export default function DashboardPage() {
  const { can, access } = useSession();
  const overview = useApi<Overview>(can("operations:view") ? "/operations/overview?window_days=7" : null);
  const runs = useApi<Page<Run>>(can("run:view") ? "/runs?limit=8" : null);
  const tasks = useApi<Page<Task>>(can("task:view") ? "/tasks?open_only=true&mine=true&limit=8" : null);
  const o = overview.data;

  return (
    <>
      <PageHeader
        title={`Good day${access ? `, ${access.organization_name}` : ""}`}
        description="How your AI workforce is doing over the last 7 days, and what needs you."
      />
      <ErrorBanner message={overview.error} />

      {can("operations:view") && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Stat
            label="Waiting for a decision"
            value={o ? o.pending_approvals : "—"}
            hint={can("agent:approve_actions") ? "Open the approvals inbox" : undefined}
          />
          <Stat label="Agent runs (7d)" value={o ? o.runs_total : "—"} hint={o ? `${percent(o.success_rate)} completed` : undefined} />
          <Stat
            label="Escalated to a person"
            value={o ? percent(o.escalation_rate) : "—"}
            hint={o ? `${percent(o.failure_rate)} failed` : undefined}
          />
          <Stat
            label="Model spend (7d)"
            value={o ? money(o.estimated_cost) : "—"}
            hint={o ? `${(o.input_tokens + o.output_tokens).toLocaleString()} tokens` : undefined}
          />
        </div>
      )}

      <div className="mt-6 grid grid-cols-1 gap-6 xl:grid-cols-3">
        {can("run:view") && (
          <Card
            className="xl:col-span-2"
            title="Recent agent runs"
            action={<Link href="/runs" className="text-sm text-noblen-700 hover:underline">All runs</Link>}
          >
            {runs.loading && !runs.data ? (
              <Loading />
            ) : runs.data && runs.data.items.length > 0 ? (
              <ul className="divide-y divide-slate-100">
                {runs.data.items.map((run) => (
                  <li key={run.id} className="flex items-center justify-between gap-3 py-2.5">
                    <Link href={`/runs/${run.id}`} className="text-sm text-slate-700 hover:text-noblen-700">
                      Run {short(run.id)} · {run.tool_calls} tool call{run.tool_calls === 1 ? "" : "s"}
                      {run.escalation_reason ? ` · ${run.escalation_reason}` : ""}
                    </Link>
                    <span className="flex shrink-0 items-center gap-3 text-xs text-slate-400">
                      {when(run.created_at)} <Badge value={run.status} />
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <Empty title="No runs yet">
                Start with a ready-made agent in <Link href="/workforce" className="text-noblen-700 hover:underline">Workforce</Link>.
              </Empty>
            )}
          </Card>
        )}

        {can("task:view") && (
          <Card
            title="My open tasks"
            action={<Link href="/tasks" className="text-sm text-noblen-700 hover:underline">All tasks</Link>}
          >
            {tasks.loading && !tasks.data ? (
              <Loading />
            ) : tasks.data && tasks.data.items.length > 0 ? (
              <ul className="space-y-2">
                {tasks.data.items.map((task) => (
                  <li key={task.id} className="flex items-start justify-between gap-2 text-sm">
                    <span className="text-slate-700">{task.title}</span>
                    <Badge value={task.priority} tone={task.priority === "URGENT" || task.priority === "HIGH" ? "red" : "slate"} />
                  </li>
                ))}
              </ul>
            ) : (
              <Empty title="Nothing assigned to you" />
            )}
          </Card>
        )}

        {o && o.recent_escalations.length > 0 && (
          <Card title="Recent escalations" className="xl:col-span-2">
            <ul className="divide-y divide-slate-100">
              {o.recent_escalations.map((e) => (
                <li key={String(e.run_id)} className="flex items-center justify-between gap-3 py-2.5 text-sm">
                  <Link href={`/runs/${String(e.run_id)}`} className="text-slate-700 hover:text-noblen-700">
                    {String(e.reason ?? "Escalated")}
                  </Link>
                  <span className="shrink-0 text-xs text-slate-400">{when(e.at as string | null)}</span>
                </li>
              ))}
            </ul>
          </Card>
        )}

        {o && (
          <Card title="Workforce">
            <dl className="space-y-2 text-sm">
              {Object.entries(o.agents_by_status).map(([status, count]) => (
                <div key={status} className="flex items-center justify-between">
                  <dt><Badge value={status} /></dt>
                  <dd className="font-medium text-slate-800">{count}</dd>
                </div>
              ))}
              {Object.keys(o.agents_by_status).length === 0 && <Empty title="No agents yet" />}
              <div className="flex items-center justify-between border-t border-slate-100 pt-2 text-slate-500">
                <dt>Tool calls (7d)</dt>
                <dd>
                  {o.tool_calls} · {o.tool_denials} denied · {o.tool_failures} failed
                </dd>
              </div>
            </dl>
          </Card>
        )}
      </div>
    </>
  );
}
