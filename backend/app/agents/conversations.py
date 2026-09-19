"""Conversation service (tenant-scoped)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.errors import ConversationNotFound
from app.db.tenant import tenant_scoped
from app.models.conversation import Conversation, ConversationMessage, ConversationParticipant


async def create_conversation(
    db: AsyncSession,
    organization_id: uuid.UUID,
    created_by: uuid.UUID,
    *,
    agent_id: uuid.UUID | None = None,
    title: str | None = None,
) -> Conversation:
    conversation = Conversation(
        organization_id=organization_id,
        agent_id=agent_id,
        title=title,
        created_by=created_by,
    )
    db.add(conversation)
    await db.flush()
    db.add(
        ConversationParticipant(
            organization_id=organization_id,
            conversation_id=conversation.id,
            user_id=created_by,
            role="owner",
        )
    )
    await db.flush()
    return conversation


async def get_conversation(
    db: AsyncSession, organization_id: uuid.UUID, conversation_id: uuid.UUID
) -> Conversation:
    conversation = (
        await db.execute(
            tenant_scoped(select(Conversation), Conversation, organization_id).where(
                Conversation.id == conversation_id
            )
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise ConversationNotFound("Conversation not found.")
    return conversation


async def list_conversations(
    db: AsyncSession, organization_id: uuid.UUID, *, limit: int = 50, offset: int = 0
) -> tuple[list[Conversation], int]:
    total = (
        await db.execute(
            tenant_scoped(select(func.count(Conversation.id)), Conversation, organization_id)
        )
    ).scalar_one()
    rows = (
        (
            await db.execute(
                tenant_scoped(select(Conversation), Conversation, organization_id)
                .order_by(Conversation.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), int(total)


async def add_message(
    db: AsyncSession,
    organization_id: uuid.UUID,
    conversation_id: uuid.UUID,
    *,
    role: str,
    content: str = "",
    metadata: dict[str, Any] | None = None,
    agent_version_id: uuid.UUID | None = None,
    created_by: uuid.UUID | None = None,
) -> ConversationMessage:
    next_seq = (
        await db.execute(
            select(func.coalesce(func.max(ConversationMessage.sequence), -1) + 1).where(
                ConversationMessage.conversation_id == conversation_id
            )
        )
    ).scalar_one()
    message = ConversationMessage(
        organization_id=organization_id,
        conversation_id=conversation_id,
        sequence=int(next_seq),
        role=role,
        content=content,
        message_metadata=metadata or {},
        agent_version_id=agent_version_id,
        created_by=created_by,
    )
    db.add(message)
    await db.flush()
    return message


async def get_messages(
    db: AsyncSession,
    organization_id: uuid.UUID,
    conversation_id: uuid.UUID,
    *,
    limit: int | None = None,
) -> list[ConversationMessage]:
    stmt = (
        tenant_scoped(select(ConversationMessage), ConversationMessage, organization_id)
        .where(ConversationMessage.conversation_id == conversation_id)
        .order_by(ConversationMessage.sequence.asc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return list(rows)
