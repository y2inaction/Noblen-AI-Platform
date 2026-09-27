# AI Workforce

**Status:** the framework is implemented (Phase 3 engine + Noblen AI 3.0 M1
controlled autonomy). The reference agents are **not yet built** (Milestone 2).

All workforce agents are **configurations of one agent framework**, not separate
codebases. Each one is an `Agent` with an immutable version (instructions, model,
fallbacks, memory), tool bindings with per-tool policies, and attached knowledge
bases. They all run on the shared runtime with approvals, escalation, run traces
and audit.

## Reference agents (planned)

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

## Acceptance scenarios (to be written as deterministic tests)

- Given lead information, Sales AI qualifies the lead correctly.
- Given an unauthorized action, the agent refuses or escalates. *(Framework behaviour already tested.)*
- Given a knowledge source, the agent retrieves the correct information.
- Given a high-risk action, the agent requests approval. *(Framework behaviour already tested.)*
