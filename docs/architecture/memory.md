# Memory

**Status:** 🟡 Conversation and execution memory are implemented. The other kinds
are planned.

Memory kinds have clear boundaries. They are not mixed into one unstructured store.

| Kind | Implementation | Status |
|---|---|---|
| Conversation memory | `conversations` / `conversation_messages`, loaded per agent version memory mode (`NONE` / `CONVERSATION` / `PERSISTENT`, bounded window) | ✅ Phase 3 |
| Run-scoped context | `AgentRun.context_start_sequence`: a run always sees its own messages plus prior history per memory mode; incomplete tool-call pairs are sanitized out of prior history | ✅ M1 |
| Execution memory | `agent_runs` + `agent_run_steps`, `approvals`, `audit_logs` | ✅ M1 |
| User memory | persistent preferences and context per person | ⬜ Planned (M4) |
| Agent memory | what an agent learns about tasks and workflows | ⬜ Planned (M4) |
| Organizational memory | institutional knowledge | 🟡 via the [Knowledge Engine](knowledge.md) |

`PERSISTENT` memory mode currently behaves like a bounded conversation window,
as it did in Phase 3.

Planned stores will be tenant-scoped tables with explicit write paths, accessed by
agents through **tools**, so authorization, policy, trace and audit apply. Retention
and deletion will be configurable per organization.
