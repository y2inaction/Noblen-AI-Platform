# Memory

**Status:** ✅ Conversation, execution, user, agent and organizational memory are
implemented (user/agent/organization since 3.0 M4). Code:
`backend/app/services/memory_service.py`, `backend/app/agents/tools/memory_tools.py`,
`backend/app/api/v1/memory.py`.

Memory kinds have clear boundaries. They are not mixed into one unstructured store.

| Kind | Implementation | Status |
|---|---|---|
| Conversation memory | `conversations` / `conversation_messages`, loaded per agent version memory mode (`NONE` / `CONVERSATION` / `PERSISTENT`, bounded window) | ✅ Phase 3 |
| Run-scoped context | `AgentRun.context_start_sequence`: a run always sees its own messages plus prior history per memory mode; incomplete tool-call pairs are sanitized out of prior history | ✅ M1 |
| Execution memory | `agent_runs` + `agent_run_steps`, `approvals`, `audit_logs` | ✅ M1 |
| User memory | `memories` with scope `USER`: preferences and standing context for one person | ✅ M4 |
| Agent memory | `memories` with scope `AGENT`: lessons one agent has learned about its work | ✅ M4 |
| Organizational memory | `memories` with scope `ORGANIZATION`: short institutional facts. Documents belong in the [Knowledge Engine](knowledge.md) | ✅ M4 |

## Long-term memory (M4)

Long-term memories are short text facts (at most `MEMORY_MAX_CHARS`, default 1000)
in one tenant-scoped table with an explicit scope, a subject (the person or agent
they belong to) and provenance (the person, agent and run that wrote them).

### Who can read

| Scope | People (API) | Agent runs |
|---|---|---|
| `USER` | only the person it is about. Admins cannot list other people's memories | only runs that person started |
| `AGENT` | members of the organization (`memory:view`) | runs of that agent |
| `ORGANIZATION` | members of the organization (`memory:view`) | every run in the organization |

### Explicit write paths

| Writer | How | Scope | Permission / policy |
|---|---|---|---|
| A person | `POST/PATCH/DELETE /memories` | their own `USER` memories | `memory:write` (MEMBER+) |
| A manager | `POST/PATCH/DELETE /memories` | `AGENT` and `ORGANIZATION` | `memory:manage` (MANAGER+) |
| An agent | `save_user_memory` tool | `USER`, about the run's initiator only | MEDIUM risk, AUTO; initiator needs `memory:write` |
| An agent | `save_agent_memory` tool | `AGENT`, for itself only | MEDIUM risk, **APPROVAL_REQUIRED by default** because it is shared with every user of the agent |
| An agent | `forget_user_memory` tool | deletes the initiator's `USER` memories only | MEDIUM risk, AUTO |

Agents cannot write organizational memory. Every agent write is a tool call, so
binding, permission ceiling, approval policy, run trace and audit all apply. The
tools act through an `AgentMemory` capability that the runtime binds to the run.
Model arguments cannot choose a different person, agent or organization.

`recall_memories` (LOW, `memory:view`) searches the memories the run may see.

### Safeguards

- **No secrets.** Content that looks like a credential (password or API-key
  assignments, `sk-…`, AWS or GitHub tokens, private keys) or a card number
  (15–19 digits passing the Luhn check) is rejected. Phone numbers are allowed.
  This is a coarse guard, not data-loss prevention.
- **Bounded.** At most `MEMORY_MAX_PER_SUBJECT` (default 200) memories per person,
  agent or organization. Identical content (after whitespace normalisation) is
  refreshed instead of duplicated.
- **Untrusted data.** Memories are presented to the model as reference data with
  a notice never to follow instructions in them.
- **Audit without content.** `memory.created`, `memory.updated`, `memory.deleted`
  and `memory.user_forgotten` record the scope and subject, never the text. Run
  traces record how many memories were loaded, never which.

### In the agent's context

Agents whose version uses the `PERSISTENT` memory mode start each run with the
most recently used memories they may see: up to `MEMORY_CONTEXT_MAX_ITEMS` per
scope (default 20), appended to the system prompt as a labelled "Long-term memory"
block. The run trace gets a `MEMORY` step with the counts per scope. Agents in
`NONE` or `CONVERSATION` mode never have memory loaded automatically; they can
still use `recall_memories` if it is bound to them.

Executive AI uses `PERSISTENT` memory and binds all four memory tools
(`save_agent_memory` requires approval). Customer AI has no memory tools: it is
customer-facing, and its run initiator's private memories must not reach customers.

### Retention and deletion

- `PATCH /organizations/current` with `memory_retention_days` (1–3650, or `null`
  to keep memories until deleted; `org:manage`).
- A memory not created or refreshed within the retention period is hidden at
  once from listings, recall and run context. The worker deletes such memories
  every `MEMORY_PURGE_INTERVAL_SECONDS` (default one hour).
- `DELETE /memories/mine` deletes everything stored about the caller in the
  organization.
- Deleting a user or agent deletes their memories (foreign-key cascade).

## Remaining gaps

- Recall is text matching, not semantic search. Embeddings can be added to the
  same table when memory volume needs it.
- Memories of a member who leaves an organization stay until retention or user
  deletion (there is no member-removal flow yet). They are unreachable, because
  only that person's own runs can read them.
- Retention is organization-wide. It is not configurable per scope yet.
