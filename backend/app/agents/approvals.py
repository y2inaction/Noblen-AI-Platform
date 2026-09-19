"""Human-in-the-loop approval service (tenant-scoped state machine)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.errors import ApprovalExpired, ApprovalInvalidState, ApprovalNotFound
from app.core.config import settings
from app.db.tenant import tenant_scoped
from app.models.approval import Approval
from app.models.enums import ApprovalStatus


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


async def create_approval(
    db: AsyncSession,
    organization_id: uuid.UUID,
    *,
    agent_id: uuid.UUID | None,
    conversation_id: uuid.UUID | None,
    tool_call_id: str,
    tool_name: str,
    request_payload: dict[str, Any],
    requested_by: uuid.UUID | None,
    reason: str | None = None,
) -> Approval:
    approval = Approval(
        organization_id=organization_id,
        agent_id=agent_id,
        conversation_id=conversation_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        request_payload=request_payload,
        requested_by=requested_by,
        reason=reason,
        status=ApprovalStatus.PENDING.value,
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.AGENT_APPROVAL_TTL_SECONDS),
    )
    db.add(approval)
    await db.flush()
    return approval


async def get_approval(
    db: AsyncSession, organization_id: uuid.UUID, approval_id: uuid.UUID
) -> Approval:
    approval = (
        await db.execute(
            tenant_scoped(select(Approval), Approval, organization_id).where(
                Approval.id == approval_id
            )
        )
    ).scalar_one_or_none()
    if approval is None:
        raise ApprovalNotFound("Approval not found.")
    return approval


async def list_approvals(
    db: AsyncSession,
    organization_id: uuid.UUID,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Approval], int]:
    stmt = tenant_scoped(select(Approval), Approval, organization_id)
    count_stmt = tenant_scoped(select(func.count(Approval.id)), Approval, organization_id)
    if status:
        stmt = stmt.where(Approval.status == status)
        count_stmt = count_stmt.where(Approval.status == status)
    total = (await db.execute(count_stmt)).scalar_one()
    rows = (
        (await db.execute(stmt.order_by(Approval.created_at.desc()).limit(limit).offset(offset)))
        .scalars()
        .all()
    )
    return list(rows), int(total)


def _ensure_actionable(approval: Approval) -> None:
    if approval.status != ApprovalStatus.PENDING.value:
        raise ApprovalInvalidState(
            f"Approval is already '{approval.status}' and cannot be changed."
        )


async def _expire_if_needed(db: AsyncSession, approval: Approval) -> None:
    expires_at = _aware(approval.expires_at)
    if expires_at is not None and expires_at < datetime.now(UTC):
        approval.status = ApprovalStatus.EXPIRED.value
        await db.flush()
        raise ApprovalExpired("This approval has expired.")


async def approve(
    db: AsyncSession, organization_id: uuid.UUID, approval_id: uuid.UUID, approver_id: uuid.UUID
) -> Approval:
    approval = await get_approval(db, organization_id, approval_id)
    _ensure_actionable(approval)
    await _expire_if_needed(db, approval)
    approval.status = ApprovalStatus.APPROVED.value
    approval.approved_by = approver_id
    approval.approved_at = datetime.now(UTC)
    await db.flush()
    return approval


async def reject(
    db: AsyncSession, organization_id: uuid.UUID, approval_id: uuid.UUID, rejecter_id: uuid.UUID
) -> Approval:
    approval = await get_approval(db, organization_id, approval_id)
    _ensure_actionable(approval)
    await _expire_if_needed(db, approval)
    approval.status = ApprovalStatus.REJECTED.value
    approval.rejected_by = rejecter_id
    approval.rejected_at = datetime.now(UTC)
    await db.flush()
    return approval
