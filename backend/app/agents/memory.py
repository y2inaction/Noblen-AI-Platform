"""Scoped conversation memory for the runtime.

Phase 3 supports NONE / CONVERSATION / PERSISTENT. No vector/semantic memory yet —
PERSISTENT currently behaves like a bounded CONVERSATION window and is the documented
extension point for the Phase 4 Knowledge/RAG layer. Memory is always bounded.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import conversations as conversation_service
from app.ai.types import Message, ToolCall
from app.core.config import settings
from app.models.enums import MemoryMode


def _row_to_message(row: Any) -> Message:
    meta = row.message_metadata or {}
    tool_calls = None
    if meta.get("tool_calls"):
        tool_calls = [ToolCall(**tc) for tc in meta["tool_calls"]]
    return Message(
        role=row.role,
        content=row.content or "",
        tool_calls=tool_calls,
        tool_call_id=meta.get("tool_call_id"),
        name=meta.get("name"),
    )


async def load_messages(
    db: AsyncSession,
    organization_id: uuid.UUID,
    conversation_id: uuid.UUID,
    *,
    mode: str,
    memory_configuration: dict[str, Any] | None = None,
) -> list[Message]:
    """Return the message context for a run, per the agent version's memory mode."""
    rows = await conversation_service.get_messages(db, organization_id, conversation_id)

    if mode == MemoryMode.NONE.value:
        # Only the most recent user message — no prior context.
        for row in reversed(rows):
            if row.role == "user":
                return [_row_to_message(row)]
        return []

    # CONVERSATION and PERSISTENT: a bounded recent window.
    cfg = memory_configuration or {}
    max_messages = cfg.get("max_messages", 20)
    if not isinstance(max_messages, int) or max_messages < 1:
        max_messages = 20
    max_messages = min(max_messages, settings.AGENT_MEMORY_MAX_MESSAGES)
    windowed = rows[-max_messages:]
    return [_row_to_message(r) for r in windowed]
