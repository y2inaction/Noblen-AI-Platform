"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useSession } from "@/components/session-context";
import { Badge, Button, Card, Empty, ErrorBanner, Loading, PageHeader } from "@/components/ui";
import { api } from "@/lib/client";
import { titleCase } from "@/lib/format";
import { useAction, useApi } from "@/lib/hooks";
import type { Agent, AgentTemplate, Page } from "@/lib/types";

export default function WorkforcePage() {
  const router = useRouter();
  const { can } = useSession();
  const agents = useApi<Page<Agent>>("/agents?limit=100");
  const templates = useApi<AgentTemplate[]>("/agent-templates");
  const action = useAction();

  async function setStatus(agent: Agent, verb: "activate" | "pause") {
    const done = await action.run(() => api<Agent>(`/agents/${agent.id}/${verb}`, { method: "POST" }));
    if (done) agents.reload();
  }

  async function hire(template: AgentTemplate) {
    const agent = await action.run(() =>
      api<Agent>(`/agent-templates/${template.key}/instantiate`, { method: "POST", body: {} }),
    );
    if (agent) router.push(`/workforce/${agent.id}`);
  }

  const items = (agents.data?.items ?? []).filter((a) => a.status !== "ARCHIVED");

  return (
    <>
      <PageHeader
        title="Workforce"
        description="Your AI agents. Every action they take runs under the permissions of the person who started it, with approvals for risky steps."
      />
      <ErrorBanner message={agents.error ?? action.error} />

      <Card title="Agents">
        {agents.loading && !agents.data ? (
          <Loading />
        ) : items.length === 0 ? (
          <Empty title="No agents yet">Hire a ready-made agent below.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-slate-400">
                <tr>
                  <th className="pb-2 font-medium">Agent</th>
                  <th className="pb-2 font-medium">Type</th>
                  <th className="pb-2 font-medium">Memory</th>
                  <th className="pb-2 font-medium">Status</th>
                  <th className="pb-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {items.map((agent) => (
                  <tr key={agent.id}>
                    <td className="py-2.5">
                      <Link href={`/workforce/${agent.id}`} className="font-medium text-slate-800 hover:text-noblen-700">
                        {agent.name}
                      </Link>
                      {agent.description && (
                        <p className="max-w-md truncate text-xs text-slate-500">{agent.description}</p>
                      )}
                    </td>
                    <td className="py-2.5 text-slate-600">{titleCase(agent.agent_type)}</td>
                    <td className="py-2.5 text-slate-600">{titleCase(agent.memory_mode)}</td>
                    <td className="py-2.5"><Badge value={agent.status} /></td>
                    <td className="py-2.5 text-right">
                      {can("agent:operate") && agent.status === "ACTIVE" && (
                        <Button disabled={action.busy} onClick={() => setStatus(agent, "pause")}>Pause</Button>
                      )}
                      {can("agent:operate") && agent.status === "PAUSED" && (
                        <Button disabled={action.busy} onClick={() => setStatus(agent, "activate")}>Resume</Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <h2 className="mb-3 mt-8 text-sm font-semibold text-slate-800">Ready-made agents</h2>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {(templates.data ?? []).map((t) => (
          <Card
            key={t.key}
            title={t.name}
            action={
              can("agent:create") ? (
                <Button variant="primary" disabled={action.busy} onClick={() => hire(t)}>
                  Add to workforce
                </Button>
              ) : undefined
            }
          >
            <p className="text-sm text-slate-600">{t.summary}</p>
            <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-slate-600">
              {t.capabilities.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
            {t.planned.length > 0 && (
              <p className="mt-3 text-xs text-slate-400">Not yet available: {t.planned.join(" · ")}</p>
            )}
          </Card>
        ))}
      </div>
    </>
  );
}
