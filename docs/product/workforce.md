# AI Workforce

**Status:** the framework is implemented (Phase 3 engine + Noblen AI 3.0 M1
controlled autonomy). **Executive AI and Customer AI are available as templates
(M2).** The other reference agents are planned.

All workforce agents are **configurations of one agent framework**, not separate
codebases. Each one is an `Agent` with an immutable version (instructions, model,
fallbacks, memory), tool bindings with per-tool policies, and attached knowledge
bases. They all run on the shared runtime with approvals, escalation, run traces
and audit.

## Available now (M2)

Create either from `POST /api/v1/agent-templates/{key}/instantiate`. This creates
an ACTIVE agent with its tools bound. Attach knowledge bases to ground answers.

| Template | What it does today | Tools (policy) | Planned additions |
|---|---|---|---|
| `executive-ai` | briefings from open tasks, research with citations, action-item capture as assigned tasks, follow-up tracking, reminders to colleagues | time, org settings, `search_knowledge`, `list_data_tables`, `query_data_table`, `list_tasks`, `create_task`, `update_task`, `notify_member` (**approval required**); memory: `recall_memories`, `save_user_memory`, `forget_user_memory`, `save_agent_memory` (**approval required**), `PERSISTENT` memory mode; integrations (M6): `list_calendar_events`, `create_calendar_event` (**approval**), `send_email` (**approval, always**) | calendar, email drafting/sending |
| `customer-ai` | answers from approved knowledge only, qualifies requests into follow-up tasks, escalates refunds/complaints/risk | time, org settings, `search_knowledge`, `list_data_tables`, `query_data_table`, `create_task`; CRM (M6): `upsert_crm_contact`, `add_crm_note` (**approval**; no CRM search for a customer-facing agent) | WhatsApp/email/web-chat channels, ticketing/CRM |

Both run on the shared runtime: permission ceiling, risk policy, approvals,
escalation (with operator alerts), run traces and audit. They can also run in the
background.

## Reference agents (roadmap)

| Agent | Capabilities | Tools it needs (to be built as real adapters) |
|---|---|---|
| Executive AI | briefing, research, task management, meeting prep, follow-up | tasks, calendar, knowledge search, email draft (HIGH to send) |
| Sales AI | lead qualification, follow-up, CRM, scheduling | CRM read/update (MEDIUM), calendar, email/WhatsApp send (HIGH) |
| Customer AI | enquiries, support, qualification, escalation | knowledge search, ticketing, messaging reply (HIGH by default) |
| Marketing AI | research, campaign planning, content strategy, analysis | knowledge/web research, analytics read, content drafts |
| Operations AI | workflow coordination, task monitoring, reporting, SOP execution | tasks, workflows, reporting, knowledge (SOPs) |
| Research AI | research, document analysis, intelligence briefs | knowledge search, document analysis, web research |

An agent ships only when its tools genuinely work. An agent whose tools are
not yet built would be a chatbot, and does not count as implemented.

## Acceptance scenarios (deterministic tests)

Implemented in `backend/tests/agents/test_workforce.py` and `test_controlled_autonomy.py`:

- Executive AI turns a meeting commitment into an assigned, dated, high-priority
  task with agent/run provenance, and the assignee is notified.
- An Executive AI briefing reads the real open tasks (done items excluded).
- A reminder to a colleague waits for approval, approvers are alerted, and the
  message arrives only after approval.
- Work tools cannot reach people outside the organization.
- Customer AI escalates a refund demand, and operators and the initiator are alerted.
- Customer AI logs a follow-up task.
- Given an unauthorized or high-risk action, the agent is denied or asked for
  approval (M1).

Still to write: "Given lead information, Sales AI qualifies the lead correctly"
(Sales AI needs CRM tools), and knowledge-grounded answers against pgvector for
Customer AI (the retrieval path is covered by the Phase 4 RAG runtime tests).
