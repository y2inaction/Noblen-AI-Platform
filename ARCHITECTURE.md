# Noblen AI Platform — Architecture

> **Status:** Living document. Started in Phase 0 (Discovery). Updated at the end
> of every development phase (see `PROJECT_ROADMAP.md`).

## 1. Guiding principle

**Build ONE reusable platform, not eight independent applications.**

Every commercial Noblen service (Customer Service, Sales, Content, Executive
Assistant, Operations, Custom Agents, Knowledge Intelligence, Training) is a
*configuration* layered on a single shared core: the same authentication,
tenancy, RBAC, AI gateway, agent engine, memory, knowledge base, workflow
engine, integrations, billing, usage metering, and audit logging.

The future product lines (BusinessOS, GovernmentOS, EducationOS, HealthOS,
AgricultureOS, PropertyOS, FinanceOS, IntelligenceOS, AutomationOS) are, in the
same way, packaged bundles of agents + workflows + knowledge on top of this core.

## 2. System context

```
                         ┌──────────────────────────────┐
   Browser / Mobile ───► │        Nginx (reverse proxy) │
   API clients      ───► │  TLS termination, routing     │
                         └───────────────┬───────────────┘
                            /            │            /api
                   ┌────────▼───────┐    │    ┌────────▼─────────┐
                   │  Next.js       │    │    │  FastAPI backend │
                   │  (frontend)    │    │    │  (API-first)     │
                   └────────────────┘    │    └───┬────────┬─────┘
                                          │        │        │
                              ┌───────────▼──┐  ┌──▼───┐  ┌─▼──────────┐
                              │ PostgreSQL   │  │Redis │  │ Celery     │
                              │ (+ pgvector) │  │cache │  │ workers    │
                              └──────────────┘  │queue │  │ + beat     │
                                                └──────┘  └────────────┘
                                                              │
                            External providers (via abstractions):
                            Anthropic · OpenAI · Paystack · Google ·
                            WhatsApp · SMTP
```

## 3. Layered backend architecture

The backend follows a clean, layered separation of concerns:

```
API layer (FastAPI routers, /api/v1/*)
  └─ depends on → Schemas (Pydantic v2 request/response contracts)
  └─ depends on → Services (business logic, transaction boundaries)
        └─ depends on → Repositories / Models (SQLAlchemy 2.0 async)
        └─ depends on → Core (config, security, logging, AI gateway, RBAC)
```

Rules:
- **Routers never touch the ORM directly** for business logic — they call services.
- **Services own transactions** and enforce tenant scoping + permission checks.
- **Core** holds cross-cutting concerns (config, security, logging, rate limiting,
  the AI provider abstraction, tool registry).
- Business logic is **never coupled to a single AI provider or payment provider** —
  both sit behind interfaces.

## 4. Multi-tenancy model

Tenancy is enforced at the **row level with a mandatory `organization_id`** on every
tenant-owned table (shared-database, shared-schema, discriminator-column model).

Core hierarchy:

```
Organization
 ├─ Users (via organization_members, with a Role)
 ├─ Teams → team_members
 ├─ Agents → versions, tools, knowledge sources, memory
 ├─ Knowledge bases → documents → chunks → embeddings
 ├─ Conversations → messages → participants
 ├─ Contacts / Leads → lead_activities
 ├─ Workflows → versions, nodes, runs, run_logs
 ├─ Tasks / Notifications
 ├─ Integrations → credentials, webhooks
 ├─ Subscriptions / Plans / Payments / Usage records
 └─ Audit logs
```

Enforcement strategy (defence in depth):
1. **Application layer** — every query is scoped through a `TenantContext` and
   service methods require an `organization_id`; a shared `scoped()` query helper
   makes cross-tenant reads a deliberate, reviewable exception.
2. **Automated tests** — a dedicated `test_tenant_isolation` suite asserts that
   *User A can never read Organization B's data*. This test is a permanent quality gate.
3. **Future (Phase 10)** — PostgreSQL Row-Level Security (RLS) policies as a second
   line of defence for the highest-risk tables.

A user may belong to multiple organizations; the **active organization** is resolved
per request from the JWT / the `X-Organization-Id` header and validated against the
user's memberships.

## 5. Authentication & authorization

- **Passwords:** hashed with **Argon2id** (memory-hard). Plaintext is never stored or logged.
- **Sessions:** stateless **JWT access tokens** (short-lived) + **refresh tokens**
  (long-lived, stored hashed and revocable in the DB). Rotation on refresh.
- **Email verification & password reset:** single-use, expiring, hashed tokens
  (architecture built in Phase 1; email delivery wired in Phase 7).
- **OAuth:** the auth service is provider-agnostic so social/OAuth logins can be
  added without schema churn.
- **RBAC:** roles (`SUPER_ADMIN`, `ADMIN`, `MANAGER`, `MEMBER`, `VIEWER`) map to
  permission sets. Permissions are checked **server-side only** — the client is never
  trusted. New roles/permissions can be added without code changes to the checker.

## 6. AI gateway (Phase 2)

A single `AIService` exposes: `generate`, `stream`, `embed`, `count_tokens`,
`classify`, `extract`, `summarize`. It sits in front of a `Provider` interface with
concrete `AnthropicProvider` and `OpenAIProvider` implementations. The gateway owns
provider/model selection, retries, timeouts, structured errors, streaming, token &
estimated-cost tracking, and logging. Every AI call is attributable to
`(organization, user, agent, conversation, workflow)` where applicable.

## 7. Agent engine (Phase 3)

Reusable, versioned agents (`DRAFT → TESTING → ACTIVE → PAUSED → ARCHIVED`) with a
system prompt, provider/model config, a **secure tool registry** (each tool has a
schema + permission requirement + audit logging), knowledge sources, and layered
memory (conversation / agent / organization / user). Agents never get raw database
access — only registered, permission-gated tools.

## 8. Knowledge intelligence (Phase 4)

Pipeline: `upload → validate → extract → clean → chunk → embed → store → index`
over PDF/DOCX/TXT/CSV/XLSX. Retrieval assembles context from tenant-isolated chunks
using **pgvector** similarity search, with citations back to source documents.

## 9. Workflow engine (Phase 5)

Triggers (webhook, form, schedule, new lead/message, payment, manual) → nodes
(AI generate/classify/extract/summarize, condition, HTTP, DB action, email,
notification, task) with per-run logging. Long-running/scheduled execution runs on
Celery workers + beat.

## 10. Channels & integrations (Phase 7)

A `Channel` abstraction (WEB, API first; WHATSAPP/EMAIL/INSTAGRAM/FACEBOOK/SMS later)
and an integration/credential model with encrypted credentials. Providers that lack
credentials ship as documented interfaces with mock test providers — never fake
"working" features.

## 11. Human-in-the-loop & AI safety

Every tool/action carries an execution policy: `AUTO | APPROVAL_REQUIRED | DISABLED`.
Irreversible or outward-facing actions (send email/WhatsApp, publish, payment, delete)
default to `APPROVAL_REQUIRED`. Autonomous external communication is off until an org
explicitly enables it.

## 12. Observability

Structured JSON logging carrying `request_id, organization_id, user_id, agent_id,
workflow_id, error_type, timestamp`. `/health` (liveness) and `/readiness`
(dependency checks) endpoints from Phase 1. Metrics + error monitoring hooks prepared.

## 13. Localisation (Nigeria / Africa first)

Currency (`NGN` default but **never hard-coded**), timezone (`Africa/Lagos` default,
**per-organization configurable**), Nigerian + international phone handling, English
now with Hausa/Yoruba/Igbo/Pidgin planned. All money and time values carry explicit
currency/timezone metadata.

## 14. Technology stack

| Concern            | Choice                                             |
|--------------------|----------------------------------------------------|
| Frontend           | Next.js (App Router) + TypeScript + Tailwind CSS   |
| Backend            | Python 3.11 + FastAPI + Pydantic v2                |
| ORM / migrations   | SQLAlchemy 2.0 (async) + Alembic                   |
| Database           | PostgreSQL 16 (+ pgvector for embeddings)          |
| Cache / queue      | Redis + Celery (workers + beat)                    |
| AI                 | Provider abstraction → Anthropic, OpenAI           |
| Vector search      | pgvector (no separate vector DB until required)    |
| Payments           | Paystack (behind a billing-provider interface)     |
| Infra              | Docker + Docker Compose, Ubuntu, AWS EC2, Nginx    |

## 15. Repository layout

```
noblen-ai-platform/
├── backend/            FastAPI application, models, services, migrations, tests
├── frontend/           Next.js dashboard
├── infra/              Nginx config, deployment & backup scripts
├── docs/               architecture, database, api, agents, workflows, security,
│                       deployment, integrations, development
├── .github/workflows/  CI (lint, type-check, test, build, docker build)
├── docker-compose.yml       development stack
├── docker-compose.prod.yml  production stack
├── .env.example        documented environment contract (no secrets)
├── ARCHITECTURE.md · PROJECT_ROADMAP.md · DECISIONS.md · CHANGELOG.md
```

See `docs/` for deep-dives and `DECISIONS.md` for the rationale behind key choices.
