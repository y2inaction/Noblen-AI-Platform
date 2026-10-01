# ADR-0036 — One data-visibility model for agents, runs, workflows and their outputs

**Status:** Accepted (2026-09, Milestone 8). The participant level for run content is
implemented by ADR-0035. The provenance-based widening is implemented by Milestone 8
(ADR-0037; see `milestone-8-provenance.md`).
**Relates to:** ADR-0034 (RLS: tenant level), ADR-0035 (run content), ADR-0037
(provenance), M3 knowledge ACLs, ADR-0029 memory scopes.

## Context

Visibility rules exist today, but each subsystem invented its own:

| Subsystem | Rule today | Where |
|---|---|---|
| Every resource | Same organization | `tenant_scoped`, `get_tenant_context` |
| Every endpoint | Role → permission (`VIEWER ⊂ MEMBER ⊂ OPERATOR ⊂ MANAGER ⊂ ADMIN`; VIEWER gets every `:view`) | `app/rbac/permissions.py`, `require_permission` |
| Knowledge | Base: ORGANIZATION or RESTRICTED. Document: INHERIT or RESTRICTED. Grants to USER or ROLE; creator; `knowledge:read_all` (ADMIN) | `app/knowledge/access.py` (SQL predicates) |
| Memory | USER (owner only), AGENT and ORGANIZATION (members; managers write) | `memory_service._visible_to_member`, `_visible_to_run` |
| Conversations | Creator and participants only (3d9571f) | `conversations.readable_by` |
| Approvals | `agent:approve_actions`; separation of duties optional | `api/v1/approvals.py` |
| Workflow and agent runs | Organization plus `:view`, **including content** | `api/v1/workflows.py`, `api/v1/runs.py` |

The last row is the inconsistency behind ADR-0035. Because the same idea is spelled
four ways, a new resource has nothing obvious to copy.

## Decision (proposed): six levels, one ordering, two kinds of field

Each level is **narrower than or equal to** the one before it. Every check includes all
broader levels. For example, Owner also requires Organization.

| Level | Who | Mechanism (existing unless noted) | Examples |
|---|---|---|---|
| **T. Tenant / Organization** | Active members of the org | `get_tenant_context` and `tenant_scoped`; RLS as a second enforcement (ADR-0034) | Agent and workflow *definitions*, tool catalogue, tasks, ORG memory, ORGANIZATION knowledge bases, run *metadata* |
| **R. Role** | Members whose role holds permission *P* | `require_permission(P)`; the role map is the single source | Approval queue (`agent:approve_actions`), audit log (`audit:view`), operations metrics, managing agents and workflows |
| **P. Participant** | People taking part in a specific object | Per-resource SQL predicate `readable_by(user)` | Conversations (creator and participants), **run content** (acting person, ADR-0035), an approval request (its approvers) |
| **O. Owner** | Exactly one person | `created_by` / `user_id` equality | USER memories, a person's own restricted document |
| **X. Restricted (ACL)** | Explicit USER or ROLE grants | `knowledge_access_grants` | Restricted knowledge bases and documents |
| **S. System / operator** | The worker and platform superusers | Worker: system session (ADR-0034), IDs only, then acts *as the run's person*. Superusers: `is_superuser` (must still be a member) | Claims, schedules, recovery, purges |

Rules that tie the levels together:

1. **Permissions gate actions and resource types. Levels gate rows and fields.** A
   permission never widens a row's level. For example, `workflow:view` lets you see
   that runs exist, not their content. The only row-widening permission is the
   existing, documented `knowledge:read_all`. No new "read everything" permissions are
   added.
2. **Every run-like resource has metadata fields (level T/R) and content fields
   (level P, or the derived level from ADR-0037).** Content includes input, context,
   outputs, messages, free-text reasons, and payloads. The split is declared once per
   schema (`*Out` for metadata, `*Content` for content), not per endpoint.
3. **Derived data inherits the narrowest level of its inputs** unless it passes through
   a *publication* sink. Publication sinks are: creating a task, AGENT/ORG memory,
   sending outside the org, or a knowledge upload. These are actions the acting person
   is authorized to take, audited, and approval-gated where risk says so. A publication
   is the only way content changes level.
4. **Agents and workflows never hold authority of their own.** They act as exactly one
   person, the initiator or activator. Every read and write is checked as that person
   at execution time, as today. The agent's own configuration (tool bindings, knowledge
   sources, permission ceiling) can only narrow what that person could do.
5. **System level never returns tenant content through the API.** Operator access to
   content, if ever needed, is a separate, audited, break-glass path. None exists and
   none is proposed now.
6. **SUPER_ADMIN role / `is_superuser`** keep today's behavior: permission wildcard
   *within* orgs they are members of. They are subject to the P and O rules, because
   the wildcard is a permission and permissions never widen rows (rule 1). For X they
   inherit `knowledge:read_all` (`resolve_principal` grants it), the one documented
   row-widening permission. There is no cross-tenant platform-admin API. If one is ever
   needed, it is level S, audited and separate.

## Every resource, classified

These are the levels as they should be. **Bold** marks where the code differs today
(fixed by ADR-0035 unless noted).

| Resource | Metadata level | Content level | Notes |
|---|---|---|---|
| Conversations and messages | P | P | Creator and participants (3d9571f). Tool results are stored here as `role="tool"` messages. |
| Memories | T for AGENT/ORG; O for USER | same | USER scope is owner-only, admins included. AGENT/ORG writes need `memory:manage` or approval. |
| Knowledge bases, documents, chunks, tables | T or X | T or X | ORGANIZATION visibility is T. RESTRICTED is X (grants to USER or ROLE), and the creator is O. `knowledge:read_all` (ADMIN, and platform superusers through `resolve_principal`) is the one documented row-widening permission. |
| Agents, agent versions, tool bindings | T (`agent:view`) | T | Definitions are organization-shared by design. System instructions are visible to `agent:view`. |
| Workflows, workflow versions | T (`workflow:view`) | T | Definitions only. Runs are separate. |
| Agent runs (`agent_runs`, `agent_run_steps`) | T (`run:view`) | P (ADR-0035, implemented) | Content is `escalation_reason`, the escalation step's `detail.reason`, and the error text of *failed tool calls*. Platform-written step errors (denials, rejections, a paused agent, model error codes) are metadata. Step `detail` holds argument *keys* only, never values. |
| Workflow runs and step runs | T (`workflow:view`) | P (ADR-0035, implemented); approval requests: R (`agent:approve_actions`) | Content is `input`, `context`, step `output`, and free-text `error` and `decision_note`. `trigger_detail` is metadata (`{"test"}`, `{"webhook": true}`, `{"event"}`, `{"scheduled_for"}`). |
| Tool executions | via their run | via their run | There is no separate `ToolExecution` table. An execution is a trace step (metadata), a tool message in the run's conversation (P), an audit entry (R) and, when gated, an approval (R). |
| Approvals | R (`agent:approve_actions`) | R | `request_payload` is what the approver decides, so it must be visible to them. The resumed run's answer is not shown to the approver: **P**. |
| Tasks | T (`task:view`) | T | A task is a publication. When an agent or workflow creates one from private context, the acting person published it. Provenance comes with ADR-0037. |
| Notifications | O (recipient) | O | Bodies carrying run content (escalation reasons, step errors) go only to the run's person, through `participant_body`. Workflow approval requests go to approvers, who may see the request (R). |
| Integration connections | T (`integration:view`) | T for `config`; **never** for secrets | Secrets are encrypted at rest and returned only as masked "is set" markers. No level can read them through the API. |
| Imported MCP tools | T (`tool:view`) | T | Organization-owned catalogue rows (ADR-0032). |
| Audit log | R (`audit:view`, ADMIN) | R | There is no read API yet. Entries hold actions, ids, tool names and denial reasons, never arguments or outputs. Keep it that way. |
| Usage and cost | R (`ai:view_usage`, `operations:view`) | — | Aggregates only. |
| Operations overview | R (`operations:view`) | P for escalation reasons | Reasons are shown only to each run's person. |

**Refinement found while implementing ADR-0035.** "Content" is decided by *who wrote
the text*, not by the field name. Text written by the platform stays metadata: policy
denials, rejections, error codes. Text written by a tool or the model, or text that
quotes run input, is content. The same `error` column can therefore be metadata on
one row and content on another.

## Mapping existing code onto the model (no renames required)

| Concept | Existing helper | Level |
|---|---|---|
| `tenant_scoped` | `app/db/tenant.py` | T |
| `require_permission` | `app/api/deps.py` | R |
| `conversations.readable_by` | `app/agents/conversations.py` | P |
| `rbac.visibility.sees_run_content` and its presenters | `app/rbac/visibility.py` (ADR-0035) | P |
| `memory_service._visible_to_member` | USER branch | O |
| `knowledge_base_readable` / `document_readable` | `app/knowledge/access.py` | X (includes O via `created_by`) |
| `system_session()` (new, ADR-0034) | — | S |

## Consequences

- One vocabulary for reviews: "which level is this field?" New endpoints answer it in
  the schema.
- No policy engine, no new permission families. The model is five predicates that
  mostly exist already, plus one rule about publication.
- Some operational views get narrower (ADR-0035). Operators keep metadata.
