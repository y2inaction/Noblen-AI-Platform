"use client";

import Link from "next/link";
import { useState } from "react";
import {
  approveAction,
  executeAgent,
  listAgents,
  type AgentSummary,
  type ExecutionResult,
} from "@/lib/api";

/**
 * Minimal internal Agent Playground (Phase 3). Not the production dashboard —
 * it exists to prove the runtime: select an agent, send a message, execute, and
 * see the assistant response or the approval-required state.
 *
 * Paste an access token (from POST /api/v1/auth/login) to authenticate.
 */
export default function PlaygroundPage() {
  const [token, setToken] = useState("");
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [agentId, setAgentId] = useState("");
  const [message, setMessage] = useState("");
  const [conversationId, setConversationId] = useState<string | undefined>();
  const [result, setResult] = useState<ExecutionResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function loadAgents() {
    setError(null);
    try {
      const items = await listAgents(token);
      setAgents(items);
      if (items.length && !agentId) setAgentId(items[0].id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load agents");
    }
  }

  async function send() {
    setError(null);
    setBusy(true);
    try {
      const res = await executeAgent(token, agentId, message, conversationId);
      setResult(res);
      setConversationId(res.conversation_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Execution failed");
    } finally {
      setBusy(false);
    }
  }

  async function approve() {
    if (!result?.approval_id) return;
    setBusy(true);
    try {
      await approveAction(token, result.approval_id);
      setError(null);
      setResult({ ...result, status: "approved (resumed)" });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Approval failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto max-w-3xl px-4 py-10">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-slate-900">Agent Playground</h1>
        <Link href="/dashboard" className="text-sm text-noblen-700 hover:underline">
          Dashboard →
        </Link>
      </div>
      <p className="mb-6 text-sm text-slate-500">
        Internal test harness for the Agent Engine. Paste an access token to begin.
      </p>

      <label className="block text-sm font-medium text-slate-700">Access token</label>
      <div className="mb-4 mt-1 flex gap-2">
        <input
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="Bearer token"
          className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm"
        />
        <button
          onClick={loadAgents}
          className="rounded-lg bg-noblen-600 px-4 py-2 text-sm font-medium text-white hover:bg-noblen-700"
        >
          Load agents
        </button>
      </div>

      {agents.length > 0 && (
        <div className="mb-4">
          <label className="block text-sm font-medium text-slate-700">Agent</label>
          <select
            value={agentId}
            onChange={(e) => setAgentId(e.target.value)}
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
          >
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name} ({a.status})
              </option>
            ))}
          </select>
        </div>
      )}

      <label className="block text-sm font-medium text-slate-700">Message</label>
      <textarea
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        rows={3}
        className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
        placeholder="Ask the agent something…"
      />
      <div className="mt-3 flex items-center gap-3">
        <button
          onClick={send}
          disabled={busy || !token || !agentId || !message}
          className="rounded-lg bg-noblen-600 px-5 py-2 text-sm font-medium text-white hover:bg-noblen-700 disabled:opacity-50"
        >
          {busy ? "Running…" : "Execute"}
        </button>
        {conversationId && (
          <span className="text-xs text-slate-400">conversation: {conversationId.slice(0, 8)}…</span>
        )}
      </div>

      {error && (
        <p className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
      )}

      {result && (
        <div className="mt-6 rounded-xl border border-slate-200 bg-white p-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            Status: {result.status}
          </p>
          {result.status === "completed" && result.message && (
            <p className="mt-2 whitespace-pre-wrap text-slate-800">{result.message.content}</p>
          )}
          {result.status === "awaiting_approval" && (
            <div className="mt-2">
              <p className="text-sm text-amber-700">
                The agent wants to run tool <strong>{result.tool_name}</strong> — approval required.
              </p>
              <button
                onClick={approve}
                disabled={busy}
                className="mt-3 rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-50"
              >
                Approve
              </button>
            </div>
          )}
          <pre className="mt-3 overflow-x-auto rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
            {JSON.stringify(result.usage, null, 2)}
          </pre>
        </div>
      )}
    </main>
  );
}
