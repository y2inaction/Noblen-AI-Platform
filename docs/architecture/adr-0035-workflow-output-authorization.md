# ADR-0035 — Workflow and run content is visible to its participants, not the whole organization

**Status:** Proposed (security review, 2026-09). Not implemented. **Production-blocking.**
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
   with fake values. A `content_withheld: true` flag and a reason code are added.
3. **Approvers see exactly the approval request.** A person deciding an approval step
   sees that step's rendered `title` / `details`, because governance requires it. They
   do not see the rest of the run's context. The notification body carries only the
   title and a link (no details). Once ADR-0037 lands, the approval view shows whether
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
