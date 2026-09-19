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
