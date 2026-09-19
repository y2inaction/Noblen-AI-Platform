"""Conversation API (tenant-scoped)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import conversations as service
from app.api.deps import TenantContext, require_permission
from app.db.session import get_db
from app.rbac.permissions import Permission
from app.schemas.conversation import (
    ConversationCreate,
    ConversationListOut,
    ConversationOut,
    MessageCreate,
    MessageOut,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: ConversationCreate,
    ctx: TenantContext = Depends(require_permission(Permission.CONVERSATION_CREATE)),
    db: AsyncSession = Depends(get_db),
) -> ConversationOut:
    conversation = await service.create_conversation(
        db, ctx.organization_id, ctx.user.id, agent_id=body.agent_id, title=body.title
    )
    await db.commit()
    await db.refresh(conversation)
    return ConversationOut.model_validate(conversation)


@router.get("", response_model=ConversationListOut)
async def list_conversations(
    ctx: TenantContext = Depends(require_permission(Permission.CONVERSATION_VIEW)),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ConversationListOut:
    items, total = await service.list_conversations(
        db, ctx.organization_id, limit=limit, offset=offset
    )
    return ConversationListOut(
        items=[ConversationOut.model_validate(c) for c in items], total=total
    )


@router.get("/{conversation_id}", response_model=ConversationOut)
async def get_conversation(
    conversation_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CONVERSATION_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> ConversationOut:
    conversation = await service.get_conversation(db, ctx.organization_id, conversation_id)
    return ConversationOut.model_validate(conversation)


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
async def list_messages(
    conversation_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CONVERSATION_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> list[MessageOut]:
    # Tenant + existence check, then scoped message fetch.
    await service.get_conversation(db, ctx.organization_id, conversation_id)
    messages = await service.get_messages(db, ctx.organization_id, conversation_id)
    return [MessageOut.model_validate(m) for m in messages]


@router.post("/{conversation_id}/messages", response_model=MessageOut, status_code=201)
async def add_message(
    conversation_id: uuid.UUID,
    body: MessageCreate,
    ctx: TenantContext = Depends(require_permission(Permission.CONVERSATION_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> MessageOut:
    await service.get_conversation(db, ctx.organization_id, conversation_id)
    message = await service.add_message(
        db,
        ctx.organization_id,
        conversation_id,
        role="user",
        content=body.content,
        created_by=ctx.user.id,
    )
    await db.commit()
    await db.refresh(message)
    return MessageOut.model_validate(message)
