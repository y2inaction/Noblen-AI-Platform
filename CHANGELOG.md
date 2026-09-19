# Changelog

All notable changes to the Noblen AI Platform are documented here.
The format is loosely based on [Keep a Changelog](https://keepachangelog.com/),
and the project uses Conventional Commits.

## [Unreleased]

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
