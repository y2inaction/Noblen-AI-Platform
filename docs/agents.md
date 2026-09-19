# Agents

The agent engine is a reusable framework; every commercial agent (Customer
Service, Sales, Content, Executive Assistant, Operations) is a configuration on
top of it, not a separate app.

> **Phase 3 is implemented.** The reusable Agent Engine below is built and tested;
> commercial agent packages come in Phase 6.

## Implementation (Phase 3)

**Layout** (`app/agents/`): `registry.py` (CRUD + lifecycle + versioning),
`runtime.py` (execution loop), `memory.py`, `approvals.py`, `conversations.py`,
`lifecycle.py` (state machine), `errors.py`, and `tools/` (base contract, registry,
built-ins, seed).

**Model → version.** `Agent` is the editable handle (name, status, draft config,
`active_version_id`). Runnable config lives in **immutable** `AgentVersion` rows.
Editing draft config then cutting a new version is the only way to change behavior;
activating a version archives the previous one. Lifecycle
`DRAFT→TESTING→ACTIVE→PAUSED→ARCHIVED` is enforced server-side.

**Runtime loop.** `AgentRuntime.execute()` resolves the runnable version, builds a
sandboxed `ToolContext` + scoped memory + the agent's tool set, then loops:
`AI Gateway → response → (tool calls?) → authorize → AUTO run / APPROVAL_REQUIRED
pause / DISABLED reject → repeat`. It runs entirely through the Phase 2 AI Gateway
(never a vendor SDK) and is bounded by `AGENT_MAX_ITERATIONS`,
`AGENT_MAX_TOOL_CALLS`, and `AGENT_MAX_RUNTIME_SECONDS`.

**Tools.** Only handlers registered in the in-code registry can run; each is bound to
an agent (`agent_tools`) with a permission mode (`AUTO`/`APPROVAL_REQUIRED`/`DISABLED`)
enforced by the runtime. Handlers receive only a `ToolContext` (org id, user id, agent
id, conversation id, and a safe org-settings snapshot) — never DB/env/secrets/OS.
Built-ins: `get_current_time`, `get_organization_settings`, `echo`.

**Memory.** `NONE` (current turn only), `CONVERSATION` (bounded recent window),
`PERSISTENT` (bounded window today; the documented extension point for Phase 4 RAG).
Bounded by `AGENT_MEMORY_MAX_MESSAGES`.

**Approvals.** An `APPROVAL_REQUIRED` tool creates a PENDING `Approval` and stops
execution (no tool runs). `approve` executes the tool and resumes the loop; `reject`
records a rejection and lets the agent respond; approvals expire after a TTL. All
tenant-scoped and gated by `agent:approve_actions`.

**Execution response.** Normalized: `status = completed | awaiting_approval`, with the
assistant `message` (completed) or `approval_id` + `tool_name` (awaiting_approval),
plus aggregated `usage`. No provider credentials or hidden reasoning are ever exposed.

**Conversations.** `conversations` / `conversation_messages` (role system/user/
assistant/tool, with a deterministic per-conversation `sequence`) / participants — all
tenant-scoped. Messages store operational events only; no chain-of-thought is persisted.

## Original design notes

## Agent model (planned)
`name`, `description`, `system instructions`, `provider`, `model`, `temperature`,
`tools`, `knowledge sources`, `memory configuration`, `permissions`, `status`,
`version`, `metadata`.

## Lifecycle
`DRAFT → TESTING → ACTIVE → PAUSED → ARCHIVED`. Agents are **versioned**; an active
production configuration is never destroyed without preserving its history.

## Tool registry
Each tool declares: `name`, `description`, JSON `schema`, a **permission
requirement**, an execution handler, and audit logging. Agents never get raw
database access — only registered, permission-gated tools. Initial categories:
communication, CRM, calendar, knowledge, content, tasks, system.

## Memory (multi-level)
- Conversation memory (current context)
- Agent memory (persistent, per-agent)
- Organization knowledge (company-level)
- User preferences
Memory is explicit and categorised — not every message is saved.

## Safety
Tools honour the execution policy (`AUTO | APPROVAL_REQUIRED | DISABLED`) and
organization/tool permissions. Outward-facing actions require approval by default.

## Commercial agents (Phase 6)
Customer Service, Sales, Content, Executive Assistant, and Operations agents, each
assembled from the shared engine + tools + knowledge + workflows.
