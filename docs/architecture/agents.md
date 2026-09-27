# Agents: Controlled Autonomy

**Status:** ✅ Implemented. The Phase 3 engine is documented in
[`../agents.md`](../agents.md). This page covers the Noblen AI 3.0 Milestone 1 layer
on top of it. Code: `backend/app/agents/runtime.py`, `backend/app/models/run.py`,
`backend/app/agents/approvals.py`, `backend/app/api/v1/{agents,approvals,runs}.py`.

Noblen implements **controlled autonomy**, not unrestricted autonomy. An agent plans
and acts through tools, but every action is authorized, validated, policy-checked,
traced, and approved by a human when it is risky. When the agent cannot proceed
safely, it hands the task to a human.

## What a Noblen agent has

| Concern | Where |
|---|---|
| Identity, lifecycle | `Agent` (DRAFT/TESTING/ACTIVE/PAUSED/ARCHIVED), Phase 3 |
| Instructions, model | immutable `AgentVersion` (provider, model, temperature, `configuration.fallback_models`) |
| Tools and agent permissions | `AgentTool` bindings with `AUTO`/`APPROVAL_REQUIRED`/`DISABLED` |
| Knowledge | `agent_knowledge_sources` → `search_knowledge` tool (Phase 4) |
| Memory | conversation window (Phase 3) + run-scoped context (M1) |
| Guardrails | iteration / tool-call / runtime budgets, input limits, risk policy |
| Human approval | `Approval` with approve / reject / modify |
| Escalation | `escalate_to_human` + automatic escalation paths |
| Observability and audit | `AgentRun` + `AgentRunStep`, `ai_usage_records`, `audit_logs` |

## Runs

Every `execute` creates an `AgentRun`, which ends in one of these states:

| Status | When |
|---|---|
| `COMPLETED` | the model gave a final answer |
| `AWAITING_APPROVAL` | paused on a pending approval |
| `ESCALATED` | the agent called `escalate_to_human`; the model refused (`refusal`/`content_filter`); output was truncated; an iteration, tool-call or runtime budget ran out; or the agent was paused/archived while awaiting approval |
| `FAILED` | every model candidate failed. The run is committed before the error propagates, and the API returns a translated 5xx rather than a generic 500 |

Behaviour change from Phase 3: exhausting a budget used to return HTTP 409
(`runtime_limit_exceeded`). It now returns `status: "escalated"` with an
`escalation_reason`, so a human operator can pick up the task.

Each run has an append-only step trace (`MODEL_CALL`, `TOOL_CALL`, `APPROVAL`,
`ESCALATION`, each `SUCCEEDED`/`FAILED`/`DENIED`/`PENDING`). A step records provider,
model, tokens, latency, finish reason, tool risk level and argument *keys*, but never
prompt or response text. Content stays in the conversation (see `AI_LOG_PROMPTS`).

`ExecutionResponse` and the approval `execution` payload now include `run_id` and
`escalation_reason`.

## Tool authorization: an intersection

A tool call executes only if all of the following hold:

1. **Agent permission:** the tool is bound to the agent, enabled, and not `DISABLED`.
2. **Human ceiling:** the *initiating user's current* role (re-read from the
   database at call time, including on resume) holds the handler's
   `required_permission`. An agent cannot be used to exceed its user's rights, and
   revoking a role takes effect immediately.
3. **Valid arguments** against the tool's input schema.
4. **Policy:** `APPROVAL_REQUIRED` pauses for a human. A handler declared
   `risk_level = HIGH` always requires approval, even if its binding says `AUTO`.

Denials are returned to the model as tool errors, traced as `DENIED`, and audited
(`agent.tool_denied`). Disabled tools are not advertised to the model at all.

## Human-in-the-loop

| Decision | Endpoint | Effect |
|---|---|---|
| Approve | `POST /approvals/{id}/approve` (optional `{"note"}`) | executes the model's arguments |
| Modify | `POST /approvals/{id}/modify` `{"arguments", "note"}` | re-validates against the tool schema, then executes the reviewer's arguments |
| Reject | `POST /approvals/{id}/reject` (optional `{"note"}`) | never executes; the model is told a reviewer rejected it, including the note |

On resume, the runtime:

- stops if the agent is no longer ACTIVE (**kill switch**; the run is escalated);
- re-authorizes the call, so a tool disabled or a permission revoked in the
  meantime blocks it;
- executes **on behalf of the initiator**, while the approval records the reviewer;
- answers **every other tool call of the paused model turn** before calling the
  model again. *Fixed bug:* previously the remaining calls were left unanswered,
  which providers such as Anthropic reject.
- rebuilds context from the run's own messages, so the model sees its tool call
  and result in every memory mode. *Fixed bug:* with `memory_mode = NONE`,
  resuming used to reload only the user message.

Decisions are audited (`agent.approval_requested`, `agent.approval_decided`,
`agent.tool_executed`). Deciding requires `agent:approve_actions`.

## Tests

`backend/tests/agents/test_controlled_autonomy.py` contains deterministic scenarios
driven by a scripted model. The provider asserts that every request is well-formed:
every tool call has been answered. The scenarios cover the run trace, escalation
paths, provider outage, HIGH-risk gating, the permission ceiling, tool crashes,
multi-call resume, resume in no-memory mode, execution on behalf of the initiator,
modify, reject notes, the kill switch, disabled tools, operations metrics, tenant
isolation, the operator role and the SUPER_ADMIN grant.

## Background execution (M2)

`POST /agents/{id}/execute` with `"background": true` admits the task (the user
message is stored and a run is created as `QUEUED`) and returns **202** with
`status: "queued"` and a `run_id`. Poll `GET /runs/{run_id}`.

Workers (`python -m app.agents.worker`, the `worker` Compose service) use the
**database as the queue**. Each worker claims the oldest queued run with
`SELECT … FOR UPDATE SKIP LOCKED`, so several workers can run side by side, then
drives it through the same loop as a synchronous run. Trace, approvals,
escalation and audit are identical. The kill switch applies: a queued run whose
agent has been paused escalates instead of starting. Test-version executions
cannot be queued.

## Notifications (M2)

- **Approval requested:** every active member who can decide approvals
  (`agent:approve_actions`) gets an in-app notification linking the approval.
- **Run escalated:** the same people and the run's initiator are notified, with
  the escalation reason.

## Separation of duties (M2)

With the organization setting `require_independent_approval` on
(`PATCH /organizations/current`), the person who started a run cannot approve,
modify or reject its actions. The API returns `403
independent_approval_required`, and this applies to platform admins too. The
setting is off by default, so a single-person organization can still work.

## Limitations

- A worker that dies mid-run leaves the run `RUNNING`. Re-queueing stale runs
  automatically is not implemented yet.
- There are no email or chat delivery channels for notifications yet (in-app only).
- Model turns are replayed from neutral messages. Provider-native content (for
  example Claude thinking blocks) is not preserved yet. This is fine for the
  current default model (`claude-sonnet-4-5`, no extended thinking), and required
  before defaulting to models that think by default.
