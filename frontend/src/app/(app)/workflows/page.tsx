"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useSession } from "@/components/session-context";
import { Badge, Button, Card, Empty, ErrorBanner, Field, Loading, PageHeader, inputClass } from "@/components/ui";
import { api } from "@/lib/client";
import { titleCase, when } from "@/lib/format";
import { useAction, useApi } from "@/lib/hooks";
import type { Page, Workflow, WorkflowRun } from "@/lib/types";

const EXAMPLE = JSON.stringify(
  {
    trigger: { type: "manual" },
    steps: [
      { id: "open", type: "tool", tool: "list_tasks", arguments: { open_only: true } },
      {
        id: "any",
        type: "condition",
        left: "{{ steps.open.output.total }}",
        op: "gt",
        right: 0,
        then: "review",
        else: "end",
      },
      { id: "review", type: "approval", title: "Create a review task for {{ steps.open.output.total }} open items?" },
      { id: "log", type: "tool", tool: "create_task", arguments: { title: "Weekly review" } },
    ],
  },
  null,
  2,
);

function NewWorkflow({ onCreated }: { onCreated: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [definition, setDefinition] = useState(EXAMPLE);
  const action = useAction();

  async function create(e: React.FormEvent) {
    e.preventDefault();
    let parsed: unknown;
    try {
      parsed = JSON.parse(definition);
    } catch {
      action.setError("The definition is not valid JSON.");
      return;
    }
    const done = await action.run(() => api("/workflows", { method: "POST", body: { name, definition: parsed } }));
    if (done) {
      setOpen(false);
      setName("");
      onCreated();
    }
  }

  if (!open) {
    return (
      <Button variant="primary" onClick={() => setOpen(true)}>
        New workflow
      </Button>
    );
  }
  return (
    <Card title="New workflow" className="mb-6 w-full">
      <form onSubmit={create} className="space-y-3">
        <Field label="Name">
          <input required maxLength={160} value={name} onChange={(e) => setName(e.target.value)} className={inputClass} />
        </Field>
        <Field label="Definition (JSON)">
          <textarea
            rows={16}
            value={definition}
            onChange={(e) => setDefinition(e.target.value)}
            className={`${inputClass} font-mono text-xs`}
          />
        </Field>
        <p className="text-xs text-slate-500">
          Steps: agent, tool, condition, approval. Triggers: manual, schedule, event, webhook. The definition is
          validated when saved. It starts as a draft.
        </p>
        <ErrorBanner message={action.error} />
        <div className="flex gap-2">
          <Button type="submit" variant="primary" disabled={action.busy}>Save draft</Button>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
        </div>
      </form>
    </Card>
  );
}

function WorkflowRow({ workflow, onChange }: { workflow: Workflow; onChange: () => void }) {
  const router = useRouter();
  const { can } = useSession();
  const [input, setInput] = useState("{}");
  const [running, setRunning] = useState(false);
  const [token, setToken] = useState<{ token: string; path: string } | null>(null);
  const action = useAction();

  async function lifecycle(verb: "activate" | "pause") {
    const updated = await action.run(() => api<Workflow>(`/workflows/${workflow.id}/${verb}`, { method: "POST" }));
    if (updated?.webhook_token && updated.webhook_path) {
      setToken({ token: updated.webhook_token, path: updated.webhook_path });
    }
    if (updated) onChange();
  }

  async function start() {
    let parsed: unknown;
    try {
      parsed = JSON.parse(input || "{}");
    } catch {
      action.setError("The input is not valid JSON.");
      return;
    }
    const run = await action.run(() =>
      api<WorkflowRun>(`/workflows/${workflow.id}/runs`, { method: "POST", body: { input: parsed } }),
    );
    if (run) router.push(`/workflow-runs/${run.id}`);
  }

  return (
    <li className="py-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <p className="font-medium text-slate-800">{workflow.name}</p>
          <p className="text-xs text-slate-500">
            {workflow.trigger_type ? titleCase(workflow.trigger_type) : "Not activated"}
            {workflow.event_name ? ` · ${workflow.event_name}` : ""}
            {workflow.next_run_at ? ` · next run ${when(workflow.next_run_at)}` : ""}
          </p>
        </div>
        <Badge value={workflow.status} />
        <Link href={`/runs?tab=workflows`} className="text-sm text-noblen-700 hover:underline">Runs</Link>
        {can("workflow:manage") && workflow.status !== "ACTIVE" && workflow.status !== "ARCHIVED" && (
          <Button disabled={action.busy} onClick={() => lifecycle("activate")}>Activate</Button>
        )}
        {can("workflow:manage") && workflow.status === "ACTIVE" && (
          <Button disabled={action.busy} onClick={() => lifecycle("pause")}>Pause</Button>
        )}
        {can("workflow:run") && workflow.status === "ACTIVE" && (
          <Button variant="primary" onClick={() => setRunning((v) => !v)}>Run now</Button>
        )}
      </div>
      {running && (
        <div className="mt-3 flex flex-wrap items-start gap-2">
          <textarea
            aria-label="Run input (JSON)"
            rows={3}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            className={`${inputClass} max-w-lg flex-1 font-mono text-xs`}
          />
          <Button variant="primary" disabled={action.busy} onClick={start}>Start run</Button>
        </div>
      )}
      {token && (
        <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          <p className="font-medium">Webhook token (shown once; store it in the sending system now)</p>
          <p className="mt-1 break-all font-mono text-xs">POST {token.path}</p>
          <p className="break-all font-mono text-xs">X-Noblen-Webhook-Token: {token.token}</p>
        </div>
      )}
      <div className="mt-2">
        <ErrorBanner message={action.error} />
      </div>
    </li>
  );
}

export default function WorkflowsPage() {
  const { can } = useSession();
  const workflows = useApi<Page<Workflow>>("/workflows?limit=100");

  return (
    <>
      <PageHeader
        title="Workflows"
        description="Automations that combine agents, tools, conditions and human approvals. Each run acts for the person who started or activated it."
        actions={can("workflow:manage") ? <NewWorkflow onCreated={workflows.reload} /> : undefined}
      />
      <ErrorBanner message={workflows.error} />
      <Card>
        {workflows.loading && !workflows.data ? (
          <Loading />
        ) : (workflows.data?.items.length ?? 0) === 0 ? (
          <Empty title="No workflows yet" />
        ) : (
          <ul className="divide-y divide-slate-100">
            {workflows.data!.items.map((w) => (
              <WorkflowRow key={w.id} workflow={w} onChange={workflows.reload} />
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}
