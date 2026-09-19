"""Human-in-the-loop approval API."""

from __future__ import annotations

import dataclasses
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import approvals as service
from app.agents.runtime import AgentRuntime, get_agent_runtime
from app.api.deps import TenantContext, require_permission
from app.db.session import get_db
from app.rbac.permissions import Permission
from app.schemas.approval import ApprovalDecisionOut, ApprovalListOut, ApprovalOut

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_model=ApprovalListOut)
async def list_approvals(
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_APPROVE_ACTIONS)),
    db: AsyncSession = Depends(get_db),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ApprovalListOut:
    items, total = await service.list_approvals(
        db, ctx.organization_id, status=status_filter, limit=limit, offset=offset
    )
    return ApprovalListOut(items=[ApprovalOut.model_validate(a) for a in items], total=total)


@router.get("/{approval_id}", response_model=ApprovalOut)
async def get_approval(
    approval_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_APPROVE_ACTIONS)),
    db: AsyncSession = Depends(get_db),
) -> ApprovalOut:
    approval = await service.get_approval(db, ctx.organization_id, approval_id)
    return ApprovalOut.model_validate(approval)


async def _decide(
    db: AsyncSession,
    ctx: TenantContext,
    runtime: AgentRuntime,
    approval_id: uuid.UUID,
    approved: bool,
) -> ApprovalDecisionOut:
    approval = (
        await service.approve(db, ctx.organization_id, approval_id, ctx.user.id)
        if approved
        else await service.reject(db, ctx.organization_id, approval_id, ctx.user.id)
    )
    execution = None
    # Resume the run so the agent can react to the tool result / rejection.
    if approval.conversation_id is not None and approval.agent_id is not None:
        result = await runtime.resume_after_approval(
            db,
            organization_id=ctx.organization_id,
            user_id=ctx.user.id,
            approval=approval,
            approved=approved,
        )
        execution = dataclasses.asdict(result)
        execution["conversation_id"] = str(result.conversation_id)
        execution["agent_id"] = str(result.agent_id)
        execution["agent_version_id"] = str(result.agent_version_id)
        if result.approval_id:
            execution["approval_id"] = str(result.approval_id)
    await db.commit()
    await db.refresh(approval)
    return ApprovalDecisionOut(approval=ApprovalOut.model_validate(approval), execution=execution)


@router.post("/{approval_id}/approve", response_model=ApprovalDecisionOut)
async def approve_action(
    approval_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_APPROVE_ACTIONS)),
    runtime: AgentRuntime = Depends(get_agent_runtime),
    db: AsyncSession = Depends(get_db),
) -> ApprovalDecisionOut:
    return await _decide(db, ctx, runtime, approval_id, approved=True)


@router.post("/{approval_id}/reject", response_model=ApprovalDecisionOut)
async def reject_action(
    approval_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_APPROVE_ACTIONS)),
    runtime: AgentRuntime = Depends(get_agent_runtime),
    db: AsyncSession = Depends(get_db),
) -> ApprovalDecisionOut:
    return await _decide(db, ctx, runtime, approval_id, approved=False)
