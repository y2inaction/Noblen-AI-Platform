"""Long-term memory API (Noblen AI 3.0, M4): the human write paths.

- Everyone with `memory:write` manages their own USER memories.
- `memory:manage` (managers) writes AGENT and ORGANIZATION memories.
- Listing shows the caller's own USER memories plus AGENT and ORGANIZATION
  memories. Other people's USER memories are never visible, to anyone.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    TenantContext,
    get_tenant_context,
    require_permission,
    user_is_platform_superuser,
)
from app.core.exceptions import PermissionDeniedError
from app.db.session import get_db
from app.models.enums import MemoryScope
from app.models.memory import Memory
from app.rbac.permissions import Permission, role_has_permission
from app.schemas.memory import (
    MemoryCreate,
    MemoryForgetOut,
    MemoryListOut,
    MemoryOut,
    MemoryUpdate,
)
from app.services import memory_service
from app.services.audit_service import record_audit

router = APIRouter(prefix="/memories", tags=["memory"])


def _require_write(ctx: TenantContext, scope: str, owner_id: uuid.UUID | None) -> None:
    """Own USER memories need memory:write; everything else needs memory:manage."""
    if scope == MemoryScope.USER.value and owner_id == ctx.user.id:
        needed = Permission.MEMORY_WRITE
    else:
        needed = Permission.MEMORY_MANAGE
    if user_is_platform_superuser(ctx.user) or role_has_permission(ctx.role_name, needed):
        return
    raise PermissionDeniedError(
        f"Your role '{ctx.role_name}' lacks the required permission '{needed}'."
    )


async def _audit(db: AsyncSession, ctx: TenantContext, action: str, memory: Memory) -> None:
    # Scope and subject only: memory content is never written to the audit log.
    await record_audit(
        db,
        action=action,
        user_id=ctx.user.id,
        organization_id=ctx.organization_id,
        target_type="memory",
        target_id=str(memory.id),
        metadata={
            "scope": memory.scope,
            "agent_id": str(memory.agent_id) if memory.agent_id else None,
        },
    )


@router.get("", response_model=MemoryListOut)
async def list_memories(
    scope: MemoryScope | None = None,
    agent_id: uuid.UUID | None = None,
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.MEMORY_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> MemoryListOut:
    items, total = await memory_service.list_for_member(
        db,
        ctx.organization_id,
        ctx.user.id,
        scope=scope.value if scope else None,
        agent_id=agent_id,
        text=q,
        limit=limit,
        offset=offset,
    )
    return MemoryListOut(items=[MemoryOut.model_validate(m) for m in items], total=total)


@router.post("", response_model=MemoryOut, status_code=status.HTTP_201_CREATED)
async def create_memory(
    body: MemoryCreate,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> MemoryOut:
    scope = body.scope.value
    owner = ctx.user.id if scope == MemoryScope.USER.value else None
    _require_write(ctx, scope, owner)
    memory, created = await memory_service.create_memory(
        db,
        ctx.organization_id,
        scope=scope,
        content=body.content,
        category=body.category,
        user_id=owner,
        agent_id=body.agent_id,
        created_by=ctx.user.id,
    )
    if created:
        await _audit(db, ctx, "memory.created", memory)
    await db.commit()
    await db.refresh(memory)
    return MemoryOut.model_validate(memory)


# Declared before /{memory_id} so "mine" is never parsed as an id.
@router.delete("/mine", response_model=MemoryForgetOut)
async def forget_me(
    ctx: TenantContext = Depends(require_permission(Permission.MEMORY_WRITE)),
    db: AsyncSession = Depends(get_db),
) -> MemoryForgetOut:
    """Delete every USER memory about the caller in this organization."""
    deleted = await memory_service.delete_user_memories(db, ctx.organization_id, ctx.user.id)
    await record_audit(
        db,
        action="memory.user_forgotten",
        user_id=ctx.user.id,
        organization_id=ctx.organization_id,
        target_type="user",
        target_id=str(ctx.user.id),
        metadata={"deleted": deleted},
    )
    await db.commit()
    return MemoryForgetOut(deleted=deleted)


@router.get("/{memory_id}", response_model=MemoryOut)
async def get_memory(
    memory_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.MEMORY_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> MemoryOut:
    memory = await memory_service.get_for_member(db, ctx.organization_id, ctx.user.id, memory_id)
    return MemoryOut.model_validate(memory)


@router.patch("/{memory_id}", response_model=MemoryOut)
async def update_memory(
    memory_id: uuid.UUID,
    body: MemoryUpdate,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> MemoryOut:
    memory = await memory_service.get_for_member(db, ctx.organization_id, ctx.user.id, memory_id)
    _require_write(ctx, memory.scope, memory.user_id)
    await memory_service.update_memory(db, memory, content=body.content, category=body.category)
    await _audit(db, ctx, "memory.updated", memory)
    await db.commit()
    await db.refresh(memory)
    return MemoryOut.model_validate(memory)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> None:
    memory = await memory_service.get_for_member(db, ctx.organization_id, ctx.user.id, memory_id)
    _require_write(ctx, memory.scope, memory.user_id)
    await _audit(db, ctx, "memory.deleted", memory)
    await memory_service.delete_memory(db, memory)
    await db.commit()
