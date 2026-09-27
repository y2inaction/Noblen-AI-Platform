# Authorization test plan (for ADR-0034 – ADR-0037)

**Status:** Proposed. These tests do not exist yet unless marked *(exists)*. Each new
test must **fail before its fix**; the PR records the failing run. The tests reuse the
existing fixtures: `tests/conftest.py` (users, orgs, roles),
`tests/agents/conftest.py` (scripted provider), and
`tests/workflows/test_workflow_engine.py` (engine helpers).

## Structure

- `tests/security/` is a new package. Its matrix tests are **parametrized over roles**
  (`VIEWER, MEMBER, OPERATOR, MANAGER, ADMIN`) and over the *relationship* to the
  object (`owner/initiator`, `participant`, `same-org other`, `other-org`).
- RLS tests live in `tests/security/pg/`. They are skipped unless
  `RLS_TEST_DATABASE_URL` is set, and CI provides it in the PostgreSQL job, connecting
  as `noblen_app`.
- Every test asserts **both** the API response and the absence of the secret string
  in the serialized body. It never only checks the status code.

A "canary" string (for example `CANARY-<uuid>`) is planted in the protected source
(restricted document, USER memory, webhook payload). The tests search for it in every
response.

## 1. Tenant isolation

| Test | Expectation |
|---|---|
| Org B member reads org A's agent / run / workflow run / task / memory / knowledge by id | 404 *(exists in part: `test_tenant_isolation.py`, `test_agents_api.py`, `test_registry.py`, `test_memory.py`, `knowledge/test_access_control.py`, `workflows/test_workflow_engine.py`; to be consolidated into one matrix)* |
| Org B lists: no org A ids appear | *(exists for members and some resources; extend to every list endpoint)* |
| `X-Organization-Id` of an org you don't belong to | 403 *(exists)* |
| **RLS:** raw `SELECT` on every tenant table as `noblen_app` with org A set returns no org B rows | pg only |
| **RLS:** no tenant setting → zero rows (fail closed) | pg only |
| **RLS:** pooled connection (pool size 1) alternating tenants never sees the other tenant | pg only |
| **RLS:** the setting survives a mid-run commit (agent run with 2 tool calls; workflow with 3 steps) | pg only |
| **RLS:** worker claim, schedule queue, recovery, purge and webhook lookup work via `system_session`; `system_session` is imported only by allow-listed modules | pg + static |
| **RLS:** every table with `organization_id` has `relrowsecurity` and a policy | pg + metadata |
| Startup check refuses a superuser, owner or `BYPASSRLS` app role in production | unit |

## 2. Role isolation (VIEWER / MEMBER / OPERATOR / MANAGER / ADMIN)

| Test | Expectation |
|---|---|
| Endpoint × role matrix generated from `DEFAULT_ROLE_PERMISSIONS`: every route's `require_permission` returns 403 for roles lacking it | Table-driven over the FastAPI route list, so new routes are covered automatically |
| Metadata endpoints (`/workflow-runs`, `/runs`, `/operations/overview`) are available to VIEWER | 200, *with content fields absent* |
| `agent:approve_actions` is required for approval lists and decisions; separation of duties | *(exists)* |
| No role (ADMIN included) reads another member's USER memory, private conversation or run content | 404 / withheld |

## 3. Owner isolation

- USER memory: only the owner lists or reads it; ADMIN cannot *(exists in part)*.
- Conversations: only the creator and participants *(exists: `test_conversation_privacy.py`)*.
- **Run content:** only the initiator sees `input`, `context`, `steps[].output` and
  `escalation_reason` of their own agent and workflow runs.

## 4. Knowledge isolation (the propagation cases)

The same restricted document (readable by A only, canary inside) is used in every row.

| Path | Viewer | Expectation |
|---|---|---|
| Direct search / get | B (MEMBER) | not found *(exists, M3)* |
| Agent run by B | B | the agent cannot retrieve it *(exists)* |
| Agent run by A → conversation | B | 404 *(exists)* |
| **Agent run by A → `GET /runs/{id}`** | B (VIEWER) | metadata only; the canary is absent in `escalation_reason` / errors |
| **Workflow run by A (agent step) → `GET /workflow-runs/{id}`** | B (VIEWER, MEMBER, MANAGER, ADMIN) | the canary is absent; `content_withheld: true` — *the ADR-0035 regression test* |
| Same, viewer = A | A | the canary is present |
| **Workflow step output templated into an approval step** | approver C | C sees only the approval request, not the rest of the context; the notification body has no details |
| **Tool step (`create_task`) from A's restricted output** | B | the task is visible (publication), with `source_run_id` set and the audit event marked `restricted: true` |
| **Run trace steps** | B | argument keys only, never values *(exists)* |
| After ADR-0037: B is granted the document | B | the workflow output becomes visible (derived visibility) |
| After ADR-0037: the document is deleted | B | withheld (fail closed); A still sees it |

## 5. Memory isolation

Same matrix as section 4, with A's USER memory as the canary. Paths:
- `recall_memories`;
- `PERSISTENT` injection;
- the run's conversation (tool messages) *(exists: `test_conversation_privacy.py`)*;
- workflow agent step → output;
- run detail and run list (escalation reason, failed-tool errors);
- `GET /operations/overview`;
- notification bodies;
- the approval-decision response;
- `save_agent_memory` publication (approval required *(exists)*, provenance recorded).

## 6. Workflow isolation

- Workflow *definitions* are visible organization-wide (T level). Editing requires
  `workflow:manage` *(exists)*.
- A run acts only as its initiator or activator. Losing the role or membership
  mid-run stops the run *(exists: `_authority`)*.
- Re-activation by another manager changes `run_as_user_id`, and later runs act as
  the new activator. There is an audit event *(exists)*. Add: an old run keeps its
  original `initiated_by`.
- Webhook payload (`input`) is withheld from non-participants.
- Cancel and approve permissions on a run are unchanged.

## 7. Provenance (ADR-0037)

For one agent run and one workflow run that read a document, a memory and an
integration:

| Field | Assert |
|---|---|
| organization | equals the run's org |
| user / initiator | `initiated_by` equals the starter or activator; `acting_role` equals the role at start |
| agent | `agent_id`, `agent_version_id` set |
| workflow | `workflow_id`, `version_id`, `step_id` set |
| tool | trace step names match the tools called |
| source | `sources` contains exactly the document, memory and connection ids actually returned (not the ones searched but filtered out) |
| authorization context | `acting_role` is recorded; the `can_read_sources` decision is deterministic across roles |
| timestamp | `created_at` ≤ `finished_at` |
| bounds | 501 references → `sources_truncated`, and non-participants are withheld |
| no duplication | `sources` never contains content text (the canary is absent from the provenance JSON) |
