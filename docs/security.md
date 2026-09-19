# Security

Security is a first-class concern from Phase 1. See also spec §28 and
[`ARCHITECTURE.md`](../ARCHITECTURE.md) §5.

## Secrets
- All secrets come from environment variables; `.env` is git-ignored and
  `.env.example` documents the contract. No secrets are ever committed, logged,
  or exposed to the frontend (only `NEXT_PUBLIC_*` reaches the browser).

## Passwords & tokens
- Passwords hashed with **Argon2id** (`argon2-cffi`); never stored/logged in plaintext.
- **Access tokens**: short-lived JWTs (stateless).
- **Refresh tokens**: long-lived, stored only as SHA-256 hashes, **rotated** on
  every refresh and revocable on logout.
- Email-verification and password-reset tokens are single-use, expiring, and hashed.

## Authorization
- **RBAC** enforced server-side via `require_permission(...)`. The client cannot
  self-authorize; every role and membership is validated against the database.
- Roles: `SUPER_ADMIN`, `ADMIN`, `MANAGER`, `MEMBER`, `VIEWER` (extensible).

## Tenant isolation
- Mandatory `organization_id` on every tenant-owned row; queries scoped via
  `tenant_scoped`. Automated tests prove cross-tenant reads are impossible.
- PostgreSQL Row-Level Security is planned as defence-in-depth (Phase 10).

## Transport & HTTP hardening
- Secure response headers (`X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, `Permissions-Policy`).
- CORS restricted to configured origins.
- Per-client rate limiting (sliding window; Redis-backed in production).
- Request validation via Pydantic; SQLAlchemy parameterises queries (no string SQL).
- TLS termination at Nginx with Let's Encrypt in production (Phase 11).

## Auditing & error handling
- Security-significant events (`auth.login`, `auth.register`, `member.role_changed`,
  …) are written to `audit_logs`.
- Errors are never silently swallowed; handlers return structured JSON and log with
  a request id. Internal details are not leaked to clients.

## Human-in-the-loop (AI safety)
- Tools/actions carry a policy `AUTO | APPROVAL_REQUIRED | DISABLED`. Irreversible
  or outward-facing actions default to approval. Autonomous external communication
  is disabled until an organization opts in (implemented with the agent engine).
