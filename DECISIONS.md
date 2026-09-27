# Noblen AI Platform — Architecture Decision Records (ADR)

Chronological log of significant technical decisions and their rationale.
New decisions are appended; superseded ones are marked, not deleted.

---

### ADR-0001 — One reusable platform, not eight apps
**Status:** Accepted (Phase 0)
**Context:** Noblen offers eight commercial AI services and plans nine future
"OS" products. Building them independently would duplicate auth, tenancy, billing,
AI plumbing, etc., and diverge over time.
**Decision:** Build a single shared core (auth, tenancy, RBAC, AI gateway, agent
engine, memory, knowledge, workflows, integrations, billing, usage, audit). Each
commercial service is a *configuration* (agents + workflows + knowledge) on that core.
**Consequences:** Slightly more upfront design; dramatically lower long-term cost and
consistent security posture across all products.

### ADR-0002 — Backend: FastAPI + SQLAlchemy 2.0 (async) + Pydantic v2
**Status:** Accepted (Phase 0)
**Context:** Need an async, typed, well-documented, high-throughput API with strong
validation and automatic OpenAPI docs.
**Decision:** FastAPI + Pydantic v2 for the API/validation, SQLAlchemy 2.0 async ORM
with asyncpg, Alembic for migrations.
**Consequences:** First-class async, auto OpenAPI, mature ecosystem. Team must follow
the async patterns consistently.

### ADR-0003 — Multi-tenancy: shared schema + mandatory `organization_id`
**Status:** Accepted (Phase 0)
**Context:** Must be multi-tenant from day one on modest infrastructure, with strict
isolation but low operational overhead.
**Decision:** Shared database, shared schema, row-level discrimination via a required
`organization_id` on every tenant-owned table. Enforced in the service layer via a
tenant-scoping helper; guarded by an automated tenant-isolation test suite.
PostgreSQL Row-Level Security is planned as defence-in-depth in Phase 10.
**Consequences:** Simple ops and cheap to run; isolation depends on disciplined
scoping — hence the mandatory automated test. Reconsider schema-per-tenant only if a
very large enterprise tenant needs physical isolation.

### ADR-0004 — Password hashing with Argon2id
**Status:** Accepted (Phase 1)
**Context:** Passwords must never be stored in plaintext; bcrypt has a 72-byte
truncation quirk and recent passlib/bcrypt version friction.
**Decision:** Use `argon2-cffi` (Argon2id) directly via a thin wrapper in
`core/security.py`. Memory-hard, modern, no truncation surprises.
**Consequences:** Slightly higher CPU/memory per hash (intended); parameters are
tunable as hardware improves.

### ADR-0005 — Sessions: JWT access + rotating refresh tokens
**Status:** Accepted (Phase 1)
**Context:** Need stateless, horizontally scalable auth with the ability to revoke.
**Decision:** Short-lived JWT access tokens (stateless) + long-lived refresh tokens
stored **hashed** in the DB, rotated on each refresh and revocable on logout.
**Consequences:** Scales without server-side session store for access; refresh
revocation gives control. Refresh token table must be pruned periodically.

### ADR-0006 — Portable column types; SQLite for tests, PostgreSQL for real
**Status:** Accepted (Phase 1)
**Context:** Want fast, dependency-free tests in CI while targeting PostgreSQL
(pgvector, JSONB) in production.
**Decision:** Use SQLAlchemy generic types (`Uuid`, `JSON`, `String`, `Text`) in
Phase 1 models so the suite can run on in-memory SQLite (schema created via
`metadata.create_all`, bypassing Alembic). Alembic migrations target PostgreSQL.
Postgres-specific features (JSONB, pgvector, GIN indexes) are introduced with the
phases that need them (Phase 4 onward), with a Postgres CI service added then.
**Consequences:** Very fast unit/integration tests now; a documented seam where SQLite
and Postgres behaviour could differ, closed by the Postgres-backed CI job added in
later phases.

### ADR-0007 — AI provider abstraction (no direct provider coupling)
**Status:** Accepted (Phase 0, implemented Phase 2)
**Context:** Business logic must not depend on a single AI vendor; must add providers
easily and attribute cost/usage.
**Decision:** `AIService` → `Provider` interface → `AnthropicProvider` /
`OpenAIProvider`. All AI access goes through the gateway; keys live only server-side.
**Consequences:** Vendor-swappable, testable with a mock provider, centralized
retries/limits/telemetry.

### ADR-0008 — Payments abstraction with Paystack first
**Status:** Accepted (Phase 0, implemented Phase 7)
**Context:** Primary market is Nigeria/Africa (Paystack), but other providers will
come later.
**Decision:** A `BillingProvider` interface with a `PaystackProvider` implementation.
Plans and prices are **configurable data**, never hard-coded.
**Consequences:** Provider-swappable billing; admin-configurable pricing.

### ADR-0009 — Localisation not hard-coded
**Status:** Accepted (Phase 0)
**Context:** Nigeria-first, but international from the start; multi-language later.
**Decision:** Currency defaults to `NGN` and timezone to `Africa/Lagos` but both are
stored per organization and never hard-coded in logic. Money/time values always carry
explicit currency/timezone. Language architecture leaves room for Hausa/Yoruba/Igbo/
Pidgin.
**Consequences:** Clean path to multi-currency, multi-timezone, multi-language.

### ADR-0010 — Human-in-the-loop as a first-class policy
**Status:** Accepted (Phase 0)
**Context:** AI actions can be irreversible or outward-facing (emails, payments,
deletes, publishing).
**Decision:** Every tool/action has an execution policy `AUTO | APPROVAL_REQUIRED |
DISABLED`; risky actions default to `APPROVAL_REQUIRED`. Autonomous external
communication is disabled until an org opts in.
**Consequences:** Safer defaults; approval workflow is core infrastructure, not an
afterthought.

### ADR-0011 — AI Gateway is DB-free; usage is persisted by the API/service layer
**Status:** Accepted (Phase 2)
**Context:** The gateway must be trivially unit-testable and reusable from many call
sites (endpoints, agents, workflows) without dragging a database session through it.
**Decision:** `AIGateway` computes token usage and estimated cost and returns them on
the normalized response, but performs no persistence. The API/service layer
(`ai_usage_service`) writes `ai_usage_records` and owns the transaction.
**Consequences:** Clean separation and fast tests; each caller is responsible for
recording usage (endpoints do this on both success and failure paths).

### ADR-0012 — Lazy provider SDK imports + injectable clients
**Status:** Accepted (Phase 2)
**Context:** Tests must never require a real API key or network, and importing the app
shouldn't require every vendor SDK to be importable/configured.
**Decision:** Provider adapters import their SDK lazily inside `_get_client()` and
accept an injected client. Tests inject fakes; a `MockProvider` is the default in the
test gateway. Vendor exceptions are mapped to a normalized error hierarchy.
**Consequences:** Offline, deterministic tests; adding a provider is isolated to one
adapter.

### ADR-0013 — Cost is an estimate from a configurable pricing registry
**Status:** Accepted (Phase 2)
**Context:** Provider pricing changes over time and must not be hard-coded in business
logic, nor presented as an actual invoice.
**Decision:** A `PricingRegistry` keyed by `provider:model` (input/output price per 1M
tokens, currency, effective date), overridable via `AI_PRICING_OVERRIDES_JSON`.
Unknown models return `None` (never an invented price). All costs are labelled
*estimated*.
**Consequences:** Cost visibility without misrepresenting billing; prices are updated
as data, not code.

### ADR-0014 — Agents are mutable handles; versions are immutable snapshots
**Status:** Accepted (Phase 3)
**Context:** A production agent must not change behavior because someone edited its
prompt; but authors need to iterate.
**Decision:** `Agent` holds the editable draft config + an `active_version_id` pointer.
Runnable config lives in immutable `AgentVersion` rows (unique `version_number` per
agent). Editing means: update the draft → create a new version → activate it. There is
no version-edit path; activating a new version archives the previous one. The runtime
resolves the active version server-side (or an explicit test version).
**Consequences:** Safe production behavior with full history; slightly more ceremony to
ship a change (cut + activate a version), which is the intended safeguard.

### ADR-0015 — Normalized tool-calling in the gateway; server-side permission + approvals
**Status:** Accepted (Phase 3)
**Context:** Agents need controlled capabilities, but a model-generated tool call must
never be trusted to authorize itself, and the runtime must stay provider-agnostic.
**Decision:** Tool-calling is expressed with normalized `ToolSpec`/`ToolCall` types in
the AI Core (each provider adapter translates to/from its native shape; the mock drives
it deterministically for tests). Only handlers in an in-code registry can run; each is
bound to an agent with a permission mode (`AUTO`/`APPROVAL_REQUIRED`/`DISABLED`) enforced
by the runtime, not the model. `APPROVAL_REQUIRED` creates a human approval and stops;
approval resumes the loop. Tools receive only a sandboxed `ToolContext` — never the DB,
env, secrets, filesystem, or OS.
**Consequences:** Safe, auditable tool use that generalizes across providers and future
tools; the tool loop is fully testable offline via the mock provider.

### ADR-0016 — Runtime is gateway-injected and bounded
**Status:** Accepted (Phase 3)
**Context:** The runtime must be testable without real providers and must never run
unbounded on a misbehaving/malicious agent.
**Decision:** `AgentRuntime` takes an injected `AIGateway` (tests inject a mock-backed
one) and enforces configurable limits (`AGENT_MAX_ITERATIONS`, `AGENT_MAX_TOOL_CALLS`,
`AGENT_MAX_RUNTIME_SECONDS`) plus bounded memory. Usage is recorded through the Phase 2
service, extended (additively) with agent/version/conversation attribution.
**Consequences:** Deterministic tests and safe-by-default execution; limits are tunable
per environment.

### ADR-0017 — pgvector is the single production vector store; portable column type
**Status:** Accepted (Phase 4)
**Context:** RAG needs vector similarity. The prior phases test on SQLite, but vector
search must run natively in the production database — not emulated in Python, and not a
second datastore.
**Decision:** PostgreSQL + pgvector only (no FAISS/Chroma/Pinecone/Weaviate/Qdrant/
Milvus, no JSON/Python similarity for production). The embedding column uses a custom
`EmbeddingVector` type that renders as `vector(dim)` on PostgreSQL and JSON on SQLite,
so the shared metadata's `create_all` still works for non-knowledge SQLite suites while
Knowledge/RAG tests run against a real PostgreSQL+pgvector service (local + CI). Cosine
similarity uses the `<=>` operator with an HNSW index; queries are tenant-scoped in SQL.
**Consequences:** Production-faithful retrieval and a CI Postgres service; a documented
SQLite/Postgres seam for the vector column, closed by the pg-backed knowledge tests.

### ADR-0018 — Fixed embedding dimension; RAG reaches the model only as tool data
**Status:** Accepted (Phase 4)
**Context:** Mixing embedding dimensions corrupts a vector index, and retrieved
documents are untrusted content that must not act as instructions.
**Decision:** The vector column is fixed platform-wide at `KNOWLEDGE_EMBEDDING_DIMENSION`
(default 1536); ingestion rejects any embedding of another dimension rather than
silently inserting it. RAG is delivered through the `search_knowledge` tool: the runtime
(not the model) resolves the agent's authorized knowledge bases from
`agent_knowledge_sources`, and retrieved passages return as tool results explicitly
labelled untrusted — they can never modify permissions, tool authorization, or security
policy.
**Consequences:** A consistent index and a clean prompt-injection boundary; changing the
embedding model/dimension is an explicit, handled migration rather than a silent break.

---

## Noblen AI 3.0

### ADR-0019 — Noblen AI 3.0 extends the Phase 1–4 platform; no rewrite
**Status:** Accepted (3.0 M1)
**Context:** 3.0 repositions Noblen as AI Workforce & Business Operating Systems.
Phases 1–4 already provide auth, tenancy, RBAC, a model-agnostic gateway, a
versioned agent engine with tools and approvals, and knowledge/RAG.
**Decision:** Build 3.0 incrementally on that core. The remaining Phase 5–9 plan
is re-sequenced into 3.0 milestones (see `PROJECT_ROADMAP.md`).
**Consequences:** Working, tested code is preserved. The 3.0 gaps (traceability,
escalation, permission ceilings, operations) are closed in place.

### ADR-0020 — Every agent execution is a traced run
**Status:** Accepted (3.0 M1)
**Decision:** `AgentRun` + append-only `AgentRunStep` record every model call,
tool call, approval and escalation, with tokens, latency and outcome, but **no
prompt/response text** (consistent with `AI_LOG_PROMPTS`). Content stays in the
conversation. A run's context starts at its own user message, so resumes are
independent of the memory window.
**Consequences:** AI-operations metrics are plain queries. Storage grows with
activity, so retention policies will be needed.

### ADR-0021 — Tool authorization is agent ∩ current human permission; risk is code-declared
**Status:** Accepted (3.0 M1). Refines ADR-0015.
**Decision:** A tool runs only if it is bound to the agent and enabled, **and** the
initiating user's *current* role holds the handler's `required_permission`.
`risk_level` lives on the handler (code), and HIGH always requires approval. On
resume, everything is re-checked, and a non-ACTIVE agent stops the run. Approved
tools run on behalf of the initiator.
**Consequences:** Agents cannot escalate privilege, and catalogue edits cannot
loosen risk. Revocations and pauses take effect immediately.

### ADR-0022 — Exceptions escalate to humans instead of failing
**Status:** Accepted (3.0 M1). Supersedes the 409 behaviour of ADR-0016.
**Decision:** An `escalate_to_human` control tool is always offered to the model.
Refusals, truncation and exhausted budgets end the run `ESCALATED` with a reason.
Only provider outages fail a run (`FAILED`, persisted, translated HTTP error).
**Consequences:** API clients must handle `status: "escalated"`. Operators get a
queue of escalations in the operations overview.

### ADR-0023 — AI Operator role; status-only agent control
**Status:** Accepted (3.0 M1)
**Decision:** Add `OPERATOR`: member rights plus approving actions, activating
and pausing agents (`agent:operate`), and viewing runs, operations and usage. It
cannot author or reconfigure agents. `SUPER_ADMIN` can only be granted by a
platform admin.

### ADR-0024 — Agent tools reach data through injected, scoped capabilities
**Status:** Accepted (3.0 M2). Extends ADR-0015.
**Decision:** Tools still never receive a DB session. Like `knowledge_search`,
work items are exposed as an `AgentWorkspace` built by the runtime and bound to
the run's organization, agent, run and initiator. Services validate that
referenced people are active members of that organization.
**Consequences:** New data-backed tools follow one pattern. Tenancy and
attribution cannot be chosen by model arguments.

### ADR-0025 — The database is the run queue
**Status:** Accepted (3.0 M2). Supersedes the "synchronous only" part of ADR-0016.
**Decision:** Background runs are `QUEUED` rows claimed by workers with
`SELECT … FOR UPDATE SKIP LOCKED`. The worker is the backend image with a different
command. No new broker is introduced (Redis stays for rate limiting and caching).
**Consequences:** Durable, horizontally scalable execution with no new infrastructure.
Throughput is bounded by polling (fine at current scale). A crashed worker leaves
its run `RUNNING` until stale-run recovery is added.

### ADR-0026 — Reference agents are templates, not code
**Status:** Accepted (3.0 M2)
**Decision:** Executive AI and Customer AI are data (instructions, memory, tool
bindings and policies) instantiated into ordinary agents. A template only lists
tools that genuinely work, and planned capabilities are listed separately.
**Consequences:** BusinessOS and IndustryOS packages can ship as template sets.
Improving a template does not change existing agents: the organization cuts a
new version.

### ADR-0027 — Knowledge access is a predicate inside the query; agents read as their initiator
**Status:** Accepted (3.0 M3). Extends ADR-0014.
**Decision:** Knowledge-base and document ACLs (visibility plus user/role grants)
compile to SQL predicates that every knowledge query adds to its `WHERE` clause,
including semantic search before `ORDER BY … LIMIT`. Nothing is filtered after
ranking. The reader is a `Principal` resolved from the database. For agents it is
the user who started the run, intersected with the agent's attached bases.
Admins hold `knowledge:read_all`.
**Consequences:** Restricted content cannot leak through ranking, counts or
top-k starvation. An agent can never read more than its initiator. Grants are
per resource; group principals can be added to the same predicate later.

### ADR-0028 — Tabular knowledge is typed JSON rows queried with a declarative spec
**Status:** Accepted (3.0 M3)
**Decision:** CSV/XLSX sheets are stored as tables with typed columns (`number` or
`text`) and JSON rows, and queried through a small spec (filters, one aggregate,
group-by, order, limit) compiled to SQL JSON-path expressions with bound values.
No model- or user-written SQL is ever executed. The rows are also embedded as
text for semantic search.
**Consequences:** Exact counts and totals from spreadsheets, portable across
PostgreSQL and SQLite, and governed by the same access rules. JSON rows are not
indexed per column, so very large tables are bounded by `KNOWLEDGE_MAX_TABLE_ROWS`.
Joins and external databases need a later design.

### ADR-0029 — Long-term memory is scoped, with explicit write paths
**Status:** Accepted (3.0 M4)
**Decision:** User, agent and organizational memory share one tenant-scoped table
with an explicit `scope`, a subject and provenance. Nothing is remembered
implicitly: people write through the API (their own user memories, or agent and
organization memories with `memory:manage`), and agents write only through tools
bound to them. Those tools can only write about the run's initiator or for the
agent itself. Shared agent memory needs approval by default. User memory is
private to its person and the runs they start, including from admins. Agents in
`PERSISTENT` mode get memories in their system prompt as labelled reference
data; other modes do not. Recall is text matching for now.
**Consequences:** Memory is governed like any other agent action (permission
ceiling, approval, trace, audit) and cannot leak between people through the
agent. Operators cannot inspect someone's private memories. Semantic recall and
per-scope retention can be added without changing the model.
