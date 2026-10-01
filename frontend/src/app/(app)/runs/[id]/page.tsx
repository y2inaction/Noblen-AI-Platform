"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import {
  Badge,
  Card,
  ErrorBanner,
  JsonView,
  Loading,
  PageHeader,
  Stat,
  WithheldNotice,
} from "@/components/ui";
import { money, short, titleCase, when } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { RunDetail, RunStep } from "@/lib/types";

function describe(step: RunStep): string {
  switch (step.step_type) {
    case "MODEL_CALL":
      return `Model call${step.model ? ` · ${step.model}` : ""}`;
    case "TOOL_CALL":
      return `Tool · ${step.name ?? "unknown"}`;
    case "APPROVAL":
      return `Approval · ${step.name ?? ""}`;
    case "ESCALATION":
      return "Escalated to a person";
    case "MEMORY":
      return "Loaded long-term memory";
    default:
      return titleCase(step.step_type);
  }
}

export default function RunTracePage() {
  const { id } = useParams<{ id: string }>();
  const run = useApi<RunDetail>(`/runs/${id}`);
  if (run.loading && !run.data) return <Loading />;
  if (!run.data) return <ErrorBanner message={run.error ?? "Run not found."} />;
  const r = run.data;

  return (
    <>
      <PageHeader
        title={`Agent run ${short(r.id)}`}
        description={`Started ${when(r.created_at)}${r.completed_at ? ` · finished ${when(r.completed_at)}` : ""}`}
        actions={
          <>
            <Badge value={r.status} />
            <Link href={`/workforce/${r.agent_id}`} className="text-sm text-noblen-700 hover:underline">Agent</Link>
          </>
        }
      />
      {r.content_withheld && <WithheldNotice reason={r.content_withheld_reason} />}
      {r.escalation_reason && (
        <p className="mb-4 rounded-lg bg-orange-50 px-4 py-3 text-sm text-orange-800">
          Escalated: {r.escalation_reason}
        </p>
      )}
      {r.error_code && <ErrorBanner message={`Failed: ${r.error_code}`} />}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Model calls" value={r.model_calls} />
        <Stat label="Tool calls" value={r.tool_calls} />
        <Stat label="Tokens" value={(r.input_tokens + r.output_tokens).toLocaleString()} />
        <Stat label="Estimated cost" value={money(r.estimated_cost)} />
      </div>
      <Card title="Trace" className="mt-6">
        <p className="mb-4 text-xs text-slate-400">
          The trace records what happened (steps, tools, tokens, decisions), never prompt or response text.
        </p>
        <ol className="relative space-y-4 border-l border-slate-200 pl-6">
          {r.steps.map((step) => (
            <li key={step.id}>
              <span className="absolute -left-1.5 mt-1.5 h-3 w-3 rounded-full border-2 border-white bg-noblen-500" aria-hidden />
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-slate-800">
                  {step.sequence}. {describe(step)}
                </span>
                <Badge value={step.status} />
                <span className="text-xs text-slate-400">
                  {step.latency_ms ? `${step.latency_ms} ms · ` : ""}
                  {step.input_tokens + step.output_tokens ? `${step.input_tokens + step.output_tokens} tokens · ` : ""}
                  {when(step.created_at)}
                </span>
              </div>
              {step.error && <p className="mt-1 text-sm text-red-700">{step.error}</p>}
              {step.detail && Object.keys(step.detail).length > 0 && (
                <details className="mt-1">
                  <summary className="cursor-pointer text-xs text-slate-500">Details</summary>
                  <div className="mt-1">
                    <JsonView value={step.detail} />
                  </div>
                </details>
              )}
            </li>
          ))}
        </ol>
      </Card>
    </>
  );
}
