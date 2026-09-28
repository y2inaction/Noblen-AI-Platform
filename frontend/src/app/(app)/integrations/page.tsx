"use client";

import { useState } from "react";
import { useSession } from "@/components/session-context";
import { Badge, Button, Card, Empty, ErrorBanner, Loading, PageHeader } from "@/components/ui";
import { api } from "@/lib/client";
import { titleCase, when } from "@/lib/format";
import { useAction, useApi } from "@/lib/hooks";
import type { Connection } from "@/lib/types";

interface McpTool {
  id: string;
  name: string;
  remote_name: string;
  description: string;
  risk_level: string;
  permission_mode: string;
  enabled: boolean;
  available: boolean;
}

function McpTools({ connection }: { connection: Connection }) {
  const { can } = useSession();
  const tools = useApi<McpTool[]>(`/integrations/${connection.id}/tools`);
  const action = useAction();

  async function sync() {
    const done = await action.run(() => api(`/integrations/${connection.id}/tools/sync`, { method: "POST" }));
    if (done) tools.reload();
  }

  async function update(tool: McpTool, body: Record<string, unknown>) {
    const done = await action.run(() =>
      api(`/integrations/${connection.id}/tools/${tool.id}`, { method: "PATCH", body }),
    );
    if (done) tools.reload();
  }

  return (
    <div className="mt-3 rounded-lg border border-slate-200 p-3">
      <div className="mb-2 flex items-center justify-between">
        <p className="text-sm font-medium text-slate-700">Imported tools</p>
        {can("integration:manage") && (
          <Button disabled={action.busy} onClick={sync}>Sync from server</Button>
        )}
      </div>
      <ErrorBanner message={tools.error ?? action.error} />
      {(tools.data ?? []).length === 0 ? (
        <p className="text-sm text-slate-500">No tools imported yet.</p>
      ) : (
        <ul className="divide-y divide-slate-100">
          {tools.data!.map((tool) => (
            <li key={tool.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
              <code className="text-xs">{tool.remote_name}</code>
              <Badge value={tool.risk_level} />
              <Badge value={tool.enabled ? "ENABLED" : tool.available ? "DISABLED" : "UNAVAILABLE"} tone={tool.enabled ? "green" : "slate"} />
              <span className="flex-1 truncate text-xs text-slate-500">{tool.description}</span>
              {can("integration:manage") && tool.available && (
                <>
                  <select
                    aria-label={`Risk level for ${tool.remote_name}`}
                    value={tool.risk_level}
                    disabled={action.busy}
                    onChange={(e) => update(tool, { risk_level: e.target.value })}
                    className="rounded border border-slate-300 px-1.5 py-1 text-xs"
                  >
                    {["LOW", "MEDIUM", "HIGH"].map((r) => (
                      <option key={r} value={r}>{titleCase(r)} risk</option>
                    ))}
                  </select>
                  <Button disabled={action.busy} onClick={() => update(tool, { enabled: !tool.enabled })}>
                    {tool.enabled ? "Disable" : "Enable"}
                  </Button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ConnectionItem({ connection, onChange }: { connection: Connection; onChange: () => void }) {
  const { can } = useSession();
  const [result, setResult] = useState<{ ok: boolean; error: string | null } | null>(null);
  const action = useAction();

  async function test() {
    const res = await action.run(() =>
      api<{ ok: boolean; error: string | null }>(`/integrations/${connection.id}/test`, { method: "POST" }),
    );
    if (res) {
      setResult(res);
      onChange();
    }
  }

  const settings = Object.entries(connection.config).filter(([, v]) => v !== null && v !== "");
  const secrets = Object.entries(connection.secret_fields);

  return (
    <li className="py-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <p className="font-medium text-slate-800">{connection.name}</p>
          <p className="text-xs text-slate-500">
            {titleCase(connection.provider)} · last used {when(connection.last_used_at)}
          </p>
        </div>
        <Badge value={connection.status} />
        {can("integration:manage") && (
          <Button disabled={action.busy} onClick={test}>{action.busy ? "Testing…" : "Test"}</Button>
        )}
      </div>
      <dl className="mt-2 grid grid-cols-1 gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
        {settings.map(([k, v]) => (
          <div key={k} className="flex gap-2">
            <dt className="text-slate-500">{k}</dt>
            <dd className="truncate text-slate-700">{String(v)}</dd>
          </div>
        ))}
        {secrets.map(([k, hint]) => (
          <div key={k} className="flex gap-2">
            <dt className="text-slate-500">{k}</dt>
            <dd className="text-slate-700">{hint}</dd>
          </div>
        ))}
      </dl>
      {result && (
        <p className={`mt-2 text-sm ${result.ok ? "text-emerald-700" : "text-red-700"}`}>
          {result.ok ? "Connection works." : result.error}
        </p>
      )}
      {!result && connection.last_error && <p className="mt-2 text-sm text-red-700">Last error: {connection.last_error}</p>}
      <ErrorBanner message={action.error} />
      {connection.provider === "MCP" && <McpTools connection={connection} />}
    </li>
  );
}

export default function IntegrationsPage() {
  const connections = useApi<Connection[]>("/integrations");
  return (
    <>
      <PageHeader
        title="Integrations"
        description="Connections your agents and workflows use: email, webhooks, calendar, CRM and MCP servers. Secrets are write-only and never shown."
      />
      <ErrorBanner message={connections.error} />
      <Card>
        {connections.loading && !connections.data ? (
          <Loading />
        ) : (connections.data?.length ?? 0) === 0 ? (
          <Empty title="No connections yet">
            An administrator adds them through the API (<code>POST /api/v1/integrations</code>).
          </Empty>
        ) : (
          <ul className="divide-y divide-slate-100">
            {connections.data!.map((c) => (
              <ConnectionItem key={c.id} connection={c} onChange={connections.reload} />
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}
