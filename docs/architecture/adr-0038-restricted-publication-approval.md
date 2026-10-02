# ADR-0038 — Approval before publishing restricted-derived content

**Status:** Implemented (Milestone 9, M9.1–M9.9, 2026-10; PR #7). Follows ADR-0036 and
ADR-0037.
Implementation contract: [`milestone-9-restricted-publication.md`](milestone-9-restricted-publication.md).

## Context

ADR-0036 makes a publication the only way content changes visibility level.
Publication sinks are: creating a task, AGENT/ORG memory, sending outside the
organization, and knowledge upload. ADR-0037, implemented by Milestone 8, records
reference-only provenance for every run and authorizes run content at read time.
It also makes publications attributable: `agent.tool_executed` and
`workflow.tool_executed` record `source_counts`, `sources_truncated`, `acting_role`
and `restricted`.

Publication is allowed, because the acting person may publish what they can read.
ADR-0037 §4 deferred an organization option to route publications derived from
restricted sources through the existing approval flow. Some organizations need that
second pair of eyes before restricted-derived content leaves its person, for example
as an email, a webhook call, a CRM note or an organization-wide task.

An approval request carries the action's arguments. For a restricted publication,
those arguments are restricted-derived content. Showing them to every holder of
`agent:approve_actions`, as the approvals inbox and the ADR-0035 approver exception
do today, would itself disclose the content. The approval must therefore be gated on
the approver's access to the sources, not only on the approval permission.

## Decision

1. **An organization setting.** `require_approval_to_publish_restricted`, default
   off, sits next to `require_independent_approval`. When it is off, behavior is
   unchanged from Milestone 8.
2. **Restricted publication.** A call to an explicit list of publication sinks:
   `create_task`, `update_task`, `notify_member`, `save_agent_memory`, `send_email`,
   `call_webhook`, `create_calendar_event`, `upsert_crm_contact`, `add_crm_note`, and
   administrator-enabled MCP tools. It is restricted when its provenance at the
   moment of the call is restricted in the ADR-0037 sense: a baseline organization
   member could not read every source now. NULL, empty, truncated or unresolvable
   provenance is restricted. Read tools, `save_user_memory` and `forget_user_memory`
   are not sinks. There is no "gate every non-LOW tool" rule. *(Decision C.)*
3. **One request per execution.** When the setting is on, a restricted publication
   requires an approval. If the action already requires one, that request is reused
   and marked as a restricted publication. No second request is created.
   *(Decision D.)*
4. **Approver eligibility.** An approver must hold `agent:approve_actions` **and** be
   able to read every recorded source of the publication now, under the ADR-0037
   read rule. An ineligible approver may learn that a request exists. They cannot see
   its restricted-derived payload, and cannot approve or reject it. If no one is
   eligible, the request stays pending, then fails closed. *(Decision A.)*
5. **The run's person.** They may approve when they satisfy (4).
   `require_independent_approval` remains authoritative and unchanged: when it is
   on, they cannot decide on their own run. *(Decision B.)*
6. **Evaluated twice.** Restriction is evaluated when the request is created and
   again when it is decided. The decision-time evaluation is authoritative,
   including the approver's current access. An approver whose access was revoked
   cannot decide. *(Decision E.)*
7. **No retraction.** Output already published stays published. Its provenance and
   authorization context remain in the audit trail. *(Decision F.)*
8. **Same rule, existing paths.** Direct agent publications and workflow
   publications follow the same rule, through the existing `Approval` flow and the
   existing workflow `WAITING`/decision flow. No parallel approval mechanism.
   *(Decision G.)*
9. **Separate record.** This ADR records Milestone 9. ADR-0037 remains the record of
   Milestone 8 and is not rewritten. *(Decision H.)*

## Consequences

- Organizations can require review before restricted-derived content is published,
  without any change for organizations that leave the setting off.
- The approvals inbox shows some requests as metadata only. A request can wait for
  the one approver who can read its sources, or expire when none can. Failing closed
  is the chosen behavior.
- Approval needs one bulk source check per request read or decision, reusing the
  ADR-0037 resolver. The query count does not depend on the number of references.
- Schema: one organization boolean, and a restricted-publication marker on agent
  approvals and workflow step runs.
- Not done: retraction of published output, workflow approval expiry, per-tool or
  per-organization sink configuration, and any change to Milestone 8.

## Implementation (Milestone 9)

Implemented in steps M9.3–M9.8, each reviewed before the next; validated in M9.9
(contract §17).

- **Setting** (M9.3): `organizations.require_approval_to_publish_restricted`, migration
  `5b9e1c3d7a42`. It is read and written through `/organizations/current` under
  `org:manage`; only a boolean is accepted.
- **One decision for both paths** (M9.4–M9.5): `app/agents/publication.py` holds the
  sink list and `is_restricted_publication`. "Restricted" is ADR-0037's
  `publication_attribution`: a baseline member could not read every source now.
  Sinks are classified by the handler that runs, never by a tool's display name.
  - **Agents:** the run's provenance so far is evaluated before the call executes.
    The binding's own approval is reused and marked (`approvals.restricted_publication`,
    migration `8e4f2a6c1b97`).
  - **Workflow tool steps:** the provenance the step consumes is evaluated before the
    tool runs. The step waits through the existing `WAITING`/decide flow and keeps its
    marker (`workflow_step_runs.restricted_publication`, migration `c3d5e7f9a1b2`), so
    a rejection is honoured even if the sources open up meanwhile.
- **Eligibility and confidentiality** (M9.6): `app/rbac/visibility.py` reuses the M8
  resolver with no participant exception, so unknown provenance has no eligible
  approver. Approve, modify, reject and the workflow decision re-check permission and
  source access at the decision, after the existing separation-of-duties check, and
  refuse with `403 not_eligible`. Approvals carry `payload_withheld`: an ineligible
  approver gets `request_payload: {}` and no `modified_payload` or `decision_note`.
  The workflow approver exception covers a marked step only for an eligible viewer.
- **Audit** (M9.7), per contract §10:
  - `agent.approval_requested` and the new `workflow.approval_requested` record
    `restricted_publication`, `source_counts` and `sources_truncated`.
  - `*.approval_decided` records `restricted_publication` and the decision-time
    `restricted`.
  - The new `*.approval_decision_refused` records the request, the approver and
    `reason: not_eligible`. It is committed before the 403 is returned.
- **Frontend** (M9.8): approvals and workflow runs show a "Restricted Publication"
  badge. A withheld request shows a notice instead of its arguments. The UI follows
  the API and decides nothing.

### Choices made during implementation

- **Migrations:** one per step (§13 of the contract), instead of the single migration
  first planned.
- **Workflow initiator rule:** it keeps its existing `403 permission_denied`. Agent
  approvals keep `independent_approval_required`.
- **Publications from direct agent runs:** a direct agent run records its
  conversation as a source, and a conversation is private to its participants. With
  the setting on, every publication from a direct agent run is therefore gated.

### Known limits

- Workflow waits do not expire (contract §15.1).
- Requests created while the setting is off are not marked (§15.2).
- A workflow tool step is judged on what it consumes. What the tool itself observes
  while running (for example an integration connection) is recorded after it runs.
