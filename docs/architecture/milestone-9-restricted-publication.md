# Milestone 9: Restricted publication approval (implementation contract)

**Status:** Proposed (M9.1, documentation only). It specifies ADR-0038, which is
**Proposed**. It builds on ADR-0035 and ADR-0036, and on ADR-0037 as implemented by
Milestone 8 ([`milestone-8-provenance.md`](milestone-8-provenance.md)). Milestone 8 is
closed and is not changed by this milestone. RLS (ADR-0034) remains out of scope.

## 1. Objective

Milestone 8 made every publication **attributable**: `agent.tool_executed` and
`workflow.tool_executed` record `source_counts`, `sources_truncated`, `acting_role`
and `restricted`. Publication itself is still allowed, because the acting person may
publish what they can read.

Milestone 9 adds the option ADR-0037 §4 deferred. An organization can require a
human approval before a run publishes content derived from **restricted** sources.
The approval reuses the existing agent approval and workflow decision flows. Its
payload is restricted-derived content, so only approvers who can read every source
may see it or decide it.

## 2. Approved decisions (A–H)

These decisions are approved and recorded in ADR-0038.

- **A. Approver eligibility.** An approver must hold `agent:approve_actions` **and**
  be able to read **every** recorded source of the publication **now**, under the
  M8.7 read rule (§6 of the M8 contract). An approver who lacks any source can neither
  see the restricted-derived payload nor approve or reject the request. They may
  learn that a request exists, without its content or any source detail. If no
  eligible approver exists, the request stays pending, then fails closed.
- **B. The run's person.** They may approve when they satisfy A. The existing
  `require_independent_approval` stays authoritative and unchanged: when it is on,
  they cannot decide on their own run's actions.
- **C. Publication sinks.** An explicit list (§3.1), not "every non-LOW tool".
- **D. One request per execution.** If the action already requires approval, that
  request is reused, marked as a restricted publication, and given the stricter
  eligibility and payload rules. No second request is created.
- **E. Evaluated twice.** Restriction is evaluated when the request is created and
  again when it is decided. The decision-time evaluation is authoritative, including
  the approver's current access. Revoked access means the approver cannot decide.
- **F. No retraction.** Output already published stays published. Its provenance and
  authorization context remain in the audit trail. Retraction is out of scope.
- **G. Same rule for agents and workflows.** Enforced in the existing agent approval
  path and the existing workflow `WAITING`/decision path. No parallel mechanism.
- **H. ADR-0038.** A new ADR records M9. ADR-0037 stays the record of Milestone 8.

## 3. Definitions

### 3.1 Publication sink

A tool call whose effect makes content visible beyond the run's person. The sinks
are exactly:

| Tool | Effect |
|---|---|
| `create_task` | an organization-visible task |
| `update_task` | changes an organization-visible task |
| `notify_member` | content delivered to another member |
| `save_agent_memory` | an AGENT memory, readable by the organization |
| `send_email` | sent outside the organization |
| `call_webhook` | sent outside the organization |
| `create_calendar_event` | written to an external calendar |
| `upsert_crm_contact` | written to the external CRM |
| `add_crm_note` | written to the external CRM |
| MCP tools an administrator has enabled | sent to a remote MCP server |

Not sinks: read tools (`search_knowledge`, `list_data_tables`, `query_data_table`,
`recall_memories`, `list_tasks`, `list_calendar_events`, `find_crm_contacts`,
`get_current_time`, `get_organization_settings`), `save_user_memory` (private to the
person), `forget_user_memory`, and `escalate_to_human`. The list is declared once, in
code, next to the tool definitions, and is not configurable per organization. A new
tool is not a sink until it is added to the list.

### 3.2 Restricted publication

A call to a publication sink whose provenance, at the moment of the call, is
**restricted** in the M8.8 sense (`visibility.publication_attribution`): a baseline
member of the organization (no role permissions, no grants, no private data, in no
conversation) could not read every source now. NULL, empty, truncated or
unresolvable provenance is restricted (fail closed).

The provenance of a call is:
- **agent runs:** the run's provenance when the call is made (its collector
  snapshot, including the conversation it read);
- **workflow tool steps:** the provenance the step consumed upstream
  (`WorkflowEngine._consumed`) plus what it observed itself.

## 4. Organization setting

- `organizations.require_approval_to_publish_restricted`: boolean, default `false`.
  It sits next to `require_independent_approval`, is read and written through the
  existing organization settings API, and is changed under the same permission.
- **Off (default):** behavior is exactly as after Milestone 8. Publications are
  attributed, never gated by this milestone.
- **On:** every restricted publication requires an approval under §5–§9.
- Turning the setting off does not resolve requests already marked as restricted
  publications. They keep the §6–§7 rules until decided, expired or cancelled.

## 5. Approval lifecycle

1. **Detection.** Before a sink executes, the platform computes whether it is a
   restricted publication (§3.2). This happens after the existing permission and
   argument checks, and before execution.
2. **Request.**
   - If the action already requires approval (binding mode `APPROVAL_REQUIRED`, a
     HIGH-risk tool, or a workflow step with `require_approval`), that request is
     created as usual and marked as a restricted publication (D).
   - Otherwise one approval request is created for it, marked the same way.
3. **Notification.** Approvers are notified that a request exists. The notification
   carries platform-written text only (tool name, agent or workflow name), never the
   payload or any source detail.
4. **Decision.** An eligible approver (§6) approves, modifies-and-approves (agent
   approvals) or rejects. Eligibility and restriction are evaluated again at this
   moment (§8).
5. **Outcome.**
   - Approved: the tool executes once, with the approved arguments, through the
     existing resume path. The `*.tool_executed` audit event records the M8.8
     attribution as today.
   - Rejected: nothing is published. The run continues exactly as after any rejected
     approval today (agents receive the rejection as the tool result; workflows
     follow the step's rejection handling).
   - Expired (agent approvals) or cancelled (workflow runs): nothing is published.

## 6. Approver authorization

For a request marked as a restricted publication, a viewer is an **eligible
approver** only if all of these hold at that moment:
1. they hold `agent:approve_actions` in the organization;
2. they can read every recorded source of the publication, under the M8.7 rule
   (`can_read_sources`), with the same no-bypass and fail-closed semantics. Unknown
   provenance can be read by no one, so such a request has no eligible approver;
3. separation of duties allows it: with `require_independent_approval` on, the run's
   person is not eligible (B). This check is the existing one, unchanged.

The run's person is eligible when 1–3 hold.

## 7. Payload confidentiality

For a request marked as a restricted publication, the restricted-derived payload is
visible only to eligible approvers (§6) and to the run's person:
- agent approvals: `request_payload`, `modified_payload` and `decision_note` in
  `GET /approvals` and `GET /approvals/{id}`;
- workflow runs: the waiting tool step's request (`output.details.arguments`), which
  ADR-0035 currently shows to every approver.

Everyone else holding `agent:approve_actions` sees that a request exists, with its
metadata only: id, status, tool name, risk level, timestamps, the requester, and the
fact that it is a restricted publication. Responses never say which source is
unreadable, mirroring `content_withheld_reason`.

## 8. Decision-time reauthorization

- When a decision is submitted, the platform re-evaluates (E):
  - the approver's eligibility (§6), with their current permissions and current
    access to every source;
  - the publication's provenance and restriction.
- An ineligible approver's decision is refused, and the request is unchanged.
  Approving and rejecting are both refused. Refusals reveal no source detail.
- If, at decision time, the publication is no longer restricted (for example, its
  sources became organization-readable), it may be approved by any holder of
  `agent:approve_actions`, subject to separation of duties. This is the authoritative
  decision-time evaluation.
- Approval applies to the request as decided. Execution follows on the existing
  resume path. No new evaluation happens between approval and execution.

## 9. Interaction with existing mechanisms

- **`require_independent_approval`:** unchanged. It applies to restricted-publication
  requests exactly as to every other approval (B).
- **Agent approvals:** the existing `Approval` row, statuses (`PENDING`, `APPROVED`,
  `REJECTED`, `EXPIRED`), expiry (`AGENT_APPROVAL_TTL_SECONDS`), approve, modify and
  reject endpoints, and resume path are reused. Modify-and-approve is an approval:
  the approver must be eligible.
- **Workflow approvals:** the existing tool-step wait (`WAITING`, `decide`,
  `/workflow-runs/{id}/approve` and `/reject`) is reused. Explicit `approval` steps
  are human checkpoints, not publications; they are unchanged.
- **Read-time visibility (M8.7):** unchanged. A run's content follows its sources
  regardless of this milestone.

## 10. Audit

Content is never written to the audit trail. The existing events gain metadata:

| Event | Added metadata |
|---|---|
| `agent.approval_requested` | `restricted_publication`, `source_counts`, `sources_truncated` |
| `agent.approval_decided` | `restricted_publication`, decision-time `restricted` |
| `workflow.approval_requested` (new, for tool steps marked as restricted publications) | `step`, `tool`, `restricted_publication`, `source_counts`, `sources_truncated` |
| `workflow.approval_decided` | `restricted_publication`, decision-time `restricted` |
| `*.approval_decision_refused` (new) | the request, the approver, and `reason: not_eligible`, never which source |
| `*.tool_executed` | unchanged: the M8.8 attribution |

## 11. Fail closed

- NULL, empty, truncated or unresolvable provenance makes a publication restricted
  (§3.2) and leaves it with no eligible approver (§6).
- A request with no eligible approver stays pending:
  - agent approvals expire (`EXPIRED`);
  - workflow runs wait until decided or cancelled (see §15, question 1).
- Errors while evaluating restriction or eligibility deny: no approval, no
  publication.
- Turning the setting off never releases or exposes an already-marked request (§4).

## 12. Invariants (each has a test in M9.2)

- **P1.** With the setting off, behavior is identical to Milestone 8.
- **P2.** With it on, no restricted publication executes without a recorded approval
  by an eligible approver.
- **P3.** At most one approval request exists per tool execution.
- **P4.** An ineligible approver never sees the payload and cannot decide.
- **P5.** Eligibility is evaluated at decision time. A revocation before the decision
  makes the approver ineligible.
- **P6.** Rejection, expiry and cancellation publish nothing.
- **P7.** Agent and workflow publications follow the same rules.
- **P8.** Audit records carry attribution and decisions, never content or source
  detail.
- **P9.** Milestone 8 behavior (provenance, read rule, attribution) is unchanged.

## 13. Schema (M9.3–M9.5)

- `organizations.require_approval_to_publish_restricted` (boolean, default false).
- A persisted restricted-publication marker on the request:
  `approvals.restricted_publication` and `workflow_step_runs.restricted_publication`
  (boolean, default false).

Each column arrives with the step that first needs it, in its own reversible
migration with no backfill:

| Step | Migration | Column |
|---|---|---|
| M9.3 | `5b9e1c3d7a42` | `organizations.require_approval_to_publish_restricted` |
| M9.4 | `8e4f2a6c1b97` | `approvals.restricted_publication` |
| M9.5 | `c3d5e7f9a1b2` | `workflow_step_runs.restricted_publication` |

The marker makes the §4 rule (turning the setting off does not release a request)
enforceable. The
provenance used for eligibility is the existing `sources` / `sources_truncated` of
the agent run or workflow step run. No other schema change is planned.

## 14. Commit sequence and stop points

Each step stops for review.

| Step | Content | Stop point |
|---|---|---|
| M9.1 | this contract and ADR-0038 (Proposed) | **stop and report** |
| M9.2 | security tests, failing by design | **stop and report the failing evidence** |
| M9.3 | organization setting and its migration | |
| M9.4 | sink list; agent path: detect, request, mark (one request per execution); approval marker migration | |
| M9.5 | workflow path: the same rule through `WAITING`/`decide`; step marker migration | |
| M9.6 | approver eligibility and payload confidentiality (§6–§8) | |
| M9.7 | audit metadata (§10) | |
| M9.8 | frontend: the existing approvals inbox shows metadata-only requests | |
| M9.9 | final docs, ADR-0038 finalized, validation | **stop and report; no merge without explicit approval** |

### Checklist

- [x] M9.1 contract and ADR-0038 (Proposed)
- [x] M9.2 security tests (failing)
- [x] M9.3 organization setting
- [x] M9.4 agent path
- [x] M9.5 workflow path
- [x] M9.6 eligibility and confidentiality
- [ ] M9.7 audit
- [ ] M9.8 frontend
- [ ] M9.9 final docs and validation

### Gates before asking to merge (M9.9)

- ruff, format and mypy clean.
- Full SQLite suite, and the full PostgreSQL + pgvector suite.
- Migration upgrade → downgrade → upgrade on PostgreSQL.
- Frontend lint, typecheck and build.
- The existing browser walkthrough with no console errors.
- A live cross-role check of the §12 invariants.
- CI green on the final head.
- Only then is ADR-0038 finalized.

## 15. Open questions (to decide before M9.2)

1. **Workflow waits have no expiry.** Agent approvals expire after
   `AGENT_APPROVAL_TTL_SECONDS`. Waiting workflow runs stay `WAITING` until decided
   or cancelled (`agent:operate`). This contract keeps that: a restricted workflow
   publication with no eligible approver waits until cancelled. Adding a workflow
   expiry would be a new feature.
2. **Requests made while the setting is off.** An action that already requires
   approval for other reasons (for example a HIGH-risk `send_email`), with the
   setting off, is not marked. Its payload stays visible to every approver, as
   today (ADR-0035). This contract does not tighten that, because P1 requires
   unchanged behavior with the setting off.

## 16. Out of scope

- Retraction or recall of published output (F).
- RLS (ADR-0034).
- Workflow approval expiry (§15).
- Changing Milestone 8: provenance capture, the read rule, attribution.
- Changing `require_independent_approval`.
- Per-organization or per-tool configuration of the sink list.
- Content labels, classifiers, encryption changes, policy engines.
- New product features and unrelated refactoring.
