# Workflow Engine

**Status:** ✅ Implemented (3.0 M5). Code: `backend/app/workflows/`
(`definition.py`, `templating.py`, `service.py`, `engine.py`, `worker.py`) and
`backend/app/api/v1/workflows.py`.

```
Trigger (manual · schedule · event) ─► queued WorkflowRun ─► worker
   ─► step: agent | tool | condition | approval ─► … ─► COMPLETED / FAILED / ESCALATED / CANCELLED
```

The engine reuses the platform instead of duplicating it:

- **Agent step:** an ordinary `AgentRun`, with its trace, tool policy, approvals,
  memory and escalation. It is linked from the step run.
- **Tool step:** a registered handler with the same argument validation, permission
  ceiling and risk policy that agents get.
- **Approval step:** a human decision, with notifications and separation of duties.
- **Audit:** `audit_logs`, plus an append-only step trace per run.
- **Queue:** the database, like agent runs (ADR-0025). There is no new broker.

## Model

| Table | Purpose |
|---|---|
| `workflows` | name, status (`DRAFT → ACTIVE ⇄ PAUSED → ARCHIVED`), active version, trigger (denormalised), `next_run_at`, `run_as_user_id` |
| `workflow_versions` | immutable, numbered definitions (trigger + steps). A run always executes the version it started with |
| `workflow_runs` | status, trigger, input, step outputs (`context`), current step and attempt, the person it acts for, event depth, retry backoff |
| `workflow_step_runs` | one row per step attempt: status, output, error, linked `agent_run_id`, and the decision (who, note) for approvals |

## Definitions

```json
{
  "trigger": {"type": "schedule", "daily_at": "08:00"},
  "steps": [
    {"id": "open", "type": "tool", "tool": "list_tasks", "arguments": {"open_only": true}},
    {"id": "any", "type": "condition", "left": "{{ steps.open.output.total }}",
     "op": "gt", "right": 0, "then": "brief", "else": "end"},
    {"id": "brief", "type": "agent", "agent_id": "<agent id>",
     "input": "Write a short briefing from these tasks: {{ steps.open.output.tasks }}",
     "retry": {"max_attempts": 3, "backoff_seconds": 60}, "on_failure": "escalate"},
    {"id": "check", "type": "approval", "title": "Send today's briefing?",
     "details": "{{ steps.brief.output.text }}"},
    {"id": "send", "type": "tool", "tool": "notify_member",
     "arguments": {"recipient_email": "{{ input.email }}", "title": "Morning briefing",
                   "body": "{{ steps.brief.output.text }}"}}
  ]
}
```

**Triggers**
- `manual`: `POST /workflows/{id}/runs` with an `input` object.
- `schedule`: `every_minutes` (at least `WORKFLOW_MIN_INTERVAL_MINUTES`, default 5) or
  `daily_at` "HH:MM" in the organization's timezone. Missed slots are not replayed:
  a late worker runs once, then moves to the next slot.
- `event`: `task.created` or `task.completed`. The task's id, title, status,
  priority and assignee become the run's `input`.
- `webhook` (M6): external systems POST JSON to `/api/v1/hooks/workflows/{id}` with
  the token shown at activation. See [integrations.md](integrations.md#inbound-webhooks-workflow-trigger).

**Steps**
- Common fields: `id` (lowercase slug), `next` (a step id or `"end"`; the default
  is the next step in the list), `retry` (`max_attempts` 1–5, `backoff_seconds`,
  doubling each attempt), `on_failure` (`fail` | `continue` | `escalate`).
- `agent`: `agent_id` and `input`. Output `{text, agent_run_id}`.
- `tool`: `tool`, `arguments` and `require_approval`. Only tools marked
  `available_in_workflows` can be used: time, organization settings, the task tools
  and `notify_member`, plus (M6) `send_email`, `call_webhook`, the calendar tools and
  the CRM tools. Tools that need an agent's scope (knowledge, data tables, memory)
  and imported MCP tools are used through an agent step instead.
- `condition`: `left`, `op` (`eq ne gt gte lt lte contains exists empty`), `right`,
  `then` and `else`.
- `approval`: `title`, `details` and `on_reject` (without it, a rejection cancels the run).

**References.** `{{ input.x }}`, `{{ trigger.event }}`, `{{ steps.<id>.output.<path> }}`
and `{{ run.id }}` are dotted paths into the run context. There are no expressions,
filters or code: this is not a template language. A value that is exactly one
reference keeps its type (numbers stay numbers); otherwise references are
interpolated as text. A reference with no value fails the step.

Definitions are validated on create and again on activation: schema, step graph
(unique ids, every target exists), reference roots, and agents and tools in the
organization.

## Execution and controls

- **Authority.** A run acts for one person: the starter of a manual run, or the
  activator of a scheduled or event workflow. Before every step, that person must
  still be an active member with `workflow:run`, plus `agent:run` for agent steps
  and the tool's `required_permission` for tool steps. If not, the run fails with
  `not_authorized`, whatever the step's failure policy.
- **Durability.** The worker claims queued runs with `FOR UPDATE SKIP LOCKED` and
  commits after every step.
- **Waiting.**
  - Approval steps, and tool steps with `require_approval`, set the run to
    `WAITING` and notify everyone holding `agent:approve_actions`.
    `POST /workflow-runs/{id}/approve|reject` hands the run back to the worker.
  - HIGH-risk tools always wait for approval.
  - When an agent step's agent waits for its own approval, the workflow waits too.
    The worker re-queues the run once that agent run finishes.
- **Separation of duties.** With `require_independent_approval`, the person the run
  acts for cannot decide its approvals.
- **Failures.** Retryable failures are retried with backoff while attempts remain:
  model or provider errors, and tool crashes. Deterministic failures are not: invalid
  arguments, a failed tool result, a missing reference, an agent escalation. After
  that, `on_failure` applies. `escalate` ends the run `ESCALATED` and notifies
  operators (`agent:operate`) and the person it acts for.
- **Visibility.** Step outputs, including agent answers, are visible to everyone
  with `workflow:view`. Agent steps run with the activator's access, so avoid
  shared workflows over knowledge restricted to a few people.
- **Crash recovery.** A run left `RUNNING` by a crashed worker is re-queued when
  it stopped between steps or during a condition or approval step. If it stopped
  mid-agent or mid-tool, it is escalated, never re-executed (ADR-0033).
- **Kill switch.** Pausing or archiving a workflow stops its triggers. Queued and
  waiting runs are cancelled when next picked up (test runs excepted).
  `POST /workflow-runs/{id}/cancel` cancels a queued or waiting run.
- **Bounds.**
  - At most `WORKFLOW_MAX_STEPS` steps per definition.
  - At most `WORKFLOW_MAX_STEPS_PER_RUN` executed steps per run (loops fail with
    `step_budget_exceeded`).
  - Event chains stop at `WORKFLOW_MAX_EVENT_DEPTH`. A workflow whose steps create
    tasks cannot re-trigger itself forever.
- **Test runs.** People with `workflow:manage` can run any version, including a
  draft, with `version_id`.
- **Audit.** `workflow.created`, `workflow.updated`, `workflow.version_created`,
  `workflow.activated`, `workflow.paused`, `workflow.archived`,
  `workflow.run_queued`, `workflow.run_finished`, `workflow.approval_decided`,
  `workflow.run_cancelled`, and `workflow.tool_executed` for MEDIUM/HIGH tools.

## Permissions

| Permission | Roles | Allows |
|---|---|---|
| `workflow:view` | VIEWER+ | list workflows, versions and runs |
| `workflow:run` | MEMBER+ | start manual runs; required of the person a run acts for |
| `workflow:manage` | MANAGER+ | create, version, activate, pause and archive; test-run drafts |
| `agent:approve_actions` | OPERATOR+ | approve or reject workflow approval steps |
| `agent:operate` | OPERATOR+ | cancel runs |

## Not yet

- Form triggers, and MCP tools as workflow steps. Webhook triggers and the email,
  webhook, calendar and CRM tools arrived in M6 (see [integrations.md](integrations.md)).
- Parallel branches, sub-workflows and a visual builder.
- Cron expressions. Schedules are `every_minutes` or `daily_at` for now.
