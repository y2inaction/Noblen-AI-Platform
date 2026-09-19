# Database

PostgreSQL 16 (+ pgvector for embeddings from Phase 4). SQLAlchemy 2.0 async ORM,
Alembic migrations. Tests run on in-memory SQLite (see
[`DECISIONS.md`](../DECISIONS.md) ADR-0006).

## Multi-tenancy

Every tenant-owned table carries a non-null, indexed `organization_id`
(`TenantMixin`). Services scope all reads/writes through
`app/db/tenant.py::tenant_scoped`, and the `tests/test_tenant_isolation.py`
suite is a permanent gate proving User A cannot read Org B's data.

## Phase 1 tables

| Table                       | Purpose                                            |
|-----------------------------|----------------------------------------------------|
| `organizations`             | Tenants (name, slug, currency, timezone, locale)   |
| `users`                     | Global identities (Argon2 password hash)           |
| `organization_members`      | User ↔ org link with role (grants tenant access)   |
| `roles`, `permissions`      | RBAC catalogue (+ `role_permissions` association)  |
| `teams`, `team_members`     | Tenant-scoped teams                                |
| `refresh_tokens`            | Hashed, revocable, rotating refresh tokens         |
| `email_verification_tokens` | Hashed, expiring email-verification tokens         |
| `password_reset_tokens`     | Hashed, expiring password-reset tokens             |
| `audit_logs`                | Security/business event log                        |

## Planned tables (later phases)

Agents & versions & tools & memory (Phase 3); knowledge bases, documents, chunks,
embeddings (Phase 4); conversations & messages (Phase 3); contacts, leads, lead
activities (Phase 6); workflows, versions, nodes, runs, run logs (Phase 5); tasks,
notifications; integrations, integration credentials, webhooks (Phase 7);
subscriptions, plans, payments, usage records (Phase 7); system settings; AI
provider/model configuration (Phase 2).

## Migrations

```bash
cd backend
alembic revision --autogenerate -m "describe change"   # generate
alembic upgrade head                                   # apply
alembic downgrade -1                                   # revert one
```

Column types are portable (`Uuid`, `JSON`, `DateTime`), so the same migrations
render correctly on both PostgreSQL and SQLite. Postgres-specific features (JSONB,
pgvector, GIN indexes) arrive with the phases that need them.
