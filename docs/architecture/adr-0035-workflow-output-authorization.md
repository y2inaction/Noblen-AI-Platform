# ADR-0035 — Workflow and run content is visible to its participants, not the whole organization

**Status:** Implemented (participant rule, §1–§4). Proven by `tests/security/test_run_content_visibility.py`:
14 tests that failed before the fix and pass after it. §5, widening through provenance, waits for ADR-0037.
**Relates to:** ADR-0029 (memory scopes), ADR-0030 (workflows), M3 knowledge ACLs,
the conversation-privacy fix (commit 3d9571f). The model is in ADR-0036; provenance is
in ADR-0037.

## Context: the exact leak path

A **run acts for one person**. Manual runs act for the starter. Scheduled, event and
webhook runs act for the activator (`workflows.run_as_user_id`). That person's
authority is re-checked before every step (`WorkflowEngine._authority`). Agents inside
the run therefore read *as that person*:

- restricted knowledge through the M3 ACL predicates;
- the person's private USER memories (`_visible_to_run`, and `PERSISTENT` injection
  into the system prompt);
- integrations through `IntegrationGateway`.

That part is correct. Authorization is lost when the result is stored and read back:

```
User A (MEMBER) ──POST /workflows/{id}/runs──▶ WorkflowRun(initiated_by=A)
  └─ AgentStep ─▶ AgentRuntime.execute(user_id=A)
       ├─ search_knowledge ─▶ chunks from a document restricted to A      (authorized: A may read)
       ├─ recall_memories / PERSISTENT memory ─▶ A's private memories      (authorized)
       └─ model answer text (may quote/paraphrase both)
  └─ engine._agent / _agent_result ─▶ step_run.output = {"text": answer}   ◀── content copied, no label
  └─ engine._record ─▶ run.context["steps"][id]["output"] = {"text": answer}
  └─ ToolStep templates may copy it again (create_task.description, send_email.body)

User B (VIEWER; holds workflow:view by the ":view" rule)
  └─ GET /api/v1/workflow-runs/{id}  (require_permission(WORKFLOW_VIEW) only)
       └─ WorkflowRunDetail.context + steps[].output  ◀── LEAK: A's restricted content to B
```

Authorization is lost at `app/api/v1/workflows.py::get_run`. It checks only the
organization and `workflow:view`, then serializes `run.context` and every
`WorkflowStepRun.output`. No authorization context is stored with the content that
could be checked on read.

The same pattern appears, with lower severity, in other places:

| # | Endpoint / sink | Content exposed | Gate today | Severity |
|---|---|---|---|---|
| 1 | `GET /workflow-runs/{id}` | `context.steps.*.output`, `steps[].output` (agent text, tool results, rendered approval details) | `workflow:view` (VIEWER) | **High** |
| 2 | `GET /workflow-runs`, `GET /workflow-runs/{id}` | `input` and `trigger_detail` (webhook/event payloads, manual input) | `workflow:view` | Medium |
| 3 | Approval step notification | `body` = rendered details, sent to *every* `agent:approve_actions` holder | Role | Medium (approvers must see what they approve; they may lack the sources) |
| 4 | `GET /runs/{id}`, `GET /runs`, `GET /operations/overview`, and the `run_escalated` notification body | `escalation_reason` (model-written free text); `steps[].error` for tool errors | `run:view` (VIEWER) / `operations:view` / approvers | Low–Medium |
| 5 | Tasks created by agents or workflow tool steps | `title` / `description` derived from private context, published to `task:view` | Organization | By design (publication), but no provenance |
| 6 | `GET /approvals*` | `request_payload` (tool arguments, e.g. an email body) | `agent:approve_actions` | Accepted (approver needs it); add provenance warning |
| 7 | `POST /approvals/{id}/approve\|modify\|reject` | `execution.message` and `execution.escalation_reason`: the resumed run's answer, returned to the approver | `agent:approve_actions` | Medium |

Records audited for this path:
- Workflow APIs (`/workflows*`): definitions only; no run content.
- Workflow-run and step-run APIs (`/workflow-runs*`): rows 1 and 2 above.
- Agent-run APIs (`/runs*`, `/operations/overview`): row 4.
- Tasks (`/tasks*`): row 5.
- Approvals (`/approvals*`), including the response to a decision. That response
  returns the resumed run's result, including the agent's next answer, to the approver.
- Conversations: already participant-only.
- Knowledge and memory APIs: already enforced in SQL.
- Notifications: recipient-only, but some bodies carry run content (rows 3 and 4).
- Audit log: no read API. Its metadata holds names and ids only.
- Tool executions: there is no `ToolExecution` table. Each execution is split across a
  trace step, a tool message in the conversation, an audit entry and an optional
  approval, and each part is covered above.

Already safe: run traces (argument *keys* only), conversations (participants only,
3d9571f), memories (USER scope private), knowledge (ACL predicates in SQL), and
integration secrets (never serialized).

## Decision (proposed)

Separate **metadata** from **content** on every run-like resource (ADR-0036). Authorize
content server-side, in the service layer. Hiding it in the UI is not enough.

1. **Metadata stays organization-visible** under the existing `:view` permissions:
   ids, status, timings, step ids and types, attempts, counts, tokens, cost, error
   *codes*, agent/workflow ids, and who initiated.
2. **Content is participant-visible.** Content means `input`, `trigger_detail`
   payload, `context`, `steps[].output`, `escalation_reason` and free-text `error`.
   The participants of a workflow run or agent run are:
   - (a) the acting person (`initiated_by`);
   - (b) platform operators through an audited path (ADR-0036, level S). There is no
     such API today, and none is added.

   Everyone else gets the metadata with the content fields **omitted**, not blanked
   with fake values. A `content_withheld: true` flag is added (no reason code was
   needed: the only reason is "not the run's person").
3. **Approvers see exactly the approval request.** A person deciding an approval step
   sees that step's rendered `title` / `details`, because governance requires it. They
   do not see the rest of the run's context. The approval notification goes only to
   approvers, so it keeps the request's title and details (refined during
   implementation). Once ADR-0037 lands, the approval view shows whether
   the request derives from sources the approver cannot read.
4. **One authorization function per resource**, used by every path that returns content:
   - `workflow_run_content_visible(run, viewer)`, used by the API, the UI data, exports
     and notifications;
   - `agent_run_content_visible(run, viewer)`.

   These are SQL-expressible predicates, like `readable_by` for conversations. List
   endpoints then never load content they would drop.
5. **Widening later is provenance-based, not role-based** (ADR-0037). Suppose a viewer
   can read *every* source a run's content derives from. Then showing it discloses
   nothing new, and the rule becomes "participant OR can-read-all-sources". Admins are
   not given blanket content access: `knowledge:read_all` covers knowledge, not other
   people's private memories.
6. **Sinks that publish** (tasks, AGENT/ORG memory, outbound email and webhooks) stay
   deliberate publications made under the acting person's authority. The existing HIGH
   approval covers outbound sends, and approval covers `save_agent_memory`. They record
   provenance (ADR-0037) so a leak through them is attributable. They are not
   silently re-restricted.

## Consequences

- VIEWERs and other members keep a useful operations view: statuses, failures, cost.
  They lose the content of other people's runs. Managers who author a workflow see
  content only for runs that act as them, which includes every scheduled, event and
  webhook run of workflows they activated.
- The UI `workflow-runs/[id]` and `runs/[id]` pages must handle `content_withheld`.
- Existing stored outputs need no migration. The rule applies on read.
- The regression test must fail before the fix (ADR test plan, `authorization-test-plan.md`).

## Implementation (2026-09)

**Rule.** It lives in one module, `backend/app/rbac/visibility.py`:
- `Viewer`, built from `TenantContext.viewer`;
- `sees_run_content(viewer, acting_user_id)`, the participant rule;
- `sees_approval_requests(viewer)`, which requires `agent:approve_actions`;
- presenters for workflow runs, agent runs, execution results and escalation
  reasons.

Endpoints call the presenters and never choose fields themselves. There is no
per-endpoint `if user.id != run.initiated_by`. Platform superusers and admins get
no content bypass.

**Field classification** (ADR-0036 levels):

| Field | Level | Who sees it |
|---|---|---|
| ids, `status`, timings, `current_step`, attempts, `steps_executed`, `depth`, step ids/types/statuses, `agent_run_id`, `decision`, `decided_by`, `error_code`, tokens, cost, `initiated_by` | T (metadata) | `workflow:view` / `run:view` |
| `trigger_detail` | T (metadata) | Holds only `{"test"}`, `{"webhook": true}`, `{"event"}` or `{"scheduled_for"}`. Payloads live in `input`. |
| `input` (manual input, webhook and event payloads) | P | the run's person |
| `context` | P | the run's person |
| step `output` | P | the run's person |
| step `output` of an approval request (approval steps; tool steps `WAITING`/`REJECTED`) | R | the run's person and `agent:approve_actions` |
| workflow run and step `error`, `decision_note` | P (R for approval requests) | as above |
| agent run `escalation_reason`; escalation step `detail.reason` | P | the run's person |
| agent run step `error` of a **failed tool call** (tool-written, or quotes rejected arguments) | P | the run's person |
| agent run step `error` of denials, rejections, paused agents, model errors (platform-written) | T (metadata) | `run:view` |
| approval `request_payload` | R | `agent:approve_actions` (what they decide; unchanged) |
| approval-decision `execution.message` / `execution.escalation_reason` | P | the run's person; the approver gets status and ids |
| `operations/overview` `recent_escalations[].reason` | P | the run's person |
| `run_escalated` / `workflow_escalated` notification body | P | the run's person; operators get the title, link and a generic body |
| `workflow_approval_requested` notification title/body | R | approvers, the same people who see the request |
| recovery notification bodies | T | fixed platform text |

**Endpoints protected:**
- `GET /workflow-runs`, `GET /workflow-runs/{id}`;
- `POST /workflows/{id}/runs`, `POST /workflow-runs/{id}/approve|reject|cancel`
  (their response bodies);
- `GET /runs`, `GET /runs/{id}`;
- `GET /operations/overview`;
- `POST /approvals/{id}/approve|modify|reject` (the `execution` payload);
- notifications from agent escalations and workflow step escalations.

**Frontend.** `content_withheld` is typed, and the workflow-run and agent-run pages
explain the withheld content. The UI only mirrors the server.

**Remaining paths (known, accepted or scheduled):**
1. **Publication sinks.** An agent or workflow tool step can publish private content
   under the acting person's authority. Sinks are `create_task` / `update_task`
   (organization-visible tasks), outbound email and webhooks (HIGH risk, approval
   required), and AGENT/ORG memory (approval required). This is by design (ADR-0036
   rule 3). Attribution through `source_run_id` and provenance comes with ADR-0037.
2. **Approvers see the request they decide.** A workflow approval step can template
   private output into its `title`/`details`, and an agent's gated tool call carries
   its arguments. The approver sees them. This is required for governance. ADR-0037
   adds a warning when the request derives from sources the approver cannot read.
3. **Trace `argument_keys`** are model-chosen names. Values are never stored. Low risk.
4. **The rule is conservative.** Managers do not see outputs of others' runs even
   when those derive only from organization-level data. ADR-0037 widens this safely.
5. **No database-level enforcement (RLS) yet** (ADR-0034).
