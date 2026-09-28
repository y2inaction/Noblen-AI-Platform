"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { useSession } from "@/components/session-context";
import { Badge, Card, Empty, ErrorBanner, Loading, PageHeader } from "@/components/ui";
import { money, short, titleCase, when } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Agent, Page, Run, Workflow, WorkflowRun } from "@/lib/types";

const AGENT_STATUSES = ["", "RUNNING", "QUEUED", "AWAITING_APPROVAL", "COMPLETED", "ESCALATED", "FAILED"];
const WORKFLOW_STATUSES = ["", "QUEUED", "RUNNING", "WAITING", "COMPLETED", "ESCALATED", "FAILED", "CANCELLED"];

function RunsView() {
  const router = useRouter();
  const params = useSearchParams();
  const { can } = useSession();
  const tab = params.get("tab") === "workflows" && can("workflow:view") ? "workflows" : "agents";
  const status = params.get("status") ?? "";
  const query = status ? `&status=${status}` : "";

  const agentRuns = useApi<Page<Run>>(tab === "agents" ? `/runs?limit=100${query}` : null);
  const agents = useApi<Page<Agent>>(tab === "agents" && can("agent:view") ? "/agents?limit=200" : null);
  const workflowRuns = useApi<Page<WorkflowRun>>(tab === "workflows" ? `/workflow-runs?limit=100${query}` : null);
  const workflows = useApi<Page<Workflow>>(tab === "workflows" ? "/workflows?limit=200" : null);

  const agentName = new Map((agents.data?.items ?? []).map((a) => [a.id, a.name]));
  const workflowName = new Map((workflows.data?.items ?? []).map((w) => [w.id, w.name]));

  function go(next: { tab?: string; status?: string }) {
    const search = new URLSearchParams();
    search.set("tab", next.tab ?? tab);
    if (next.status) search.set("status", next.status);
    router.replace(`/runs?${search.toString()}`);
  }

  const statuses = tab === "agents" ? AGENT_STATUSES : WORKFLOW_STATUSES;
  const loaded = tab === "agents" ? agentRuns : workflowRuns;

  return (
    <>
      <PageHeader title="Runs" description="Every agent and workflow execution, with a step-by-step trace." />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="flex rounded-lg border border-slate-300 bg-white p-0.5 text-sm">
          {(["agents", "workflows"] as const)
            .filter((t) => t === "agents" || can("workflow:view"))
            .map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => go({ tab: t })}
                className={`rounded-md px-3 py-1 ${tab === t ? "bg-noblen-600 text-white" : "text-slate-600"}`}
              >
                {t === "agents" ? "Agent runs" : "Workflow runs"}
              </button>
            ))}
        </div>
        <select
          aria-label="Status"
          value={status}
          onChange={(e) => go({ status: e.target.value })}
          className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm"
        >
          {statuses.map((s) => (
            <option key={s} value={s}>
              {s ? titleCase(s) : "All statuses"}
            </option>
          ))}
        </select>
      </div>
      <ErrorBanner message={loaded.error} />

      <Card>
        {loaded.loading && !loaded.data ? (
          <Loading />
        ) : tab === "agents" ? (
          (agentRuns.data?.items.length ?? 0) === 0 ? (
            <Empty title="No agent runs match" />
          ) : (
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-slate-400">
                <tr>
                  <th className="pb-2 font-medium">Run</th>
                  <th className="pb-2 font-medium">Agent</th>
                  <th className="pb-2 font-medium">Steps</th>
                  <th className="pb-2 font-medium">Cost</th>
                  <th className="pb-2 font-medium">Started</th>
                  <th className="pb-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {agentRuns.data!.items.map((r) => (
                  <tr key={r.id}>
                    <td className="py-2">
                      <Link href={`/runs/${r.id}`} className="font-mono text-xs text-noblen-700 hover:underline">
                        {short(r.id)}
                      </Link>
                    </td>
                    <td className="py-2 text-slate-700">{agentName.get(r.agent_id) ?? short(r.agent_id)}</td>
                    <td className="py-2 text-slate-600">
                      {r.model_calls} model · {r.tool_calls} tool
                    </td>
                    <td className="py-2 text-slate-600">{money(r.estimated_cost)}</td>
                    <td className="py-2 text-slate-500">{when(r.created_at)}</td>
                    <td className="py-2"><Badge value={r.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        ) : (workflowRuns.data?.items.length ?? 0) === 0 ? (
          <Empty title="No workflow runs match" />
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-slate-400">
              <tr>
                <th className="pb-2 font-medium">Run</th>
                <th className="pb-2 font-medium">Workflow</th>
                <th className="pb-2 font-medium">Trigger</th>
                <th className="pb-2 font-medium">Step</th>
                <th className="pb-2 font-medium">Started</th>
                <th className="pb-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {workflowRuns.data!.items.map((r) => (
                <tr key={r.id}>
                  <td className="py-2">
                    <Link href={`/workflow-runs/${r.id}`} className="font-mono text-xs text-noblen-700 hover:underline">
                      {short(r.id)}
                    </Link>
                  </td>
                  <td className="py-2 text-slate-700">{workflowName.get(r.workflow_id) ?? short(r.workflow_id)}</td>
                  <td className="py-2 text-slate-600">{titleCase(r.trigger_type)}</td>
                  <td className="py-2 font-mono text-xs text-slate-600">{r.current_step ?? "—"}</td>
                  <td className="py-2 text-slate-500">{when(r.created_at)}</td>
                  <td className="py-2"><Badge value={r.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
}

export default function RunsPage() {
  return (
    <Suspense fallback={<Loading />}>
      <RunsView />
    </Suspense>
  );
}
