# Agents (design — implemented from Phase 3)

The agent engine is a reusable framework; every commercial agent (Customer
Service, Sales, Content, Executive Assistant, Operations) is a configuration on
top of it, not a separate app.

## Agent model (planned)
`name`, `description`, `system instructions`, `provider`, `model`, `temperature`,
`tools`, `knowledge sources`, `memory configuration`, `permissions`, `status`,
`version`, `metadata`.

## Lifecycle
`DRAFT → TESTING → ACTIVE → PAUSED → ARCHIVED`. Agents are **versioned**; an active
production configuration is never destroyed without preserving its history.

## Tool registry
Each tool declares: `name`, `description`, JSON `schema`, a **permission
requirement**, an execution handler, and audit logging. Agents never get raw
database access — only registered, permission-gated tools. Initial categories:
communication, CRM, calendar, knowledge, content, tasks, system.

## Memory (multi-level)
- Conversation memory (current context)
- Agent memory (persistent, per-agent)
- Organization knowledge (company-level)
- User preferences
Memory is explicit and categorised — not every message is saved.

## Safety
Tools honour the execution policy (`AUTO | APPROVAL_REQUIRED | DISABLED`) and
organization/tool permissions. Outward-facing actions require approval by default.

## Commercial agents (Phase 6)
Customer Service, Sales, Content, Executive Assistant, and Operations agents, each
assembled from the shared engine + tools + knowledge + workflows.
