"""Publication sinks (Milestone 9, ADR-0038 decision C).

A publication is a tool call that moves a run's output out of its person's
reach: into organization tasks, notifications or agent memory, or outside the
organization. The list is explicit. Read tools, `save_user_memory` and
`forget_user_memory` are not publications, and risk level plays no part.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools.base import ToolHandler
from app.integrations.service import MCP_HANDLER
from app.models.organization import Organization
from app.rbac.visibility import publication_attribution

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


async def is_restricted_publication(
    db: AsyncSession,
    organization_id: uuid.UUID,
    handler: ToolHandler,
    sources: Sequence[Mapping[str, Any]] | None,
    truncated: bool,
) -> bool:
    """Whether a call needs an approval as a restricted publication (M9): a sink,
    in an organization with `require_approval_to_publish_restricted` on, whose
    provenance is `restricted` in the ADR-0037 sense (a baseline member could not
    read every source now; unknown, empty or truncated provenance is restricted).
    Agent runs and workflow tool steps both decide through this function."""
    if not is_publication_sink(handler):
        return False
    org = await db.get(Organization, organization_id)
    if org is None or not org.require_approval_to_publish_restricted:
        return False
    attribution = await publication_attribution(db, organization_id, sources, truncated)
    return bool(attribution["restricted"])
