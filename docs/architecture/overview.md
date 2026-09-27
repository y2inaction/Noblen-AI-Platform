# Noblen AI 3.0 — Architecture Overview

> **Status:** living document. It describes what is **implemented** and marks
> everything else as *planned*. Last updated with Milestone 1 (Controlled Autonomy
> & AI Operations) and Milestone 2 (First AI Workforce).

## 1. Target architecture

```
                    NOBLEN AI
                        │
                    NOBLENOS
                        │
              ┌─────────▼─────────┐
              │ Noblen AI Platform│
              └─────────┬─────────┘
       ┌────────────────┼────────────────┐
    AGENTS          KNOWLEDGE        AUTOMATION
       └────────────────┼────────────────┘
                INTELLIGENCE LAYER
             ┌──────────┼──────────┐
         BUSINESS     INDUSTRY   ENTERPRISE
            OS           OS          OS
```

Everything above the platform line is **configuration** of one core, never a
separate codebase. See [`../product/noblen-ai-3.md`](../product/noblen-ai-3.md).

## 2. How 3.0 builds on Phases 1–4

Noblen AI 3.0 continues the existing platform. It does not replace it. Phases 1–4
already delivered the core. Milestone 1 closes the gaps between that core and a
*production agent*: identity, permissions, tools, knowledge, execution, error
handling, observability, auditability and human escalation.

| Component | Where | Status |
|---|---|---|
| Auth, multi-tenancy, RBAC, audit log | `app/services/auth_service.py`, `app/db/`, `app/rbac/` | ✅ Phase 1; RBAC extended in M1 |
| Model gateway (Anthropic, OpenAI, mock) | `app/ai/` — [model-gateway.md](model-gateway.md) | ✅ Phase 2; **fallback chain added in M1** |
| Agent engine (versions, lifecycle, runtime, tools, approvals, conversations) | `app/agents/` — [agents.md](agents.md) | ✅ Phase 3; **controlled autonomy added in M1** |
| Knowledge + RAG (pgvector) | `app/knowledge/` — [knowledge.md](knowledge.md) | ✅ Phase 4 (org + agent scoped) |
| **Run trace (`AgentRun`, `AgentRunStep`)** | `app/models/run.py` | ✅ **M1** |
| **Escalation to human operators** | `app/agents/runtime.py` | ✅ **M1** |
| **Tool risk levels + initiator permission ceiling** | `app/agents/tools/base.py` — [tools.md](tools.md) | ✅ **M1** |
| **Approval modify, notes, correct resume, kill switch** | `app/agents/`, `/approvals` | ✅ **M1** |
| **AI Operator role, operations overview** | `app/rbac/`, `/operations/overview` — [observability.md](observability.md) | ✅ **M1** (backend) |
| Memory beyond conversations | — [memory.md](memory.md) | 🟡 conversation + execution memory only |
| Workflow engine | — [workflows.md](workflows.md) | ⬜ Planned |
| Integrations / MCP adapters | — | ⬜ Planned |
| **Tasks & notifications, workforce tools** | `app/services/work_service.py`, `app/agents/tools/work_tools.py` | ✅ **M2** |
| **Reference AI Workforce agents (Executive AI, Customer AI)** | `app/agents/templates.py` — [../product/workforce.md](../product/workforce.md) | ✅ **M2** (others planned) |
| **Background execution (DB-queue worker)** | `app/agents/worker.py` | ✅ **M2** |
| **Approval/escalation alerts, separation of duties** | runtime, `/approvals` | ✅ **M2** |
| Operating-environment UI | `frontend/` (dashboard shell, playground) | ⬜ Planned |

## 3. Execution path of one agent task

```
POST /api/v1/agents/{id}/execute
  → tenant context + agent:run permission
  → AgentRuntime.execute
      create AgentRun (context starts at this user message) · audit agent.run_started
      loop (bounded: iterations / tool calls / runtime):
        AIGateway.generate ── primary model, then ordered fallbacks
        record MODEL_CALL step + usage
        refusal / truncation ─────────────────────────────► ESCALATED
        for each tool call:
          escalate_to_human ──────────────────────────────► ESCALATED
          binding exists & not DISABLED            else DENIED (audited)
          initiator's CURRENT role has permission  else DENIED (audited)
          arguments valid                          else error result to model
          policy (binding mode ⊕ HIGH risk) = APPROVAL ───► AWAITING_APPROVAL
          execute on behalf of the initiator → TOOL_CALL step
      final answer ──────────────────────────────────────► COMPLETED
      provider failure ──────────────────────────────────► FAILED (persisted, HTTP 5xx)
```

After an approval decision, `resume_after_approval` re-checks the agent status,
tool binding and initiator permission, executes approved or modified arguments,
answers the remaining calls of the paused turn, and continues the loop.

## 4. Implementation sequence

1. **M1: Controlled autonomy & AI operations** ✅
2. **M2: First AI Workforce + execution hardening** ✅ Tasks and notifications,
   workforce tools, Executive AI and Customer AI templates, background worker,
   approval/escalation alerts, separation of duties. *Carried forward:*
   provider-native replay (needed only before defaulting to a model that thinks
   by default) and automatic re-queueing of stale runs.
3. **M3: Knowledge permissions + structured retrieval** ✅ Knowledge-base and
   document ACLs by user and role, enforced inside the retrieval query; agents read
   as their initiator; CSV/XLSX tables with a declarative query spec and the
   `list_data_tables` / `query_data_table` tools.
4. **M4: Memory** ✅ Scoped `USER` / `AGENT` / `ORGANIZATION` memory with explicit
   write paths (owner and manager APIs, approval-gated agent tools), private user
   memory, retention, and context loading for `PERSISTENT` agents.
5. **M5: Workflow engine** ✅ Versioned workflows with manual, schedule and task-event
   triggers; agent, tool, condition and approval steps; retries with backoff and
   failure policies; run-as authority re-checked per step; DB-queued execution on
   the existing worker.
6. **M6: Integrations** ✅ Encrypted connections (credential references) with an
   SSRF guard; SMTP email, outbound webhooks, CalDAV calendar and HubSpot CRM tools
   with declared risk levels; a remote MCP adapter whose imported tools are
   organization-scoped and admin-governed; inbound webhook triggers for workflows.
7. **M7: Operating-environment UI.** Dashboard, workforce, approvals inbox, run traces.
