# Noblen AI Platform — Project Roadmap

This roadmap tracks the phased delivery of the platform. **We do not build
everything at once.** Each phase ends with the Quality Gate (tests, lint, types,
builds, migrations, Docker, security, docs) before it is marked complete.

Legend: ✅ done · 🚧 in progress · ⬜ not started

---

## Noblen AI 3.0 — milestones (supersede Phases 5–9 below)

Noblen AI is becoming **AI Workforce & Business Operating Systems**. Phases 0–4 are
the foundation and remain valid. The work after Phase 4 is re-sequenced into the
milestones below; see [`docs/architecture/overview.md`](docs/architecture/overview.md).
Phases 10–11 (hardening, deployment) still apply.

- ✅ **M1: Controlled autonomy & AI operations.** Run traces (`agent_runs`,
  `agent_run_steps`); escalation (`escalate_to_human`, and budgets, refusal and
  truncation escalate); tool risk levels and the initiator permission ceiling;
  approval modify, notes, correct multi-call and no-memory resume, and a kill switch;
  gateway fallback chain; AI Operator role; `/runs` and `/operations/overview`;
  SUPER_ADMIN grant fix.
- ✅ **M2: First AI Workforce + execution hardening.** Tasks and notifications
  (+ APIs); workforce tools (`create_task`, `list_tasks`, `update_task`,
  `notify_member`); Executive AI and Customer AI templates; background execution
  on a DB-queue worker; approval and escalation alerts; separation of duties.
  *Carried forward:* provider-native replay. (Stale-run recovery landed in Phase 10.)
- ✅ **M3: Knowledge permissions + structured retrieval.** Knowledge-base and
  document visibility with user/role grants, enforced in SQL before ranking;
  agents read as their initiator; archived bases unsearchable; CSV/XLSX tables
  with a declarative query API and agent tools (`list_data_tables`,
  `query_data_table`).
- ✅ **M4: Memory.** Scoped user, agent and organizational memory (`memories`) with
  explicit write paths: owner and manager APIs, and the agent tools `save_user_memory`,
  `save_agent_memory` (approval by default), `recall_memories` and `forget_user_memory`.
  User memory is private to its person; secrets are rejected; per-organization
  retention with worker purge; `PERSISTENT` agents load memory into context.
- ✅ **M5: Workflow engine.** Versioned workflows (`/workflows`, `/workflow-runs`) with
  manual, schedule (`every_minutes`, `daily_at` in org timezone) and task-event
  triggers; agent, tool, condition and approval steps; `{{ }}` value references (no
  code); retries with backoff; `fail`/`continue`/`escalate`; run-as authority
  re-checked per step; kill switch, step budget and event-depth guard; executed by
  the existing DB-queue worker.
- ✅ **M6: Integrations.** Encrypted, per-organization connections (Fernet, key
  rotation, write-only secrets) and an SSRF guard; `send_email` (SMTP),
  `call_webhook` (signed), CalDAV calendar and HubSpot CRM tools with declared risk
  levels; remote MCP adapter (Streamable HTTP) importing organization-owned tools
  that admins enable and risk-rate; webhook triggers for workflows. *Not yet:*
  OAuth connections (Google/Microsoft), messaging channels, Paystack.
- ✅ **M7: Operating-environment UI.** Next.js UI over the API: dashboard (live
  operations overview), workforce (agents, templates, run a task), approvals inbox
  (agent actions: approve, reject, or edit arguments and approve; workflow
  decisions), agent and workflow run traces, workflows (create from JSON,
  activate/pause, run), tasks, integrations (test, MCP tool governance),
  notifications, and an organization switcher. Permission-aware via
  `GET /organizations/current/access`. Browser smoke test in `frontend/e2e/`.

---

## Phase 0 — Discovery ✅
- [x] Inspect repository & git status (empty repo, fresh branch)
- [x] Check runtime versions (Python 3.11, Node 22, Docker 29, Postgres 16)
- [x] `ARCHITECTURE.md`
- [x] `PROJECT_ROADMAP.md`
- [x] `DECISIONS.md`
- [x] Propose the exact Phase 1 implementation plan (see below)

## Phase 1 — Foundation 🚧
- [x] Repository structure (`backend/`, `frontend/`, `infra/`, `docs/`)
- [x] `.gitignore`, `.env.example`, documentation set
- [x] Docker + Docker Compose (dev + prod), Nginx, Postgres, Redis
- [x] FastAPI application skeleton (config, logging, error handling, CORS, headers)
- [x] Health (`/health`) and readiness (`/readiness`) checks
- [x] Database layer (SQLAlchemy 2.0 async) + Alembic migrations
- [x] Core models: organizations, users, memberships, roles, permissions, teams,
      refresh tokens, email-verification & password-reset tokens, audit logs
- [x] Authentication: register, login, refresh, logout, verify-email + reset scaffolding
- [x] RBAC enforcement (server-side permission checks)
- [x] Tenant isolation utilities
- [x] Next.js + TypeScript + Tailwind scaffold (login + dashboard shell)
- [x] Tests: health, auth, RBAC, **tenant isolation**
- [x] GitHub Actions CI (lint, type-check, test, build, docker build)

## Phase 2 — AI Core ✅
- [x] Provider abstraction (`AIProvider`) + Anthropic + OpenAI + mock providers
- [x] AI gateway (`generate` / `stream` / `embed`) with provider & model selection
- [x] Normalized request/response/stream types (no vendor objects leak out)
- [x] Normalized error hierarchy + provider exception mapping
- [x] Bounded retries with backoff, explicit timeouts
- [x] Configurable model-pricing registry + estimated-cost tracking
- [x] AI usage metering model, service, migration (tenant-scoped)
- [x] AI API: `/ai/generate`, `/ai/stream` (SSE), `/ai/embed`, `/ai/usage`
- [x] AI RBAC permissions + per-organization rate limiting
- [x] Test suites (providers, gateway, usage, API, security) — no real API calls
- [x] `docs/ai-core.md` + documentation updates

## Phase 3 — Agent Engine ✅
- [x] Agent registry: CRUD, slug uniqueness, server-enforced lifecycle state machine
- [x] Immutable agent versioning + active-version resolution
- [x] Agent Runtime: bounded generate→tool loop over the Phase 2 AI Gateway
- [x] Normalized tool-calling added to the AI Core (Anthropic/OpenAI/mock, additive)
- [x] Secure tool registry + sandboxed execution contract + safe built-in tools
- [x] Tool permission modes (AUTO / APPROVAL_REQUIRED / DISABLED), server-enforced
- [x] Conversations, messages (operational only), participants — tenant-scoped
- [x] Scoped memory modes (NONE / CONVERSATION / PERSISTENT), bounded
- [x] Human-in-the-loop approval subsystem (state machine + resume)
- [x] Agent RBAC permissions + per-org runtime guardrails
- [x] API: agents, versions, execute, conversations, tools, approvals
- [x] Usage attribution extended with agent/version/conversation (non-breaking)
- [x] Alembic migration (upgrade/downgrade/upgrade verified)
- [x] Test suites (registry, runtime, API, approvals, tenant isolation) — no real API calls
- [x] Minimal internal Agent Playground page + documentation

## Phase 4 — Knowledge + RAG ✅
- [x] Knowledge domain: knowledge_bases, knowledge_documents, document_chunks,
      document_embeddings (pgvector), agent_knowledge_sources — tenant-scoped
- [x] Ingestion pipeline: load → extract (TXT/MD/PDF/DOCX) → clean → chunk → embed → store
- [x] Embeddings via the Phase 2 AI Gateway; fixed 1536-dim vector column
- [x] pgvector cosine retrieval (`<=>`) + HNSW index; tenant-scoped SQL, never Python scoring
- [x] Citations (document/chunk/page + similarity); empty retrieval reported honestly
- [x] `search_knowledge` built-in tool bridging Agent Runtime → RAG (server-side agent authz)
- [x] Prompt-injection defense: retrieved content is untrusted tool data, never instructions
- [x] Storage abstraction (local; S3-ready), file validation, idempotency, atomic re-ingest
- [x] Knowledge RBAC + API (KBs, documents, ingest, search, agent-KB sources)
- [x] Alembic migration (CREATE EXTENSION vector + HNSW; upgrade/downgrade/upgrade verified)
- [x] CI runs the Knowledge/RAG suite against real PostgreSQL + pgvector
- [x] `docs/knowledge.md` + documentation updates

## Phase 5 — Workflow Engine (→ 3.0 M5)
- [ ] Workflow models, triggers, nodes, conditions, execution engine, logs, scheduler

## Phase 6 — Commercial Agents (→ 3.0 M2 AI Workforce)
- [ ] Customer Service, Sales, Content, Executive Assistant, Operations agents

## Phase 7 — Integrations (→ 3.0 M6)
- [ ] Email, Google Calendar, WhatsApp, Paystack, webhooks, CRM (interfaces + mocks first)

## Phase 8 — Client Dashboard (→ 3.0 M7)
- [x] Dashboard, agents (workforce), runs, approvals, workflows, tasks, integrations,
      notifications (3.0 M7)
- [ ] Conversations, leads, content, knowledge, analytics, billing, settings,
      agent/connection authoring forms

## Phase 9 — Admin ⬜
- [ ] Organizations, users, usage, AI costs, agents, executions, subscriptions,
      payments, system health, audit logs

## Phase 10 — Production Hardening ⬜
- [ ] Security & tenant-isolation audit, dependency audit, performance, backups,
      error-handling & logging review, PostgreSQL RLS

## Phase 11 — EC2 Deployment ⬜
- [ ] Inspect existing server, isolated compose project, `/opt/noblen-ai-platform`,
      HTTPS via Let's Encrypt, backups — **without disturbing existing apps (e.g. CIDU)**

---

## Exact Phase 1 implementation plan

**Goal:** a running, tested, multi-tenant foundation with secure auth and RBAC.

1. **Scaffolding & tooling**
   - `backend/pyproject.toml` (FastAPI, SQLAlchemy 2.0 async + asyncpg, Alembic,
     Pydantic v2, pydantic-settings, PyJWT, argon2-cffi, structlog, ruff, mypy,
     pytest, pytest-asyncio, httpx, aiosqlite for tests).
   - `frontend/` Next.js + TS + Tailwind, ESLint.
2. **Core** — `config.py` (env-driven settings), `logging.py` (structured JSON +
   request id), `security.py` (Argon2 hashing, JWT encode/decode), `exceptions.py`
   (typed API errors), `rate_limit.py` (basic per-IP limiter), secure-headers &
   request-id middleware.
3. **Database** — async engine/session, declarative `Base`, `TimestampMixin` +
   `TenantMixin`, tenant scoping helper. Alembic env wired to settings.
4. **Models** — organizations, users, organization_members, roles, permissions
   (+ association), teams, team_members, refresh_tokens, email_verification_tokens,
   password_reset_tokens, audit_logs. Portable column types so tests run on SQLite.
5. **RBAC** — permission constants, default role→permission map, a `require_permission`
   dependency that resolves the active org membership and checks server-side.
6. **Auth service + API** — register (creates org + owner admin), login, refresh
   (rotating), logout (revoke), me, verify-email + password-reset scaffolding. Audit
   logging on security events.
7. **Org/User API** — list/get org, list members, update role (permission-gated).
8. **Health** — `/health` (liveness) and `/readiness` (DB check).
9. **Migrations** — initial Alembic revision creating all Phase 1 tables.
10. **Tests** — `test_health`, `test_auth` (register/login/refresh/me),
    `test_rbac` (viewer denied, admin allowed), `test_tenant_isolation`
    (User A cannot read Org B). SQLite-backed, fast, no external services.
11. **Docker & CI** — backend & frontend Dockerfiles, dev/prod compose, Nginx,
    GitHub Actions pipeline. Quality Gate run, then commit + push + draft PR.
