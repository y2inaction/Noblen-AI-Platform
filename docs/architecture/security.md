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

## Audit events added in 3.0 (M1–M6)

`agent.run_started`, `agent.run_completed`, `agent.run_escalated`,
`agent.run_failed`, `agent.run_queued` (M2), `agent.created_from_template` (M2),
`task.created` / `task.updated` (M2), `agent.tool_denied`, `agent.tool_executed` (MEDIUM/HIGH or
approved), `agent.approval_requested`, `agent.approval_decided`, `knowledge.access_changed` (M3),
`memory.created` / `memory.updated` / `memory.deleted` / `memory.user_forgotten` (M4,
scope and subject only, never content), the `workflow.*` events (M5; see
[workflows.md](workflows.md#execution-and-controls)), and the `integration.*` events
(M6; see [integrations.md](integrations.md#audit)).

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

## Integration credentials (M6)

Connection secrets are encrypted at rest (Fernet, rotating keys; a key is required
in production), write-only through the API, and never reach agents, tools, models,
logs or the audit trail. Outbound calls pass an SSRF guard (public addresses only,
https, no redirects, size and time limits). Imported MCP tools are
organization-scoped and start disabled and HIGH risk. Inbound workflow webhooks use
hashed, rotating tokens and answer 404 to every failure. See
[integrations.md](integrations.md).

## Web UI (M7)

Tokens are kept in `sessionStorage` (one tab) with refresh-once-then-sign-out, and
sign-out revokes the refresh token. API data is rendered only as text. The UI ships
a per-request nonce Content-Security-Policy (no `'unsafe-inline'` scripts), limited
to the API origin, plus frame, sniffing, referrer
and permissions headers. Permission-based hiding is a convenience; the API enforces
everything. See [operating-environment.md](operating-environment.md#session-and-security).

## Conversation privacy (security review)

Conversations hold everything an agent saw and said for someone, including
`recall_memories` results and passages from knowledge restricted to that person.
They are readable, writable and continuable only by their participants, enforced in
SQL (`readable_by`). Other members, admins included, get 404. Being in the same
organization is not enough. Run traces remain organization-visible because they
never contain content.

Run content follows its sources (ADR-0035, ADR-0037). A workflow or agent run reads
with one person's authority, so what it was given and produced can quote their
private memories and restricted knowledge. The run records references to those
sources (never their text). Its content is visible to the person it acts for, and to
another member only if that member can read every recorded source now. Missing,
empty or truncated provenance fails closed, and admins get no bypass. Everyone else
sees the run's metadata: status, steps, timings, error codes, cost, and who
initiated it. The content fields (`input`, `context`, step outputs, escalation
reasons, and tool error text) are returned as `null` with `content_withheld: true`
and a `content_withheld_reason` (`restricted_sources` or `unknown_provenance`).
Approvers see the requests they decide. The rule is enforced in
`app/rbac/visibility.py`, not in the UI. The same file also covers the
approval-decision response, the operations overview and notification bodies.

## Separation of duties (M2)

`organizations.require_independent_approval` stops initiators from deciding
their own runs' approvals (`403 independent_approval_required`). It is off by
default and toggled by org admins (`org:manage`).

## Known gaps

- Separation of duties is organization-wide. It is not yet configurable per risk level or per tool.
- Knowledge grants are per resource; there are no group or team principals yet.
- There is no per-organization budget cap on model spend yet.
- PostgreSQL row-level security is planned as defence in depth.
- The SSRF guard resolves hostnames at check time, so DNS rebinding could race it (M6).
