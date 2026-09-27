# Noblen AI 3.0 — Architecture Overview

> **Status:** living document. It describes what is **implemented** and marks
> everything else as *planned*. Last updated with Milestone 1 (Controlled Autonomy
> & AI Operations).

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
| Reference AI Workforce agents | — [../product/workforce.md](../product/workforce.md) | ⬜ Planned (M2) |
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

1. **M1: Controlled autonomy & AI operations** ✅ (this milestone).
2. **M2: Execution hardening + first workforce.** Run agents on a worker queue,
   send approval notifications, add separation-of-duties settings, build
   Executive AI and Customer AI from templates with their first real tools
   (tasks, notes, notifications), and harden model replay (preserve provider-native
   content for models with interleaved thinking).
3. **M3: Knowledge permissions.** Add document/collection ACLs by user and role on
   top of the Phase 4 org + agent scoping, plus structured (SQL) retrieval next to
   vector search.
4. **M4: Memory.** Add user, agent and organizational memory stores with explicit
   write paths.
5. **M5: Workflow engine.** Triggers, steps, branching, retries, approval steps and
   schedules, with agents as steps.
6. **M6: Integrations.** An MCP adapter, credential references, and email,
   calendar and CRM tools with declared risk levels.
7. **M7: Operating-environment UI.** Dashboard, workforce, approvals inbox, run traces.
