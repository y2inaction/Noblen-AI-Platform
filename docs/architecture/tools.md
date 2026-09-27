# Tool System

**Status:** ✅ Implemented (Phase 3; risk and permission ceiling added in M1).
Code: `backend/app/agents/tools/`.

```
Agent ─► AgentTool binding ─► Tool (catalogue row) ─► ToolHandler (code) ─► system
```

Only handlers registered in code can run. A model's tool call is resolved to a
handler by name, or rejected. Handlers receive only a `ToolContext` (org, user,
agent, conversation, a safe org-settings snapshot, and a scoped knowledge-search
capability), never the database session, secrets or OS.

## Handler contract

| Attribute | Meaning |
|---|---|
| `name`, `handler_identifier`, `description`, `version` | identity (model-facing name) |
| `input_schema` / `output_schema` | JSON schemas; arguments are validated before execution |
| `default_permission_mode` | `AUTO` / `APPROVAL_REQUIRED` / `DISABLED` (bindings may override) |
| **`risk_level`** (M1) | `LOW` / `MEDIUM` / `HIGH`. **HIGH always requires human approval**, whatever the binding says |
| **`required_permission`** (M1) | RBAC permission the **initiating user** must currently hold |
| `execute(context, arguments)` | returns `ToolResult`. Unexpected exceptions are contained: the model sees only the error type |

`risk_level` and `required_permission` are declared **in code**, not in the
catalogue table, so no one can loosen them by editing tool rows. The tools API
exposes both (`GET /api/v1/tools`).

## Built-in tools

| Tool | Risk | Required permission | Default mode |
|---|---|---|---|
| `get_current_time` | LOW | — | AUTO |
| `get_organization_settings` | LOW | `org:view` | AUTO |
| `echo` | LOW | — | APPROVAL_REQUIRED (demonstrates the approval flow) |
| `search_knowledge` | LOW | `knowledge:search` | AUTO |

Integrations (email, calendar, CRM, messaging, payments) are not stubbed. Each
will arrive as a real handler with an honest risk level and permission.

## Runtime control tool

`escalate_to_human` is always offered to the model. It is not a catalogue tool.
Calling it ends the run `ESCALATED` with the given reason.

## MCP compatibility

Tool specs are `{name, description, input_schema}`, the same shape as an MCP tool
listing. A planned `McpToolHandler` adapter can wrap tools from a connected MCP
server as handlers (with Noblen-assigned risk level and permission) without
changing the runtime. *Not implemented yet.*
