"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useSession } from "@/components/session-context";
import { Badge, Button, Card, ErrorBanner, JsonView, Loading, PageHeader } from "@/components/ui";
import { api } from "@/lib/client";
import { short, titleCase, when } from "@/lib/format";
import { useAction, useApi } from "@/lib/hooks";
import type { WorkflowRunDetail } from "@/lib/types";

export default function WorkflowRunPage() {
  const { id } = useParams<{ id: string }>();
  const { can } = useSession();
  const run = useApi<WorkflowRunDetail>(`/workflow-runs/${id}`);
  const action = useAction();

  async function cancel() {
    const done = await action.run(() => api(`/workflow-runs/${id}/cancel`, { method: "POST" }));
    if (done) run.reload();
  }

  if (run.loading && !run.data) return <Loading />;
  if (!run.data) return <ErrorBanner message={run.error ?? "Workflow run not found."} />;
  const r = run.data;
  const cancellable = ["QUEUED", "WAITING"].includes(r.status) && can("agent:operate");

  return (
    <>
      <PageHeader
        title={`Workflow run ${short(r.id)}`}
        description={`${titleCase(r.trigger_type)} trigger · started ${when(r.started_at ?? r.created_at)}`}
        actions={
          <>
            <Badge value={r.status} />
            {r.status === "WAITING" && can("agent:approve_actions") && (
              <Link href="/approvals" className="text-sm text-noblen-700 hover:underline">Decide</Link>
            )}
            {cancellable && (
              <Button variant="danger" disabled={action.busy} onClick={cancel}>Cancel run</Button>
            )}
          </>
        }
      />
      <ErrorBanner message={action.error ?? (r.error ? `${r.error_code}: ${r.error}` : null)} />
      {r.next_attempt_at && r.status === "QUEUED" && (
        <p className="mb-4 rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800">
          Retrying step <code>{r.current_step}</code> (attempt {r.current_attempt}) {when(r.next_attempt_at)}.
        </p>
      )}
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <Card title="Steps" className="xl:col-span-2">
          <ol className="space-y-4">
            {r.steps.map((s) => (
              <li key={s.id} className="rounded-lg border border-slate-200 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium text-slate-800">
                    {s.sequence}. <code>{s.step_id}</code>
                  </span>
                  <span className="text-xs text-slate-500">{titleCase(s.step_type)}</span>
                  {s.attempt > 1 && <span className="text-xs text-slate-500">attempt {s.attempt}</span>}
                  <Badge value={s.status} />
                  {s.decision && <Badge value={s.decision.toUpperCase()} />}
                  {s.agent_run_id && (
                    <Link href={`/runs/${s.agent_run_id}`} className="ml-auto text-xs text-noblen-700 hover:underline">
                      Agent run {short(s.agent_run_id)}
                    </Link>
                  )}
                </div>
                {s.error && <p className="mt-1 text-sm text-red-700">{s.error}</p>}
                {s.decision_note && <p className="mt-1 text-sm text-slate-600">Note: {s.decision_note}</p>}
                {s.output && Object.keys(s.output).length > 0 && (
                  <details className="mt-2">
                    <summary className="cursor-pointer text-xs text-slate-500">Output</summary>
                    <div className="mt-1">
                      <JsonView value={s.output} />
                    </div>
                  </details>
                )}
              </li>
            ))}
            {r.steps.length === 0 && <p className="text-sm text-slate-500">Not started yet.</p>}
          </ol>
        </Card>
        <Card title="Input">
          <JsonView value={r.input} />
        </Card>
      </div>
    </>
  );
}
