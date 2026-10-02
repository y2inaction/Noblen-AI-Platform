# Milestone 10: PostgreSQL Row-Level Security (implementation contract)

**Status:** Proposed (M10.1, documentation only). It specifies ADR-0034, revised in
place for this milestone and still **Proposed**. Milestones 8 and 9 are closed and are
not changed by this milestone. Baseline: `main` at `ea61a85` (the Milestone 9 merge).

## 1. Objective

Tenant isolation is enforced only in application code today. One query that forgets
its `organization_id` filter would return another tenant's rows, and nothing below
the application would stop it.

M10 makes PostgreSQL enforce the tenant boundary a second time, underneath the
existing application filters:
- A tenant session sees and writes only its own organization's rows.
- A missing tenant setting returns nothing.
- The few deliberate cross-tenant operations go through one small, audited bypass.

It also gives the project a PostgreSQL test foundation. Today the automated suite runs
almost entirely on SQLite.

## 2. Approved planning decisions

| Decision | Direction |
|---|---|
| M10 subject | RLS (ADR-0034) |
| First implementation stage | A PostgreSQL application-role test harness |
| Database roles | Three: owner, application, system (`BYPASSRLS`) |
| Cross-tenant jobs | Claim or look up ids in a system session; do the work in tenant sessions |
| `FORCE ROW LEVEL SECURITY` | In M10, with the production startup check becoming fatal |
| Validation | The whole suite on PostgreSQL as the application role, plus dedicated RLS tests |
| ADR | ADR-0034 is updated in place (still Proposed) |
| M8 / M9 | Not redesigned; shown to remain correct under RLS |
| Frozen M8/M9 wording | Not changed by M10 (§16, item 8) |

The remaining choices are resolved in §16. Only policy migration packaging stays open, until M10.7.

## 3. Baseline facts (`main` at `ea61a85`)

These are facts from the code, not proposals.

- **Application enforcement.**
  - Service queries use `tenant_scoped(stmt, Model, org)` (47 calls) or explicit
    `organization_id ==` filters.
  - 11 files fetch rows by primary key alone (`db.get`): `AgentRun` (7 times),
    `Organization` (4), `User` (3), `WorkflowRun` (2), `Workflow` (2) and
    `WorkflowVersion` (1).
  - This is the class of query RLS backs up.
- **Sessions.**
  - One engine and one session factory (`app/db/session.py`).
  - `get_db` yields one session per request. `get_tenant_context` validates the
    membership on that same session, so the tenant is unknown when the session opens.
- **Database role.** In `docker-compose.yml` and `docker-compose.prod.yml`, the API,
  the worker and Alembic all connect as `POSTGRES_USER`. That role is the container's
  superuser and owns every table. Superusers always bypass RLS: **policies would do
  nothing under the current role.**
- **Tenant setting.** None exists. There is no `set_config`, `current_setting`,
  `after_begin` hook, or policy anywhere.
- **Run execution.** `AgentRuntime` and `WorkflowEngine` commit many times per run (per
  step, per tool), on a fresh session per run.
- **Tests.**
  - The shared fixture (`tests/conftest.py`) always creates an in-memory SQLite engine.
  - Only `tests/knowledge/` (about 20 tests) uses PostgreSQL (`KNOWLEDGE_TEST_DATABASE_URL`).
  - CI connects it as the `postgres` superuser.
  - The M8 and M9 validation records' "full PostgreSQL + pgvector suite" is therefore
    mostly SQLite.
- **Roles table.** `roles.organization_id` is nullable. No code creates `roles` rows
  for an organization. Roles are names checked against `DEFAULT_ROLE_PERMISSIONS`.

## 4. Security boundary

- RLS enforces the **tenant boundary only**: a row belongs to one organization (or is
  global), and a tenant session sees only its own (plus permitted global rows).
- Everything inside a tenant stays in the application, exactly as today:
  - roles and permissions;
  - knowledge ACLs (M3);
  - private memory and conversations;
  - the M8 read rule;
  - M9 approver eligibility.

  Encoding them in policies would duplicate them in a second language, and is out of
  scope (§17).
- The application filters stay. RLS is defence in depth, not a replacement.

## 5. Table inventory and classification

Every table at `ea61a85`, by class. The policy shapes are in §9.

| Class | Meaning | Tables |
|---|---|---|
| **T** | Tenant table (`TenantMixin`, `organization_id NOT NULL`) | 28 tables: `agent_knowledge_sources`, `agent_run_steps`, `agent_runs`, `agent_versions`, `agents`, `ai_usage_records`, `approvals`, `conversation_messages`, `conversation_participants`, `conversations`, `document_chunks`, `document_embeddings`, `integration_connections`, `integration_tools`, `knowledge_access_grants`, `knowledge_bases`, `knowledge_documents`, `knowledge_table_rows`, `knowledge_tables`, `memories`, `notifications`, `tasks`, `team_members`, `teams`, `workflow_runs`, `workflow_step_runs`, `workflow_versions`, `workflows` |
| **H** | Hybrid: global rows (`organization_id IS NULL`) plus organization rows | `tools` (built-in catalogue plus MCP tools an organization imports); `roles` (global templates; no organization rows are created today, §3) |
| **S** | Special organization tables | `organizations` (the tenant itself), `organization_members` (read by user before the tenant is known), `audit_logs` (nullable organization: authentication events have none) |
| **J** | Join table without `organization_id` | `agent_tools` (reaches its tenant through `agents`) |
| **N** | Not tenant data: no RLS | `users`, `refresh_tokens`, `password_reset_tokens`, `email_verification_tokens`, `permissions`, `role_permissions`, `alembic_version` |

Rules:
- A metadata test fails if a table is in no class.
- A metadata test fails if a class-T, H or S table has no enabled, forced policy
  (from M10.7).
- New tables must be classified when they are added.
- `agent_tools` is isolated through its agent (§16, item 2).

## 6. Database roles

| Role | Used by | Properties |
|---|---|---|
| `noblen_owner` | Alembic only | Owns the schema; runs DDL and creates policies |
| `noblen_app` | API and tenant-scoped worker sessions | `NOSUPERUSER NOBYPASSRLS`; not an owner; DML grants only |
| `noblen_system` | `system_session()` only (§8) | `BYPASSRLS`; DML grants only |

- **Configuration:**
  - `DATABASE_URL` connects as `noblen_app`;
  - `SYSTEM_DATABASE_URL` connects as `noblen_system`;
  - `MIGRATIONS_DATABASE_URL` connects as the owner, and falls back to `DATABASE_URL`
    only outside production.
- **Who creates the roles:** a compose init script (`docker/postgres/init-roles.sql`)
  locally, and documented manual steps for managed Postgres. Alembic never creates
  roles.
- **Startup check:** the API and worker inspect `pg_roles` and table ownership. If the
  `DATABASE_URL` role is a superuser, owns tenant tables or has `BYPASSRLS`, they warn
  from M10.3 and refuse to start in production from M10.8.

## 7. Tenant-session semantics

- **The settings.** A transaction carries `app.organization_id` and `app.user_id`. They
  are set with `set_config(name, value, true)`, which is transaction-local (`SET
  LOCAL`). They die at commit or rollback and never leak to the next user of a pooled
  connection.
- **Where they come from.** The session holds the tenant in `session.info`. A
  SQLAlchemy `after_begin` event applies it at the start of **every** transaction.
  - **API:** `get_tenant_context` sets the tenant once the membership is validated, and
    applies it at once, because the transaction has already begun.
  - **Authentication paths:** set only `app.user_id`.
  - **Registration:** sets the new organization's id, generated in Python, before
    inserting it.
  - **Worker run sessions:** set the claimed run's organization.
- **Commits.** Runs commit many times (§3). Each new transaction re-applies the
  settings through the event.
- **Fail closed.** With no setting, `current_setting(..., true)` is `NULL`, which
  matches no row.
- **Forbidden.** A session-level `SET`; a test enforces this.

## 8. Cross-tenant paths and the system session

`system_session()` uses `SYSTEM_DATABASE_URL`. A test asserts it is imported only from
an allow-list of modules. It may only claim, look up or move status. The work itself
then runs in a tenant session.

| Path | Today (fact) | Required in M10 |
|---|---|---|
| Agent run claim (`claim_next_run`) | Claims an id, then runs it in a fresh session | Claim in the system session; run in a tenant session |
| Workflow run claim (`claim_next_workflow_run`) | Same | Same |
| Schedule queueing (`queue_due_schedules`) | Locks due workflows **and inserts their runs** in one cross-tenant session | Claim due workflow ids in the system session; queue each run in a tenant session |
| Waking finished agent steps (`wake_finished_agent_steps`) | One cross-tenant `UPDATE` of waiting runs | A status-only update in the system session (allowed), or per tenant |
| Stale-run recovery (`recover_agent_runs`, `recover_workflow_runs`) | Locks stale runs **and writes steps, notifications and audit rows** for many organizations | Claim ids in the system session; escalate each in a tenant session |
| Memory purge (`purge_expired`) | Reads every organization's retention setting, then deletes per organization, all in one session | List organization ids and retention in the system session; delete in each tenant's session |
| Webhook trigger (`/hooks/workflows/{id}`) | Loads the workflow by id before the tenant is known | Look up the organization by workflow id in the system session; verify and queue in a tenant session |
| Registration (`_unique_slug`) | Reads `organizations` across tenants to find a free slug | No cross-tenant read: the unique constraint plus a retry on collision (§16, item 3) |
| Login, refresh, `/auth/me` | Read a user's memberships before a tenant is set | Allowed by the `organization_members` and `organizations` policies (§9), not the system role |
| Startup tool seeding | Writes global (`organization_id IS NULL`) tools on the app's connection | Through `system_session()` (§16, item 6) |

Each refactor keeps behavior unchanged. Existing tests already cover these paths.

## 9. Policies, rollout and `FORCE`

Policy shapes, per class:
- **T:** `USING (organization_id = current_setting('app.organization_id', true)::uuid)`
  and the same `WITH CHECK`.
- **H:** `USING (organization_id IS NULL OR organization_id = <current>)`, and
  `WITH CHECK (organization_id = <current>)`. Only the system or owner role can write
  global rows.
- **S:**
  - `organizations`: `id = <current> OR id IN (the user's ACTIVE memberships)`;
    insert `WITH CHECK (id = <current>)`.
  - `organization_members`: `organization_id = <current> OR user_id = <app.user_id>`.
  - `audit_logs`: an organization match, plus inserting rows with no organization
    `WITH CHECK (organization_id IS NULL AND user_id = <app.user_id>)`.
- **J:** `agent_tools`: `USING` and `WITH CHECK` with `EXISTS (SELECT 1 FROM agents
  WHERE agents.id = agent_tools.agent_id AND agents.organization_id = <current>)`
  (§16, item 2).

Rollout:
1. Policies are created and enabled (M10.7). From then on they bind `noblen_app`.
2. `FORCE` follows (M10.8), so the owner is bound too. Data migrations then run their
   DML under `SET LOCAL ROLE noblen_system`, through a helper (`op_bypass_rls()`).

Policies never read a "bypass" setting, so no application code can switch them off.

## 10. Migration and rollback

- **Migrations.** Policies are created by Alembic as the owner, from the single
  inventory in §5. Whether that is one migration or several by class is decided
  after the inventory (§16, item 1). Every migration is reversible.
- **Rollback, in order of speed:**
  1. Point `DATABASE_URL` back at the owner role. This disables enforcement instantly,
     with no schema change. It is documented, and for emergencies only.
  2. Downgrade the policy migration(s). This disables RLS and drops the policies.
  3. Revert the configuration and code. The roles may stay.
- **Order.** The existing staged migrations are not edited. M10 only adds migrations.

## 11. Compatibility with Milestones 8 and 9

M8 and M9 are not changed. M10 must show:
- **M8:** the resolver already filters every lookup by organization, and treats
  references to another organization as unknown. Under RLS those rows are invisible,
  which is still unknown, so the outcome is unchanged.
- **M9:** the restricted-publication gate, eligibility and audit run in tenant sessions.
  The refusal audit commits before its 403. The next transaction re-applies the
  tenant through §7.
- **Proof:** the M8 and M9 security suites, the browser walkthrough and the M9 live
  cross-role check pass unchanged, with the app connected as `noblen_app` under
  `FORCE`d policies.

## 12. PostgreSQL test harness (M10.2)

- **Selection.** The shared fixture can run the whole suite on PostgreSQL, chosen by an
  environment variable (`TEST_DATABASE_URL`). SQLite stays the default for fast
  local runs.
- **Schema.** On PostgreSQL the schema comes from Alembic (`upgrade head`), not
  `create_all`, so tests see the real migrations.
- **Connection role.** M10.2 runs as the existing superuser, and only proves the suite
  runs on PostgreSQL. From M10.3 the app connects as `noblen_app`, and migrations and
  fixtures run as the owner.
- **Isolation between tests:** a fresh database or schema per test module or session,
  migrated by Alembic; no shared global cleanup (§16, item 4).
- **CI.** The SQLite job stays. A PostgreSQL job runs the **whole** suite, as the
  application role from M10.3 on.

## 13. Validation gates (before ADR-0034 is marked Implemented)

- ruff, format and mypy clean.
- The full SQLite suite.
- The full suite on PostgreSQL + pgvector, connected as `noblen_app`, with policies
  `FORCE`d.
- RLS tests (§14), each failing before the change that satisfies it.
- Migrations upgrade → downgrade → upgrade on PostgreSQL; `alembic check` shows only
  the known drift.
- The startup check: it rejects a superuser, an owner or `BYPASSRLS` role in
  production, and accepts `noblen_app`.
- Frontend lint, typecheck and build. The browser walkthrough passes with no console
  errors, against a stack running as the three roles.
- A live cross-role check, run as `noblen_app`:
  - two organizations;
  - the M8 read rule and the M9 gate and eligibility, unchanged;
  - a query with no tenant filter returns nothing from the other organization.
- CI green on the final head.

## 14. Invariants (each gets a test, from M10.4)

- **R1.** Every class-T, H and S table has RLS enabled and forced, with its policy.
  Every table is classified.
- **R2.** A transaction without `app.organization_id` reads no tenant rows and cannot
  write any.
- **R3.** A tenant session cannot read or write another organization's rows, even
  through a query with no application filter (`db.get`, raw SQL).
- **R4.** Tenant settings never outlive their transaction. Two tenants alternating on
  a one-connection pool never see each other's rows.
- **R5.** Settings are re-applied after every commit inside a run.
- **R6.** `system_session()` is used only by the allow-listed modules, and only for
  §8 operations.
- **R7.** In production the app refuses to start as a superuser, an owner or a
  `BYPASSRLS` role.
- **R8.** Milestone 8 and 9 behavior is unchanged (§11).
- **R9.** Authentication works without the system role: register, login, refresh,
  `/auth/me` and switching organization.

## 15. Steps and stop points

Each step stops for review.

| Step | Content | Stop point |
|---|---|---|
| M10.1 | This contract and the ADR-0034 revision (documentation only) | **stop and report** |
| M10.2 | PostgreSQL test harness: whole suite on PostgreSQL (still the superuser); CI job | **stop and report the results** |
| M10.3 | Three roles, configuration, init script, startup check (warn only); tests and CI connect as `noblen_app`; no policies yet, no behavior change | |
| M10.4 | RLS security tests R1–R9, failing by design | **stop and report the failing evidence** |
| M10.5 | Tenant-session plumbing (§7); `system_session()` and its allow-list | |
| M10.6 | Cross-tenant paths refactored (§8); behavior unchanged | |
| M10.7 | Policy migration(s), enabled (§9), from the §5 inventory | |
| M10.8 | `FORCE`; data-migration helper; fatal startup check in production | |
| M10.9 | Deployment and development docs, final validation (§13), ADR-0034 Implemented | **stop and report; no merge without explicit approval** |

### Checklist

- [ ] M10.1 contract and ADR-0034 revision
- [ ] M10.2 PostgreSQL test harness
- [ ] M10.3 roles and configuration
- [ ] M10.4 RLS security tests (failing)
- [ ] M10.5 tenant-session plumbing
- [ ] M10.6 cross-tenant paths
- [ ] M10.7 policies enabled
- [ ] M10.8 `FORCE` and fatal startup check
- [ ] M10.9 final docs and validation

## 16. Decisions (resolved after M10.1 review, 2026-10)

1. **Policy migration packaging.** Still open by design. It is decided at M10.7, from
   the actual policy inventory and M10.4's results: one migration, or several by class.
2. **`agent_tools` (class J).** Tenant isolation goes through the row's agent: a policy
   with `EXISTS` on `agents` in the current organization. No `organization_id` column
   is added for RLS.
3. **Registration slug uniqueness.** Rely on the database's unique constraint and
   handle a collision by retrying with a suffix. No system-session lookup and no
   cross-tenant read.
4. **Test isolation on PostgreSQL.** A fresh PostgreSQL database or schema per test
   module or session, where practical, migrated by Alembic. No shared global cleanup
   between tests.
5. **CI shape.** Keep the SQLite job for the fast suite, and add the full PostgreSQL
   suite run as the application role. SQLite is not replaced.
6. **Writer of global rows.** Startup tool seeding runs through `system_session()`
   (the system role).
7. **`roles` (class H).** Keep the hybrid classification: global rows plus
   organization rows, with the class-H policy. Document that no organization rows
   exist today. No custom-role feature.
8. **Old M8/M9 wording.** The M8 and M9 records stay frozen. Correcting the "full
   PostgreSQL + pgvector suite" wording (§3) is a separate decision, outside M10.

## 17. Out of scope

- Rules inside one tenant in policies: roles, ACLs, private memory, conversations,
  M8 visibility, M9 eligibility.
- Schema-per-tenant or database-per-tenant.
- Encryption changes.
- PgBouncer deployment.
- Automation for managed Postgres (documentation only).
- Any SQLite behavior change.
- Workflow approval expiry.
- The other Phase 10 items: backups, performance, logging, dependency audit.
- Admin screens (Phase 9).
- Deployment (Phase 11).
- Changes to Milestones 8 or 9.
- New product features and unrelated refactoring.
