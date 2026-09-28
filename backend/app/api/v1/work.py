"""Tasks and the caller's in-app notifications (tenant-scoped)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, get_tenant_context, require_permission
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.models.enums import TaskStatus
from app.rbac.permissions import Permission
from app.schemas.work import (
    NotificationListOut,
    NotificationOut,
    TaskCreate,
    TaskListOut,
    TaskOut,
    TaskUpdate,
)
from app.services import work_service
from app.services.audit_service import record_audit

router = APIRouter(tags=["work"])


# ---- tasks ---------------------------------------------------------------- #
@router.get("/tasks", response_model=TaskListOut)
async def list_tasks(
    task_status: TaskStatus | None = Query(default=None, alias="status"),
    open_only: bool = False,
    assignee_id: uuid.UUID | None = None,
    mine: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.TASK_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> TaskListOut:
    items, total = await work_service.list_tasks(
        db,
        ctx.organization_id,
        status=task_status.value if task_status else None,
        open_only=open_only,
        assignee_id=ctx.user.id if mine else assignee_id,
        limit=limit,
        offset=offset,
    )
    return TaskListOut(items=[TaskOut.model_validate(t) for t in items], total=total)


@router.post("/tasks", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
async def create_task(
    body: TaskCreate,
    ctx: TenantContext = Depends(require_permission(Permission.TASK_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> TaskOut:
    task = await work_service.create_task(
        db,
        ctx.organization_id,
        title=body.title,
        description=body.description,
        priority=body.priority.value,
        due_at=body.due_at,
        assignee_id=body.assignee_id,
        created_by=ctx.user.id,
    )
    await record_audit(
        db,
        action="task.created",
        user_id=ctx.user.id,
        organization_id=ctx.organization_id,
        target_type="task",
        target_id=str(task.id),
    )
    await db.commit()
    await db.refresh(task)
    return TaskOut.model_validate(task)


@router.get("/tasks/{task_id}", response_model=TaskOut)
async def get_task(
    task_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TASK_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> TaskOut:
    return TaskOut.model_validate(await work_service.get_task(db, ctx.organization_id, task_id))


@router.patch("/tasks/{task_id}", response_model=TaskOut)
async def update_task(
    task_id: uuid.UUID,
    body: TaskUpdate,
    ctx: TenantContext = Depends(require_permission(Permission.TASK_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> TaskOut:
    changes = body.model_dump(exclude_unset=True)
    for key in ("status", "priority"):
        if changes.get(key) is not None:
            changes[key] = changes[key].value
    task = await work_service.update_task(
        db, ctx.organization_id, task_id, changes, actor_id=ctx.user.id
    )
    await record_audit(
        db,
        action="task.updated",
        user_id=ctx.user.id,
        organization_id=ctx.organization_id,
        target_type="task",
        target_id=str(task.id),
        metadata={"fields": sorted(changes)},
    )
    await db.commit()
    await db.refresh(task)
    return TaskOut.model_validate(task)


# ---- notifications (always the caller's own) ------------------------------ #
@router.get("/notifications", response_model=NotificationListOut)
async def list_notifications(
    unread_only: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> NotificationListOut:
    items, total, unread = await work_service.list_notifications(
        db, ctx.organization_id, ctx.user.id, unread_only=unread_only, limit=limit, offset=offset
    )
    return NotificationListOut(
        items=[NotificationOut.model_validate(n) for n in items], total=total, unread=unread
    )


@router.post("/notifications/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_notification_read(
    notification_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> None:
    updated = await work_service.mark_read(db, ctx.organization_id, ctx.user.id, notification_id)
    await db.commit()
    if updated == 0:
        # Unknown, already read, or someone else's: indistinguishable on purpose.
        raise NotFoundError("Unread notification not found.")


@router.post("/notifications/read-all")
async def mark_all_notifications_read(
    ctx: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    updated = await work_service.mark_read(db, ctx.organization_id, ctx.user.id)
    await db.commit()
    return {"updated": updated}
