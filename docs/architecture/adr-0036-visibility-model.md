# ADR-0036 — One data-visibility model for agents, runs, workflows and their outputs

**Status:** Proposed (security review, 2026-09). Not implemented.
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
   *within* orgs they are members of. They are subject to the same P/O/X rules. The
   wildcard is a permission, and permissions never widen rows (rule 1).

## Mapping existing code onto the model (no renames required)

| Concept | Existing helper | Level |
|---|---|---|
| `tenant_scoped` | `app/db/tenant.py` | T |
| `require_permission` | `app/api/deps.py` | R |
| `conversations.readable_by` | `app/agents/conversations.py` | P |
| *new* `workflow_run_content_visible` / `agent_run_content_visible` | ADR-0035 | P |
| `memory_service._visible_to_member` | USER branch | O |
| `knowledge_base_readable` / `document_readable` | `app/knowledge/access.py` | X (includes O via `created_by`) |
| `system_session()` (new, ADR-0034) | — | S |

## Consequences

- One vocabulary for reviews: "which level is this field?" New endpoints answer it in
  the schema.
- No policy engine, no new permission families. The model is five predicates that
  mostly exist already, plus one rule about publication.
- Some operational views get narrower (ADR-0035). Operators keep metadata.
