"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { useSession } from "@/components/session-context";
import { Badge, Button, Card, Empty, ErrorBanner, Loading, PageHeader, inputClass } from "@/components/ui";
import { api } from "@/lib/client";
import { short, titleCase, when } from "@/lib/format";
import { useAction, useApi } from "@/lib/hooks";
import type { Agent, ExecutionResult, Page, Run } from "@/lib/types";

export default function AgentPage() {
  const { id } = useParams<{ id: string }>();
  const { can } = useSession();
  const agent = useApi<Agent>(`/agents/${id}`);
  const runs = useApi<Page<Run>>(can("run:view") ? `/runs?agent_id=${id}&limit=20` : null);
  const [message, setMessage] = useState("");
  const [background, setBackground] = useState(false);
  const [result, setResult] = useState<ExecutionResult | null>(null);
  const action = useAction();

  async function run(e: React.FormEvent) {
    e.preventDefault();
    const res = await action.run(() =>
      api<ExecutionResult>(`/agents/${id}/execute`, { method: "POST", body: { message, background } }),
    );
    if (res) {
      setResult(res);
      setMessage("");
      runs.reload();
    }
  }

  if (agent.loading && !agent.data) return <Loading />;
  if (!agent.data) return <ErrorBanner message={agent.error ?? "Agent not found."} />;
  const a = agent.data;

  return (
    <>
      <PageHeader
        title={a.name}
        description={a.description ?? undefined}
        actions={<Badge value={a.status} />}
      />
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <div className="space-y-6 xl:col-span-2">
          {can("agent:run") && (
            <Card title="Give it a task">
              {a.status !== "ACTIVE" ? (
                <p className="text-sm text-slate-500">This agent is {a.status.toLowerCase()}; activate it to run tasks.</p>
              ) : (
                <form onSubmit={run} className="space-y-3">
                  <textarea
                    required
                    rows={4}
                    maxLength={20000}
                    value={message}
                    onChange={(e) => setMessage(e.target.value)}
                    placeholder="e.g. Brief me on what is overdue this week."
                    className={inputClass}
                  />
                  <div className="flex items-center justify-between">
                    <label className="flex items-center gap-2 text-sm text-slate-600">
                      <input type="checkbox" checked={background} onChange={(e) => setBackground(e.target.checked)} />
                      Run in the background
                    </label>
                    <Button type="submit" variant="primary" disabled={action.busy || !message.trim()}>
                      {action.busy ? "Working…" : "Run"}
                    </Button>
                  </div>
                  <ErrorBanner message={action.error} />
                </form>
              )}
              {result && (
                <div className="mt-4 space-y-2 rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm">
                  <div className="flex items-center justify-between">
                    <Badge value={result.status.toUpperCase()} />
                    {result.run_id && (
                      <Link href={`/runs/${result.run_id}`} className="text-noblen-700 hover:underline">
                        View trace
                      </Link>
                    )}
                  </div>
                  {result.message && <p className="whitespace-pre-wrap text-slate-800">{result.message.content}</p>}
                  {result.status === "awaiting_approval" && (
                    <p className="text-slate-700">
                      The agent wants to use <strong>{result.tool_name}</strong> and is waiting for a person.{" "}
                      <Link href="/approvals" className="text-noblen-700 hover:underline">Open approvals</Link>
                    </p>
                  )}
                  {result.status === "escalated" && (
                    <p className="text-slate-700">Handed to a person: {result.escalation_reason}</p>
                  )}
                  {result.status === "queued" && <p className="text-slate-700">Queued for a worker.</p>}
                </div>
              )}
            </Card>
          )}

          {can("run:view") && (
            <Card title="Recent runs">
              {runs.data && runs.data.items.length > 0 ? (
                <ul className="divide-y divide-slate-100">
                  {runs.data.items.map((r) => (
                    <li key={r.id} className="flex items-center justify-between py-2.5 text-sm">
                      <Link href={`/runs/${r.id}`} className="text-slate-700 hover:text-noblen-700">
                        Run {short(r.id)} · {r.model_calls} model / {r.tool_calls} tool calls
                      </Link>
                      <span className="flex items-center gap-3 text-xs text-slate-400">
                        {when(r.created_at)} <Badge value={r.status} />
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <Empty title="No runs yet" />
              )}
            </Card>
          )}
        </div>

        <Card title="Configuration">
          <dl className="space-y-3 text-sm">
            <div>
              <dt className="text-slate-500">Type</dt>
              <dd className="text-slate-800">{titleCase(a.agent_type)}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Memory</dt>
              <dd className="text-slate-800">{titleCase(a.memory_mode)}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Model</dt>
              <dd className="text-slate-800">{a.default_model ?? "Organization default"}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Active version</dt>
              <dd className="font-mono text-xs text-slate-800">{short(a.active_version_id)}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Created</dt>
              <dd className="text-slate-800">{when(a.created_at)}</dd>
            </div>
          </dl>
        </Card>
      </div>
    </>
  );
}
