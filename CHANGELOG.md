# Changelog

All notable changes to the Noblen AI Platform are documented here.
The format is loosely based on [Keep a Changelog](https://keepachangelog.com/),
and the project uses Conventional Commits.

## [Unreleased]

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
