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

## Audit events added in 3.0 (M1–M2)

`agent.run_started`, `agent.run_completed`, `agent.run_escalated`,
`agent.run_failed`, `agent.run_queued` (M2), `agent.created_from_template` (M2),
`task.created` / `task.updated` (M2), `agent.tool_denied`, `agent.tool_executed` (MEDIUM/HIGH or
approved), `agent.approval_requested`, `agent.approval_decided`.

## Separation of duties (M2)

`organizations.require_independent_approval` stops initiators from deciding
their own runs' approvals (`403 independent_approval_required`). It is off by
default and toggled by org admins (`org:manage`).

## Known gaps

- Separation of duties is organization-wide. It is not yet configurable per risk level or per tool.
- There is no per-organization budget cap on model spend yet.
- PostgreSQL row-level security is planned as defence in depth.
- Per-organization integration credentials (`CredentialReference`) arrive with integrations.
