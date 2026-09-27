"""Tasks and notifications (tenant-scoped business logic).

Used by the HTTP API and — through `AgentWorkspace` — by agent tools. Every
query is scoped to one organization; referenced users (assignees, recipients)
must be ACTIVE members of that organization.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.db.tenant import tenant_scoped
from app.models.enums import MembershipStatus, TaskPriority, TaskStatus
from app.models.membership import OrganizationMember
from app.models.user import User
from app.models.work import Notification, Task
from app.rbac.permissions import role_has_permission

_OPEN_STATUSES = (TaskStatus.OPEN.value, TaskStatus.IN_PROGRESS.value)


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- #
# Membership helpers
# --------------------------------------------------------------------------- #
async def require_member(
    db: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID
) -> OrganizationMember:
    member = (
        await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id == user_id,
                OrganizationMember.status == MembershipStatus.ACTIVE.value,
            )
        )
    ).scalar_one_or_none()
    if member is None:
        # Same answer whether the user doesn't exist or belongs elsewhere.
        raise ValidationError("That user is not an active member of this organization.")
    return member


async def find_member_by_email(
    db: AsyncSession, organization_id: uuid.UUID, email: str
) -> uuid.UUID:
    user_id = (
        await db.execute(
            select(User.id)
            .join(OrganizationMember, OrganizationMember.user_id == User.id)
            .where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.status == MembershipStatus.ACTIVE.value,
                User.email == email.lower().strip(),
            )
        )
    ).scalar_one_or_none()
    if user_id is None:
        raise ValidationError("That user is not an active member of this organization.")
    return user_id


async def members_with_permission(
    db: AsyncSession, organization_id: uuid.UUID, permission: str
) -> list[uuid.UUID]:
    rows = await db.execute(
        select(OrganizationMember.user_id, OrganizationMember.role_name).where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.status == MembershipStatus.ACTIVE.value,
        )
    )
    return [uid for uid, role in rows.tuples().all() if role_has_permission(role, permission)]


# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #
def _check_enum(value: str, enum_cls: Any, field: str) -> str:
    allowed = {e.value for e in enum_cls}
    if value not in allowed:
        raise ValidationError(f"Invalid {field} '{value}'. Allowed: {sorted(allowed)}.")
    return value


async def create_task(
    db: AsyncSession,
    organization_id: uuid.UUID,
    *,
    title: str,
    description: str | None = None,
    priority: str = TaskPriority.NORMAL.value,
    due_at: datetime | None = None,
    assignee_id: uuid.UUID | None = None,
    created_by: uuid.UUID | None = None,
    created_by_agent_id: uuid.UUID | None = None,
    source_run_id: uuid.UUID | None = None,
) -> Task:
    title = title.strip()
    if not title or len(title) > 255:
        raise ValidationError("Task title must be 1–255 characters.")
    _check_enum(priority, TaskPriority, "priority")
    if assignee_id is not None:
        await require_member(db, organization_id, assignee_id)
    task = Task(
        organization_id=organization_id,
        title=title,
        description=description,
        priority=priority,
        due_at=due_at,
        assignee_id=assignee_id,
        created_by=created_by,
        created_by_agent_id=created_by_agent_id,
        source_run_id=source_run_id,
    )
    db.add(task)
    await db.flush()
    if assignee_id is not None and assignee_id != created_by:
        await notify(
            db,
            organization_id,
            recipient_id=assignee_id,
            kind="task_assigned",
            title=f"New task: {title}",
            body=description,
            link={"type": "task", "id": str(task.id)},
            sent_by_agent_id=created_by_agent_id,
        )
    await _emit(db, organization_id, "task.created", task)
    return task


async def _emit(db: AsyncSession, organization_id: uuid.UUID, event: str, task: Task) -> None:
    """Start workflows listening to a task event (M5). Imported lazily: the
    workflow engine itself uses this module."""
    from app.workflows.service import emit_event

    await emit_event(
        db,
        organization_id,
        event,
        {
            "task_id": str(task.id),
            "title": task.title,
            "status": task.status,
            "priority": task.priority,
            "assignee_id": str(task.assignee_id) if task.assignee_id else None,
        },
    )


async def get_task(db: AsyncSession, organization_id: uuid.UUID, task_id: uuid.UUID) -> Task:
    task = (
        await db.execute(
            tenant_scoped(select(Task), Task, organization_id).where(Task.id == task_id)
        )
    ).scalar_one_or_none()
    if task is None:
        raise NotFoundError("Task not found.")
    return task


async def list_tasks(
    db: AsyncSession,
    organization_id: uuid.UUID,
    *,
    status: str | None = None,
    open_only: bool = False,
    assignee_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Task], int]:
    stmt = tenant_scoped(select(Task), Task, organization_id)
    count = tenant_scoped(select(func.count(Task.id)), Task, organization_id)
    filters = []
    if status is not None:
        filters.append(Task.status == _check_enum(status, TaskStatus, "status"))
    if open_only:
        filters.append(Task.status.in_(_OPEN_STATUSES))
    if assignee_id is not None:
        filters.append(Task.assignee_id == assignee_id)
    stmt, count = stmt.where(*filters), count.where(*filters)
    total = int((await db.execute(count)).scalar_one())
    rows = await db.execute(stmt.order_by(Task.created_at.desc()).limit(limit).offset(offset))
    return list(rows.scalars().all()), total


async def update_task(
    db: AsyncSession,
    organization_id: uuid.UUID,
    task_id: uuid.UUID,
    changes: dict[str, Any],
    *,
    actor_id: uuid.UUID | None = None,
    actor_agent_id: uuid.UUID | None = None,
) -> Task:
    task = await get_task(db, organization_id, task_id)
    if "title" in changes and changes["title"] is not None:
        title = str(changes["title"]).strip()
        if not title or len(title) > 255:
            raise ValidationError("Task title must be 1–255 characters.")
        task.title = title
    if "description" in changes:
        task.description = changes["description"]
    if changes.get("priority") is not None:
        task.priority = _check_enum(changes["priority"], TaskPriority, "priority")
    if "due_at" in changes:
        task.due_at = changes["due_at"]
    if "assignee_id" in changes and changes["assignee_id"] != task.assignee_id:
        new_assignee = changes["assignee_id"]
        if new_assignee is not None:
            await require_member(db, organization_id, new_assignee)
            if new_assignee != actor_id:
                await notify(
                    db,
                    organization_id,
                    recipient_id=new_assignee,
                    kind="task_assigned",
                    title=f"Task assigned to you: {task.title}",
                    link={"type": "task", "id": str(task.id)},
                    sent_by_agent_id=actor_agent_id,
                )
        task.assignee_id = new_assignee
    completed = False
    if changes.get("status") is not None:
        previous = task.status
        task.status = _check_enum(changes["status"], TaskStatus, "status")
        task.completed_at = _now() if task.status == TaskStatus.DONE.value else None
        completed = task.status == TaskStatus.DONE.value and previous != TaskStatus.DONE.value
    await db.flush()
    if completed:
        await _emit(db, organization_id, "task.completed", task)
    return task


# --------------------------------------------------------------------------- #
# Notifications
# --------------------------------------------------------------------------- #
async def notify(
    db: AsyncSession,
    organization_id: uuid.UUID,
    *,
    recipient_id: uuid.UUID,
    kind: str,
    title: str,
    body: str | None = None,
    link: dict[str, Any] | None = None,
    sent_by_agent_id: uuid.UUID | None = None,
) -> Notification:
    notification = Notification(
        organization_id=organization_id,
        recipient_id=recipient_id,
        kind=kind,
        title=title[:255],
        body=body,
        link=link,
        sent_by_agent_id=sent_by_agent_id,
    )
    db.add(notification)
    await db.flush()
    return notification


async def notify_permission_holders(
    db: AsyncSession,
    organization_id: uuid.UUID,
    permission: str,
    *,
    kind: str,
    title: str,
    body: str | None = None,
    link: dict[str, Any] | None = None,
    also: uuid.UUID | None = None,
) -> int:
    """Notify every active member holding `permission` (plus `also`, once)."""
    recipients = set(await members_with_permission(db, organization_id, permission))
    if also is not None:
        recipients.add(also)
    for recipient in recipients:
        await notify(
            db,
            organization_id,
            recipient_id=recipient,
            kind=kind,
            title=title,
            body=body,
            link=link,
        )
    return len(recipients)


async def list_notifications(
    db: AsyncSession,
    organization_id: uuid.UUID,
    recipient_id: uuid.UUID,
    *,
    unread_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Notification], int, int]:
    base = tenant_scoped(select(Notification), Notification, organization_id).where(
        Notification.recipient_id == recipient_id
    )
    unread_filter = Notification.read_at.is_(None)
    unread = int(
        (
            await db.execute(
                tenant_scoped(
                    select(func.count(Notification.id)), Notification, organization_id
                ).where(Notification.recipient_id == recipient_id, unread_filter)
            )
        ).scalar_one()
    )
    stmt = base.where(unread_filter) if unread_only else base
    total = (
        unread
        if unread_only
        else int(
            (
                await db.execute(
                    tenant_scoped(
                        select(func.count(Notification.id)), Notification, organization_id
                    ).where(Notification.recipient_id == recipient_id)
                )
            ).scalar_one()
        )
    )
    rows = await db.execute(
        stmt.order_by(Notification.created_at.desc()).limit(limit).offset(offset)
    )
    return list(rows.scalars().all()), total, unread


async def mark_read(
    db: AsyncSession,
    organization_id: uuid.UUID,
    recipient_id: uuid.UUID,
    notification_id: uuid.UUID | None = None,
) -> int:
    """Mark one (or all) of the recipient's notifications read. Returns count."""
    stmt = (
        update(Notification)
        .where(
            Notification.organization_id == organization_id,
            Notification.recipient_id == recipient_id,
            Notification.read_at.is_(None),
        )
        .values(read_at=_now())
    )
    if notification_id is not None:
        stmt = stmt.where(Notification.id == notification_id)
    result = await db.execute(stmt)
    await db.flush()
    return int(getattr(result, "rowcount", 0) or 0)


# --------------------------------------------------------------------------- #
# Agent-facing capability
# --------------------------------------------------------------------------- #
@dataclass
class AgentWorkspace:
    """Tenant-, agent- and run-bound access to work items for tool handlers.

    Tools never receive a DB session or choose an organization: the runtime
    builds this object from the run, so every call is scoped and attributed.
    """

    db: AsyncSession
    organization_id: uuid.UUID
    agent_id: uuid.UUID | None  # None for workflow tool steps (M5)
    run_id: uuid.UUID | None
    user_id: uuid.UUID | None

    async def create_task(self, **fields: Any) -> Task:
        return await create_task(
            self.db,
            self.organization_id,
            created_by=self.user_id,
            created_by_agent_id=self.agent_id,
            source_run_id=self.run_id,
            **fields,
        )

    async def list_tasks(self, **filters: Any) -> tuple[list[Task], int]:
        return await list_tasks(self.db, self.organization_id, **filters)

    async def update_task(self, task_id: uuid.UUID, changes: dict[str, Any]) -> Task:
        return await update_task(
            self.db,
            self.organization_id,
            task_id,
            changes,
            actor_id=self.user_id,
            actor_agent_id=self.agent_id,
        )

    async def member_id(self, email: str) -> uuid.UUID:
        return await find_member_by_email(self.db, self.organization_id, email)

    async def notify_member(
        self, recipient_id: uuid.UUID, title: str, body: str | None
    ) -> Notification:
        await require_member(self.db, self.organization_id, recipient_id)
        return await notify(
            self.db,
            self.organization_id,
            recipient_id=recipient_id,
            kind="agent_message",
            title=title,
            body=body,
            link={"type": "agent_run", "id": str(self.run_id)} if self.run_id else None,
            sent_by_agent_id=self.agent_id,
        )
