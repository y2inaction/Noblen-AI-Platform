# Security Architecture

Security is a product feature. See also [`../security.md`](../security.md) (baseline),
[multi-tenancy.md](multi-tenancy.md) and [tools.md](tools.md).

## Roles

| Spec role | Implemented as | Scope |
|---|---|---|
| Platform Admin | `User.is_superuser` | platform-wide |
| Organization Admin | `ADMIN` | full organization administration |
| **AI Operator** (M1) | `OPERATOR` | member rights + decide approvals, activate/pause agents (`agent:operate`), view runs, operations and AI usage. **Cannot** author or reconfigure agents or administer members |
| Manager | `MANAGER` | operator rights + author/version agents, manage tools, knowledge and team |
| User | `MEMBER` | run agents, use knowledge, view runs |
| Viewer | `VIEWER` | read-only (`*:view`, including `run:view` and `operations:view`) |
| — | `SUPER_ADMIN` | organization wildcard. **Since M1, only a platform admin can grant it.** Previously an org `ADMIN` could grant it, which was a privilege escalation |

The hierarchy is `VIEWER ⊂ MEMBER ⊂ OPERATOR ⊂ MANAGER ⊂ ADMIN`. This is tested.

## Agent security (controlled autonomy)

- **No arbitrary execution:** only code-registered handlers, bound to the agent,
  enabled and not disabled.
- **Least privilege across humans and agents:** the initiating user's *current*
  permission must cover the tool (`required_permission`).
- **Risk policy in code:** HIGH-risk tools always need approval. Catalogue edits
  cannot loosen this.
- **Approval never bypasses authorization:** everything is re-checked at execution,
  and pausing an agent stops runs waiting on approval.
- **Validated inputs:** argument schemas are checked, and reviewer-modified
  arguments are re-validated.
- **Tenancy from context:** tools get the organization from the run, never from
  model arguments.
- **No leakage:** tool crashes expose only the error type. Run traces store no
  prompt/response text. Provider errors are translated to client-safe messages.
- **Retrieved content is untrusted data** and cannot change instructions or
  permissions (Phase 4).

## Audit events added in 3.0 (M1–M5)

`agent.run_started`, `agent.run_completed`, `agent.run_escalated`,
`agent.run_failed`, `agent.run_queued` (M2), `agent.created_from_template` (M2),
`task.created` / `task.updated` (M2), `agent.tool_denied`, `agent.tool_executed` (MEDIUM/HIGH or
approved), `agent.approval_requested`, `agent.approval_decided`, `knowledge.access_changed` (M3),
`memory.created` / `memory.updated` / `memory.deleted` / `memory.user_forgotten` (M4,
scope and subject only, never content), and the `workflow.*` events (M5; see
[workflows.md](workflows.md#execution-and-controls)).

## Knowledge access control (M3)

Restricted knowledge bases and documents are readable only by their creator,
explicit user or role grants, and admins (`knowledge:read_all`). The check is a
SQL predicate inside every knowledge query (listing, fetch, semantic search and
table query), applied before ranking and limits. Agents read as the user who
started the run. Grants are changed with `knowledge:manage_access` and audited as
`knowledge.access_changed`. See [knowledge.md](knowledge.md#access-control-m3).

## Memory privacy (M4)

`USER` memories are readable only by the person they are about and by runs that
person starts. Admins cannot list them. Shared agent memory written by an agent
needs approval by default. Credentials and card numbers are rejected, and
retention is configurable per organization. See [memory.md](memory.md).

## Workflow authority (M5)

A workflow run acts for one person: whoever started it manually, or whoever
activated a scheduled or event-triggered workflow. That person's current role is
re-checked before every step, so removing a role or membership stops their
automations. Workflow approvals honour `require_independent_approval`, and
HIGH-risk tools in workflows always wait for approval. See [workflows.md](workflows.md).

## Separation of duties (M2)

`organizations.require_independent_approval` stops initiators from deciding
their own runs' approvals (`403 independent_approval_required`). It is off by
default and toggled by org admins (`org:manage`).

## Known gaps

- Separation of duties is organization-wide. It is not yet configurable per risk level or per tool.
- Knowledge grants are per resource; there are no group or team principals yet.
- There is no per-organization budget cap on model spend yet.
- PostgreSQL row-level security is planned as defence in depth.
- Per-organization integration credentials (`CredentialReference`) arrive with integrations.
