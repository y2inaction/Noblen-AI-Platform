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


def sanitize_tool_pairs(messages: list[Message]) -> list[Message]:
    """Drop tool-call/tool-result pairs that are incomplete.

    A bounded window (or a run that ended while awaiting approval) can leave an
    assistant tool call without its result, or a tool result whose call was cut
    off. Providers reject such histories (e.g. Anthropic requires every
    tool_use to be answered), so both halves are removed.
    """
    call_ids = {tc.id for m in messages if m.role == "assistant" for tc in (m.tool_calls or [])}
    result_ids = {m.tool_call_id for m in messages if m.role == "tool" and m.tool_call_id}
    cleaned: list[Message] = []
    for m in messages:
        if m.role == "tool" and m.tool_call_id not in call_ids:
            continue
        if (
            m.role == "assistant"
            and m.tool_calls
            and not all(tc.id in result_ids for tc in m.tool_calls)
        ):
            if not m.content:
                continue
            m = Message(role="assistant", content=m.content)
        cleaned.append(m)
    # A context must not open with anything but a user turn.
    while cleaned and cleaned[0].role != "user":
        cleaned.pop(0)
    return cleaned


async def load_run_context(
    db: AsyncSession,
    organization_id: uuid.UUID,
    conversation_id: uuid.UUID,
    *,
    run_start_sequence: int,
    mode: str,
    memory_configuration: dict[str, Any] | None = None,
) -> list[Message]:
    """Context for one agent run: prior history per the memory mode, plus every
    message of the run itself (from its user message onward).

    Unlike `load_messages`, the run's own tool calls and results are never
    windowed out, so a run resumed after an approval sees exactly its own work,
    in every memory mode.
    """
    rows = await conversation_service.get_messages(db, organization_id, conversation_id)
    prior = [r for r in rows if r.sequence < run_start_sequence]
    current = [r for r in rows if r.sequence >= run_start_sequence]

    prior_messages: list[Message] = []
    if mode != MemoryMode.NONE.value:
        cfg = memory_configuration or {}
        max_messages = cfg.get("max_messages", 20)
        if not isinstance(max_messages, int) or max_messages < 1:
            max_messages = 20
        max_messages = min(max_messages, settings.AGENT_MEMORY_MAX_MESSAGES)
        prior_messages = sanitize_tool_pairs([_row_to_message(r) for r in prior[-max_messages:]])

    current_messages = [_row_to_message(r) for r in current]
    return prior_messages + current_messages
