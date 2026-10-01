"""Publication sinks (Milestone 9, ADR-0038 decision C).

A publication is a tool call that moves a run's output out of its person's
reach: into organization tasks, notifications or agent memory, or outside the
organization. The list is explicit. Read tools, `save_user_memory` and
`forget_user_memory` are not publications, and risk level plays no part.
"""

from __future__ import annotations

from app.agents.tools.base import ToolHandler
from app.integrations.service import MCP_HANDLER

PUBLICATION_SINKS: frozenset[str] = frozenset(
    {
        "create_task",
        "update_task",
        "notify_member",
        "save_agent_memory",
        "send_email",
        "call_webhook",
        "create_calendar_event",
        "upsert_crm_contact",
        "add_crm_note",
    }
)


def is_publication_sink(handler: ToolHandler) -> bool:
    """Classified by the handler that runs, never by a tool's display name.
    Imported MCP tools are sinks: they run only once an administrator enables them."""
    identifier = handler.handler_identifier
    return identifier in PUBLICATION_SINKS or identifier == MCP_HANDLER
