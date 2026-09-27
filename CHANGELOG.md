# Changelog

All notable changes to the Noblen AI Platform are documented here.
The format is loosely based on [Keep a Changelog](https://keepachangelog.com/),
and the project uses Conventional Commits.

## [Unreleased]

### Added — Noblen AI 3.0, Milestone 5 (Workflow engine)
- **Workflows:** `workflows` with immutable, numbered `workflow_versions`, lifecycle
  `DRAFT → ACTIVE ⇄ PAUSED → ARCHIVED`, and the `/workflows` API.
- **Triggers:** manual (`POST /workflows/{id}/runs`, 202), schedules (`every_minutes`
  or `daily_at` in the organization's timezone) and task events (`task.created`,
  `task.completed`).
- **Steps:** `agent` (a real agent run, so approvals and escalation are reused), `tool`
  (workflow-safe tools with validation, permission ceiling and risk policy),
  `condition` (branching) and `approval` (human decision, with notifications).
  Values are passed with `{{ steps.<id>.output… }}` references; there is no
  template language and no code execution.
- **Reliability:** per-step retries with exponential backoff; `on_failure` of `fail`,
  `continue` or `escalate` (operators notified); commit after every step; an
  append-only step trace (`/workflow-runs/{id}`).
- **Controls:** runs act for one person, re-checked before each step; kill switch
  (pause, cancel); separation of duties; HIGH-risk tools always wait for approval;
  step budget; event-chain depth limit; test runs of draft versions.
- **Worker:** the existing worker also queues due schedules, resumes workflows whose
  agent step finished, and executes workflow runs (`FOR UPDATE SKIP LOCKED`).
- `workflow:run` permission (MEMBER+). Migration `ec44db57f95a`; `WORKFLOW_*` settings.

### Added — Noblen AI 3.0, Milestone 4 (Memory)
- **Scoped long-term memory:** a `memories` table with `USER`, `AGENT` and
  `ORGANIZATION` scopes, a subject and provenance (person, agent, run).
- **Human write paths:** `/memories` API. People manage their own user memories
  (`memory:write`); managers manage agent and organization memories (`memory:manage`).
  `DELETE /memories/mine` forgets everything about the caller.
- **Agent write paths:** tools `save_user_memory`, `save_agent_memory` (shared, so
  approval is required by default), `recall_memories` and `forget_user_memory`,
  acting through a run-bound `AgentMemory` capability.
- **Privacy:** user memories are readable only by their person and runs they start,
  not by admins. Credentials and card numbers are rejected. Audit and run traces
  never store memory content.
- **Context:** `PERSISTENT` agents load up to 20 memories per scope into the system
  prompt as labelled reference data, and the run trace records a `MEMORY` step with counts.
- **Retention:** `organizations.memory_retention_days`. Stale memories are hidden at
  once and purged by the worker.
- **Executive AI** (template version 2) uses `PERSISTENT` memory and binds the memory tools.
- Migration `121c8c4186bf`; `MEMORY_*` settings.

### Added — Noblen AI 3.0, Milestone 3 (Knowledge permissions + structured retrieval)
- **Knowledge ACLs:** knowledge-base visibility (`ORGANIZATION` / `RESTRICTED`) and
  document visibility (`INHERIT` / `RESTRICTED`) with user and role grants
  (`/knowledge-bases/{id}/access`, `/documents/{id}/access`). New permissions:
  `knowledge:manage_access` (MANAGER+) and `knowledge:read_all` (ADMIN). The access
  check is a SQL predicate in every knowledge query, applied before ranking and top-k.
- **Agents read as their initiator:** `search_knowledge` and the table tools only
  see what the user who started the run may read.
- **Tabular knowledge:** CSV and XLSX uploads become typed tables (`knowledge_tables`,
  `knowledge_table_rows`) as well as searchable text. They are queried with a
  declarative spec (filters, one aggregate, group-by, order, limit; no raw SQL)
  through `GET /knowledge/tables`, `POST /knowledge/tables/{id}/query`, and the agent
  tools `list_data_tables` / `query_data_table`. Both templates bind the new tools.
- Migration `3a27a8c10dcf`; `openpyxl` dependency; `KNOWLEDGE_MAX_TABLE_ROWS`,
  `KNOWLEDGE_MAX_TABLE_COLUMNS`, `KNOWLEDGE_MAX_QUERY_ROWS` settings.

### Fixed — Milestone 3
- Archived and paused knowledge bases were still searchable. Search now only
  covers `ACTIVE` bases.
- Re-uploading a restricted document's bytes no longer reveals its id through the
  duplicate check.
- Docs previously listed CSV/XLSX as supported before they were; they are now.

### Added — Noblen AI 3.0, Milestone 2 (First AI Workforce)
- **Tasks and notifications:** tenant-scoped `tasks` and in-app `notifications`,
  with `/tasks` and `/notifications` APIs. Assignees and recipients must be
  active members, and notifications are private to their recipient.
- **Workforce tools:** `create_task`, `list_tasks`, `update_task` and
  `notify_member`, reaching data only through a runtime-injected, tenant-,
  agent- and run-bound `AgentWorkspace`. Tasks record the creating agent and run.
- **Reference agents:** `executive-ai` and `customer-ai` templates
  (`GET /agent-templates`, `POST /agent-templates/{key}/instantiate`).
- **Background execution:** `"background": true` on execute returns 202 and
  queues the run. The worker (`python -m app.agents.worker`, Compose `worker`
  service) claims runs with `FOR UPDATE SKIP LOCKED`.
- **Alerts:** approvers are notified of pending approvals; operators and the
  initiator are notified of escalations.
- **Separation of duties:** the `require_independent_approval` organization setting.
- Migration `179df45d96e3`, and the `SKIP_MIGRATIONS` entrypoint switch.

### Added — Noblen AI 3.0, Milestone 1 (Controlled autonomy & AI operations)
- **Run traces:** every agent execution is an `AgentRun` with an append-only
  `AgentRunStep` trace (model calls, tool calls, approvals, escalations) and
  token/cost totals. `GET /runs`, `GET /runs/{id}`.
- **Escalation:** `escalate_to_human` control tool. Model refusal, truncation and
  exhausted budgets end the run `ESCALATED` with a reason.
- **Tool governance:** code-declared `risk_level` (HIGH always requires approval)
  and `required_permission` (the initiating user's current role must hold it),
  shown in the tools API.
- **Approvals:** `POST /approvals/{id}/modify` (validated reviewer arguments) and
  reviewer notes on approve/reject. Resume re-authorizes (kill switch when an
  agent is paused or a tool disabled) and runs on behalf of the initiator.
- **AI Operations:** `GET /operations/overview` (success, escalation and failure
  rates, approvals, tool failures and denials, tokens, cost, usage by model,
  recent escalations).
- **RBAC:** `OPERATOR` (AI Operator) role; `agent:operate`, `run:view`,
  `operations:view` permissions.
- **Gateway:** ordered `provider:model` fallback chain (per request, per agent
  version via `configuration.fallback_models`, and `AI_FALLBACK_MODELS`).
- Docs: `docs/architecture/*`, `docs/product/*`, ADR-0019 to ADR-0023.

### Changed
- Exhausting an agent's iteration, tool-call or runtime budget now returns
  `status: "escalated"` instead of HTTP 409 `runtime_limit_exceeded`.
- Provider failures during agent execution return translated errors (e.g. 503)
  instead of a generic 500, and are recorded as `FAILED` runs.
- `/agents/{id}/activate` and `/pause` require `agent:operate` (held by
  OPERATOR, MANAGER and ADMIN).

### Fixed
- Resuming after an approval left the other tool calls of the same model turn
  unanswered, which providers reject.
- With `memory_mode = NONE`, resuming after an approval dropped the agent's own
  tool call and result from context.
- Incomplete tool-call pairs at the edge of the memory window are now sanitized.
- An approved action executed even if the tool was disabled or the agent paused
  in the meantime.
- Security: an organization `ADMIN` could grant the wildcard `SUPER_ADMIN` role.
- Typing: `tenant_scoped` is generic, which fixes mypy under SQLAlchemy 2.1.

### Added — Phase 4 (Knowledge + RAG)
- Knowledge domain (tenant-scoped): `knowledge_bases`, `knowledge_documents`,
  `document_chunks`, `document_embeddings` (pgvector), `agent_knowledge_sources`.
- Deterministic ingestion pipeline (load → extract → clean → chunk → embed → store)
  for TXT/MD/PDF/DOCX, with an actionable `FAILED` state, `(kb, checksum)` idempotency,
  and atomic re-ingestion.
- Embeddings routed through the Phase 2 AI Gateway; fixed-dimension pgvector column
  (`EmbeddingVector`: `vector(dim)` on PostgreSQL, JSON on SQLite) + HNSW cosine index.
- `KnowledgeRetriever`: tenant-scoped cosine search in PostgreSQL (`<=>`, never Python
  scoring), similarity thresholds, and stable citations (never fabricated).
- `search_knowledge` built-in tool bridging the Agent Runtime to RAG, with server-side
  agent→knowledge-base authorization and untrusted-content labelling (prompt-injection
  defense).
- Document storage abstraction (local; S3-ready), file validation, and path-traversal
  protection.
- Knowledge RBAC (`knowledge:view/create/update/delete/ingest/search/manage_sources`)
  and API: knowledge bases, documents (text + upload), reprocess, search, and agent-KB
  sources. AI usage records gained nullable `knowledge_base_id` + `document_id`.
- Alembic migration (`CREATE EXTENSION vector`, HNSW index; upgrade/downgrade/upgrade
  verified on PostgreSQL) and a CI PostgreSQL+pgvector service for the Knowledge/RAG
  suite. `docs/knowledge.md` + doc updates.

### Added — Phase 3 (Agent Engine)
- Agent registry with CRUD, per-org unique slugs, and a server-enforced lifecycle
  state machine (`DRAFT → TESTING → ACTIVE → PAUSED → ARCHIVED`).
- Immutable agent versioning: an ACTIVE version is never silently modified; changes
  cut a new version. Active-version resolution for execution.
- Agent Runtime: a controlled, bounded generate→tool loop that runs entirely through
  the Phase 2 AI Gateway (no vendor SDK). Guardrails for max iterations, tool calls,
  and runtime seconds.
- Normalized tool-calling added to the AI Core (`ToolSpec`/`ToolCall`, provider
  translation for Anthropic + OpenAI + mock) — additive, Phase 2 behavior unchanged.
- Secure tool registry with a sandboxed execution contract (`ToolContext`/`ToolResult`)
  and safe built-in tools (`get_current_time`, `get_organization_settings`, `echo`).
  Only registered handlers can run; agents get no DB/env/secret/OS access.
- Tool permission modes `AUTO` / `APPROVAL_REQUIRED` / `DISABLED`, enforced server-side.
- Human-in-the-loop approval subsystem (state machine, TTL expiry, approve/reject +
  runtime resume).
- Conversations, messages (operational events only — no chain-of-thought), and
  participants — all tenant-scoped, with a deterministic per-conversation sequence.
- Scoped, bounded memory modes (`NONE` / `CONVERSATION` / `PERSISTENT`).
- New RBAC permissions (`agent:manage_versions`, `agent:approve_actions`,
  `conversation:create/write`, `tool:view/manage`) and per-org runtime limits.
- API: `/agents` (CRUD, lifecycle, versions, `/execute`), `/conversations`, `/tools`
  (+ per-agent bindings), `/approvals` (approve/reject).
- AI usage records extended with `agent_version_id` + `conversation_id` (additive).
- Alembic migration for the agent-engine tables (upgrade/downgrade/upgrade verified).
- Minimal internal Agent Playground page; `docs/agents.md` + doc updates; test suites
  (registry, runtime, agent API, approvals, tenant isolation) with no real API calls.

### Added — Phase 2 (AI Core)
- Provider-independent AI layer: `AIProvider` interface with Anthropic, OpenAI, and
  offline mock adapters (vendor SDKs imported lazily; vendor objects never leak out).
- `AIGateway` with provider/model selection, response/stream normalization, explicit
  timeouts, bounded retries with backoff, structured logging, and request-id +
  organization/user/agent/workflow attribution.
- Normalized generation/embedding/stream types and a normalized AI error hierarchy
  with provider-exception mapping.
- Configurable model-pricing registry with clearly-labelled **estimated** cost (never
  a provider invoice); overridable via `AI_PRICING_OVERRIDES_JSON`.
- Tenant-scoped `ai_usage_records` model, usage service, and Alembic migration
  (upgrade/downgrade/upgrade verified). Operational metadata only — no prompt content.
- AI API: `POST /ai/generate`, `POST /ai/stream` (SSE), `POST /ai/embed`,
  `GET /ai/usage`; new RBAC permissions (`ai:generate/stream/embed/view_usage`) and a
  per-organization AI rate limit.
- Test suites for providers, gateway, usage/cost, and the API (auth, RBAC, tenant
  isolation, no-secrets-in-response) — no real API calls.
- `docs/ai-core.md` and updates to architecture/roadmap/decisions docs.

### Added — Phase 0 (Discovery)
- Repository discovery and runtime inventory.
- `ARCHITECTURE.md`, `PROJECT_ROADMAP.md`, `DECISIONS.md`, `README.md`, `.gitignore`,
  `.env.example`, and the `docs/` documentation set.

### Added — Phase 1 (Foundation)
- FastAPI backend skeleton: env-driven config, structured JSON logging with request
  IDs, typed exceptions, CORS, secure headers, and basic per-IP rate limiting.
- Health (`/health`) and readiness (`/readiness`) endpoints.
- Async SQLAlchemy 2.0 database layer + Alembic migrations (initial revision).
- Core multi-tenant models: organizations, users, organization members, roles,
  permissions, teams, refresh tokens, email-verification & password-reset tokens,
  audit logs.
- Authentication: register (creates organization + owner), login, rotating refresh,
  logout, current-user, and email-verification / password-reset scaffolding.
  Argon2id password hashing.
- Server-side RBAC (`SUPER_ADMIN`/`ADMIN`/`MANAGER`/`MEMBER`/`VIEWER`) and
  tenant-scoping utilities.
- Next.js + TypeScript + Tailwind frontend scaffold (login + dashboard shell).
- Test suites: health, auth, RBAC, and tenant isolation.
- Docker (backend + frontend), dev/prod Docker Compose, Nginx reverse proxy,
  and a GitHub Actions CI pipeline (lint, type-check, test, build, docker build).
