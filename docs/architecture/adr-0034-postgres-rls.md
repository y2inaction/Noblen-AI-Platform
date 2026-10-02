# ADR-0034 — PostgreSQL Row-Level Security as a second tenant boundary

**Status:** Proposed (security review, 2026-09; revised in place for Milestone 10,
2026-10). Not implemented.
Implementation contract: [`milestone-10-rls.md`](milestone-10-rls.md).
**Relates to:** ADR-0003 (shared schema + `organization_id`), ADR-0025 (DB-queue worker),
ADR-0033 (stale-run recovery). Summary entry in `DECISIONS.md`.

## Context

Tenant isolation today is enforced only in application code:

- Each tenant table carries `organization_id` (`TenantMixin`). There are 28 such tables
  at `ea61a85`, `team_members` included. The others:
  - `tools` and `roles` have a nullable organization: built-ins are global;
  - `organization_members` has a non-null organization;
  - `audit_logs` has a nullable organization, for authentication events;
  - `agent_tools` has none and reaches its tenant through `agents`.

  The full classification is in the Milestone 10 contract, §5.
- Every service query goes through `tenant_scoped(stmt, Model, org)` or an explicit
  `organization_id ==` filter.
- `get_tenant_context` (`app/api/deps.py`) resolves the active organization from
  `X-Organization-Id` or the token's `org` claim. It re-validates an ACTIVE membership
  on every request.

This works, and cross-tenant tests exist. A single missing filter in a new query would
still leak another tenant's rows, and nothing below the application would stop it.
RLS makes PostgreSQL enforce the same rule a second time.

### How sessions are used today (facts that constrain the design)

| Path | Session | Tenant known when the session opens? | Cross-tenant by design? |
|---|---|---|---|
| API request (`get_db`) | One `AsyncSession` per request. It is shared by `get_current_user`, `get_tenant_context` and the handler. | **No.** The user is loaded and memberships are checked *before* the tenant is known, on the same session. | Membership lookup: yes (by `user_id`). |
| Auth: login, refresh, register, `/auth/me` | `get_db` | No. Registration *creates* the organization. | Yes: memberships across all of the user's orgs. |
| Webhook trigger (`/hooks/workflows/{id}`) | `get_db` | No. The workflow id resolves the tenant. | One lookup by id. |
| Agent worker `claim_next_run` | `SessionLocal()` with `FOR UPDATE SKIP LOCKED` | No. It claims the oldest queued run across all tenants. | Yes. |
| Workflow worker `tick`, `claim_next_workflow_run`, `queue_due_schedules`, `wake_finished_agent_steps` | Same | No | Yes |
| Recovery (`recover_agent_runs`, `recover_workflow_runs`) and memory `purge_expired` | Same | No | Yes |
| Run execution (`runtime.process_queued`, `WorkflowEngine.advance`) | New session per run | Yes (run row). It **commits many times** per run (per step, per tool). | No |
| Registration slug check (`_unique_slug`) | `get_db` | No | Yes: reads `organizations` across tenants |
| Startup tool seeding | `SessionLocal()` | n/a | Writes global (`organization_id IS NULL`) tools |
| Alembic migrations | Owner connection | n/a | Yes (DDL and backfills) |
| Administrative operations (org settings, member roles, knowledge grants) | `get_db` | Yes (tenant-scoped endpoints) | No. No cross-tenant admin API exists, and platform superusers still need membership. |
| Tests | SQLite for the whole suite; only `tests/knowledge/` (about 20 tests) on PostgreSQL, as the superuser | n/a | Fixtures create many orgs |

Revision fact (`ea61a85`): recovery (`recover_agent_runs`, `recover_workflow_runs`) and
`queue_due_schedules` do not only claim. They also write run steps, notifications,
audit rows and new runs for many organizations in the same cross-tenant session.
Run claims are already two-step (claim an id, then run it in a fresh session).

Deployment fact: docker-compose connects the app as `POSTGRES_USER`. That role is the
container's **superuser** and the **owner** of every table. Superusers always bypass
RLS, and owners bypass it unless `FORCE ROW LEVEL SECURITY` is set. **Enabling policies
under the current role would do nothing.** A dedicated application role is a
prerequisite.

## Decision (proposed)

1. **Three database roles.**
   - `noblen_owner` owns the schema and runs Alembic.
   - `noblen_app` is used by the API and by tenant-scoped worker sessions. It is
     `NOSUPERUSER NOBYPASSRLS` and not the owner, with DML grants only.
   - `noblen_system` has `BYPASSRLS` and is used *only* by an explicit
     `system_session()` for the enumerated cross-tenant operations in the table above.

   Configuration:
   - `DATABASE_URL` points to `noblen_app`.
   - `SYSTEM_DATABASE_URL` points to `noblen_system`.
   - `MIGRATIONS_DATABASE_URL` points to the owner. It falls back to `DATABASE_URL` in
     development only.
   - Local compose gets a `docker/postgres/init-roles.sql`.

2. **Per-transaction tenant setting.** The API session stores the tenant in
   `session.info["organization_id"]` once `get_tenant_context` resolves it.
   - A SQLAlchemy `after_begin` session event runs
     `SELECT set_config('app.organization_id', :org, true)` and
     `set_config('app.user_id', :uid, true)`.
   - `is_local = true` is the `SET LOCAL` form. It dies with the transaction, so a
     pooled connection can never carry one tenant's setting into another request.
     This is also compatible with PgBouncer in transaction mode.
   - The event fires at the start of *every* transaction. Worker runs that commit
     per step therefore re-apply the setting on each new transaction.
   - `get_tenant_context` also applies it immediately, because the request's
     transaction has already begun (user lookup).
   - Session-level `SET` is forbidden, and a lint test enforces this.

3. **Policies.** One policy per tenant table, generated from a single list in the
   migration:
   ```sql
   ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;
   ALTER TABLE <t> FORCE ROW LEVEL SECURITY;
   CREATE POLICY tenant_isolation ON <t>
     USING (organization_id = current_setting('app.organization_id', true)::uuid)
     WITH CHECK (organization_id = current_setting('app.organization_id', true)::uuid);
   ```
   - An unset setting yields `NULL`, which matches no row. The design **fails closed**.
   - Special cases:
     - `tools`: `organization_id IS NULL OR = current`, read-only for NULL rows.
     - `organization_members`: `organization_id = current OR user_id = app.user_id`.
       The user half lets auth list a user's own memberships without the system role.
     - `organizations`: `id = current OR id IN (the user's memberships)`.
     - `audit_logs`: org match, plus insert of org-NULL auth events via `WITH CHECK`
       on `organization_id IS NULL AND user_id = app.user_id`.
   - `users`, token tables and RBAC catalogue tables stay without RLS. They are not
     tenant data and are always accessed by primary key or user.

4. **Explicit, small bypass surface.** `system_session()` is used only by:
   - worker claims;
   - schedule queueing;
   - recovery;
   - memory purge;
   - the webhook id → organization lookup.

   Each call claims, looks up or moves status, and returns **identifiers only**
   (`run_id`, `organization_id`). The work itself then happens in a tenant session
   with the organization set. Recovery and schedule queueing are refactored to this
   shape. The registration slug check should need no cross-tenant read. Proposed: rely
   on the unique constraint and retry (contract §16, still to be decided). A test asserts
   that `system_session` is referenced only from an allow-list of modules.

5. **Registration.** The new organization's UUID is generated in Python. The session
   sets `app.organization_id` to it before inserting the organization and the owner
   membership, which satisfies `WITH CHECK` without the system role.

6. **Migrations.** Alembic connects as the owner and creates the policies. `FORCE` also
   applies to the owner, so data migrations (backfills) run their DML under
   `SET LOCAL ROLE noblen_system`. The owner is granted membership in that role.
   Policies never read a "bypass" setting, so no application code can switch
   them off. The helper is `op_bypass_rls()` in `migrations/env.py`. New tenant tables must be added to the
   policy list; a test fails when a `TenantMixin` table has no policy.

7. **Startup check (fail closed).** In `production`, the API and worker refuse to start
   if the `DATABASE_URL` role is a superuser, owns tenant tables or has `BYPASSRLS`.
   The check queries `pg_roles` and `pg_tables`.

8. **Application filters stay.** RLS is defence in depth, not a replacement.
   - `tenant_scoped` remains, as do explicit org filters, which help index use.
   - Intra-tenant rules (roles, owners, knowledge ACLs, private memory and
     conversations) remain in the application and SQL predicates. RLS is tenant-only.
     Encoding role, owner or ACL rules in policies would duplicate
     `knowledge_access.py` in a second language and is rejected.

## Consequences

- One missing filter no longer leaks another tenant's data. A missing tenant setting
  returns nothing instead of everything.
- Operations work: two new roles and connection strings, plus a compose init script.
  Managed Postgres needs manual role creation (documented in `deployment.md`).
- Cost: `set_config` adds two statements per transaction, which is negligible. Policies
  add an `organization_id =` predicate the queries already have.
- pgvector: the HNSW search already filters by organization after the ANN scan. RLS adds
  the same predicate, so recall is unchanged. The existing `ef_search` guidance still
  applies.
- SQLite has no RLS. The default unit suite is unaffected. RLS behavior is tested only in
  the PostgreSQL CI job, which must connect as `noblen_app`.

## Migration risks

| Risk | Mitigation |
|---|---|
| A path that needs cross-tenant reads silently returns nothing (worker idles, webhook 404s, login shows no orgs). | Enumerate the paths (table above). The PostgreSQL suite runs every worker and auth path as `noblen_app` plus `noblen_system`. Deploy behind a flag: policies are created but not yet `FORCE`d, with role-switch observation first. |
| The tenant setting is lost after a mid-run commit. | `after_begin` re-applies it on each transaction; test commits inside a run and then reads. |
| Superuser or owner app role makes RLS a no-op. | Startup check (7). |
| Connection-pool leakage of settings. | Only `is_local=true`; test runs two tenants on a pool of size 1. |
| Data migrations blocked by `FORCE`. | `op_bypass_rls()` helper, documented in `development.md`. |
| A forgotten policy on a new table. | Metadata-driven test: every `organization_id` table has `relrowsecurity` and a policy. |
| Rollback. | Downgrade drops the policies and disables RLS. Roles stay. |

## Rollout order (revised for Milestone 10)

The steps and stop points are in the Milestone 10 contract, §15.

1. A PostgreSQL test harness: the whole suite on PostgreSQL. Policies cannot be tested
   on SQLite, and the suite does not run on PostgreSQL today.
2. Roles, configuration, startup check (warn only). Tests and CI connect as
   `noblen_app`.
3. RLS security tests, failing by design.
4. The session event plus `system_session()`.
5. Cross-tenant paths refactored, with no behavior change.
6. Policy migration(s), enabled. Policies bind `noblen_app` from here, because it is
   neither superuser nor owner.
7. `FORCE`, the data-migration helper, and the startup check becoming fatal in
   production.

Whether the policies ship as one migration or several by class is decided from the
table inventory (contract §16).

## Revision for Milestone 10 (2026-10)

This ADR was written before Milestones 8 and 9. Reviewed against `main` at `ea61a85`,
the decision stands: three roles, transaction-local settings, fail-closed tenant-only
policies, and a small system bypass. The revision changes:
- the facts: 28 tenant tables; recovery and schedules write across tenants; the slug
  check and tool seeding; tests are mostly SQLite;
- the rollout: the PostgreSQL test harness comes first;
- the bypass: claims, lookups and status moves only, with the work in tenant sessions.

Milestones 8 and 9 stay in the application and are shown to be unchanged under RLS.
