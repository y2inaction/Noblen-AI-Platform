"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  Badge,
  Button,
  Card,
  Empty,
  ErrorBanner,
  JsonView,
  Loading,
  PageHeader,
  RestrictedPayloadNotice,
  RestrictedPublicationBadge,
  inputClass,
} from "@/components/ui";
import { ApiRequestError, api } from "@/lib/client";
import { short, when } from "@/lib/format";
import { useAction, useApi } from "@/lib/hooks";
import type { Approval, Page, WorkflowRun, WorkflowRunDetail, WorkflowStepRun } from "@/lib/types";

/** Run a decision; a `not_eligible` refusal (M9.6) also reloads the request, so
 *  the page shows what the server now allows rather than what it showed before. */
async function decideOrRefresh(
  action: ReturnType<typeof useAction>,
  send: () => Promise<unknown>,
  reload: () => void,
): Promise<void> {
  let refused = false;
  const done = await action.run(async () => {
    try {
      return await send();
    } catch (err) {
      refused = err instanceof ApiRequestError && err.code === "not_eligible";
      throw err;
    }
  });
  if (done || refused) reload();
}

function AgentApproval({ approval, onDone }: { approval: Approval; onDone: () => void }) {
  const [note, setNote] = useState("");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(() => JSON.stringify(approval.request_payload, null, 2));
  const action = useAction();
  // The server decides what this approver may see; the page only follows it.
  const withheld = approval.payload_withheld;

  async function decide(verb: "approve" | "reject" | "modify") {
    let body: Record<string, unknown> = { note: note || null };
    if (verb === "modify") {
      try {
        body = { ...body, arguments: JSON.parse(draft) };
      } catch {
        action.setError("The edited arguments are not valid JSON.");
        return;
      }
    }
    await decideOrRefresh(
      action,
      () => api(`/approvals/${approval.id}/${verb}`, { method: "POST", body }),
      onDone,
    );
  }

  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          Use <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">{approval.tool_name}</code>
          {approval.risk_level && <Badge value={approval.risk_level} />}
          {approval.restricted_publication && <RestrictedPublicationBadge />}
        </span>
      }
      action={<span className="text-xs text-slate-400">requested {when(approval.created_at)}</span>}
    >
      {approval.reason && <p className="mb-3 text-sm text-slate-600">{approval.reason}</p>}
      {withheld ? (
        <RestrictedPayloadNotice />
      ) : editing ? (
        <textarea
          aria-label="Arguments (JSON)"
          rows={8}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          className={`${inputClass} font-mono text-xs`}
        />
      ) : (
        <JsonView value={approval.request_payload} />
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <input
          aria-label="Note for the record"
          placeholder="Note (optional)"
          value={note}
          maxLength={2000}
          onChange={(e) => setNote(e.target.value)}
          className={`${inputClass} max-w-sm flex-1`}
        />
        {editing && !withheld ? (
          <>
            <Button variant="primary" disabled={action.busy} onClick={() => decide("modify")}>Approve with changes</Button>
            <Button disabled={action.busy} onClick={() => setEditing(false)}>Cancel edit</Button>
          </>
        ) : (
          <>
            <Button variant="primary" disabled={action.busy} onClick={() => decide("approve")}>Approve</Button>
            {!withheld && (
              <Button disabled={action.busy} onClick={() => setEditing(true)}>Edit arguments</Button>
            )}
          </>
        )}
        <Button variant="danger" disabled={action.busy} onClick={() => decide("reject")}>Reject</Button>
        {approval.run_id && (
          <Link href={`/runs/${approval.run_id}`} className="ml-auto text-sm text-noblen-700 hover:underline">
            Run {short(approval.run_id)}
          </Link>
        )}
      </div>
      <div className="mt-2">
        <ErrorBanner message={action.error} />
      </div>
    </Card>
  );
}

interface WaitingDecision {
  run: WorkflowRun;
  step: WorkflowStepRun;
}

function WorkflowDecision({ item, onDone }: { item: WaitingDecision; onDone: () => void }) {
  const [note, setNote] = useState("");
  const action = useAction();
  const output = item.step.output ?? {};
  // A restricted step whose request the server withheld (M9.6) comes without output.
  const withheld = item.step.restricted_publication && item.step.output === null;

  async function decide(verb: "approve" | "reject") {
    await decideOrRefresh(
      action,
      () => api(`/workflow-runs/${item.run.id}/${verb}`, { method: "POST", body: { note: note || null } }),
      onDone,
    );
  }

  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          {String(output.title ?? `Step ${item.step.step_id}`)}
          {item.step.restricted_publication && <RestrictedPublicationBadge />}
        </span>
      }
      action={<span className="text-xs text-slate-400">waiting {when(item.step.started_at)}</span>}
    >
      <p className="mb-2 text-sm text-slate-500">
        Workflow step <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">{item.step.step_id}</code>
      </p>
      {withheld && <RestrictedPayloadNotice />}
      {output.details !== undefined && output.details !== null && (
        typeof output.details === "string" ? (
          <p className="whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-sm text-slate-700">{output.details}</p>
        ) : (
          <JsonView value={output.details} />
        )
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <input
          aria-label="Note for the record"
          placeholder="Note (optional)"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          className={`${inputClass} max-w-sm flex-1`}
        />
        <Button variant="primary" disabled={action.busy} onClick={() => decide("approve")}>Approve</Button>
        <Button variant="danger" disabled={action.busy} onClick={() => decide("reject")}>Reject</Button>
        <Link href={`/workflow-runs/${item.run.id}`} className="ml-auto text-sm text-noblen-700 hover:underline">
          Workflow run {short(item.run.id)}
        </Link>
      </div>
      <div className="mt-2">
        <ErrorBanner message={action.error} />
      </div>
    </Card>
  );
}

export default function ApprovalsPage() {
  const approvals = useApi<Page<Approval>>("/approvals?status=PENDING&limit=50");
  const waiting = useApi<Page<WorkflowRun>>("/workflow-runs?status=WAITING&limit=25");
  const [decisions, setDecisions] = useState<WaitingDecision[] | null>(null);

  // A waiting workflow run needs a person only when its current step asks for a
  // decision (not when it waits on an agent run, which has its own approval).
  useEffect(() => {
    if (!waiting.data) return;
    let alive = true;
    Promise.all(
      waiting.data.items.map((run) => api<WorkflowRunDetail>(`/workflow-runs/${run.id}`).catch(() => null)),
    ).then((details) => {
      if (!alive) return;
      const found: WaitingDecision[] = [];
      for (const detail of details) {
        const step = detail?.steps
          .filter((s) => s.status === "WAITING" && !s.agent_run_id && !s.decision)
          .at(-1);
        if (detail && step) found.push({ run: detail, step });
      }
      setDecisions(found);
    });
    return () => {
      alive = false;
    };
  }, [waiting.data]);

  const agentItems = approvals.data?.items ?? [];
  const loading = (approvals.loading && !approvals.data) || (waiting.loading && decisions === null);

  return (
    <>
      <PageHeader
        title="Approvals"
        description="Actions your AI workforce wants to take and is waiting on a person for. Decisions are recorded with your name and note."
      />
      <ErrorBanner message={approvals.error ?? waiting.error} />
      {loading ? (
        <Loading />
      ) : agentItems.length === 0 && (decisions ?? []).length === 0 ? (
        <Empty title="Nothing is waiting for you">New requests appear here and in your notifications.</Empty>
      ) : (
        <div className="space-y-4">
          {agentItems.map((a) => (
            <AgentApproval key={a.id} approval={a} onDone={approvals.reload} />
          ))}
          {(decisions ?? []).map((d) => (
            <WorkflowDecision key={d.step.id} item={d} onDone={waiting.reload} />
          ))}
        </div>
      )}
    </>
  );
}
