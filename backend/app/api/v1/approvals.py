"""Human-in-the-loop approval API."""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import approvals as service
from app.agents.runtime import AgentRuntime, get_agent_runtime
from app.agents.tools.registry import validate_arguments
from app.ai.errors import AIError
from app.api.deps import TenantContext, require_permission
from app.api.v1.ai import translate_ai_error
from app.core.exceptions import PermissionDeniedError, ValidationError
from app.db.session import get_db
from app.models.approval import Approval
from app.models.organization import Organization
from app.models.run import AgentRun
from app.models.tool import Tool
from app.rbac.permissions import Permission
from app.rbac.visibility import (
    approval_provenance,
    eligible_for_restricted,
    present_approvals,
    present_execution,
    publication_attribution,
)
from app.schemas.approval import (
    ApprovalDecisionIn,
    ApprovalDecisionOut,
    ApprovalListOut,
    ApprovalModifyIn,
    ApprovalOut,
)
from app.services.audit_service import record_audit

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
    return ApprovalListOut(items=await present_approvals(db, items, ctx.viewer), total=total)


@router.get("/{approval_id}", response_model=ApprovalOut)
async def get_approval(
    approval_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_APPROVE_ACTIONS)),
    db: AsyncSession = Depends(get_db),
) -> ApprovalOut:
    approval = await service.get_approval(db, ctx.organization_id, approval_id)
    [out] = await present_approvals(db, [approval], ctx.viewer)
    return out


async def _enforce_eligibility(
    db: AsyncSession, ctx: TenantContext, approval_id: uuid.UUID
) -> None:
    """A restricted publication is decided only by an approver who can read every
    source it derives from, re-checked now (M9.6, ADR-0038 decisions A and E)."""
    approval = await service.get_approval(db, ctx.organization_id, approval_id)
    if not approval.restricted_publication:
        return
    [(sources, truncated)] = (await approval_provenance(db, [approval])).values()
    if not await eligible_for_restricted(db, ctx.viewer, ctx.organization_id, sources, truncated):
        # Audited, and kept although the request fails (M9.7): the request, the
        # approver and the reason, never which source.
        await record_audit(
            db,
            action="agent.approval_decision_refused",
            user_id=ctx.user.id,
            organization_id=ctx.organization_id,
            target_type="approval",
            target_id=str(approval.id),
            metadata={
                "reason": "not_eligible",
                "tool": approval.tool_name,
                "run_id": str(approval.run_id) if approval.run_id else None,
                "restricted_publication": True,
            },
        )
        await db.commit()
        raise PermissionDeniedError(
            "You can't decide this request: it publishes content derived from sources "
            "you can't currently read.",
            error_code="not_eligible",
        )


async def _restricted_now(db: AsyncSession, approval: Approval) -> bool:
    """ADR-0037 `restricted` for the paused run's provenance, evaluated now. A
    request without its run has unknown provenance, which is restricted."""
    run = await db.get(AgentRun, approval.run_id) if approval.run_id else None
    if run is None or run.organization_id != approval.organization_id:
        return True
    attribution = await publication_attribution(
        db, approval.organization_id, run.sources, run.sources_truncated
    )
    return bool(attribution["restricted"])


async def _enforce_separation_of_duties(
    db: AsyncSession, ctx: TenantContext, approval_id: uuid.UUID
) -> None:
    """With `require_independent_approval` on, the person who started the run
    can never decide on its actions — not even a platform superuser."""
    org = await db.get(Organization, ctx.organization_id)
    if org is None or not org.require_independent_approval:
        return
    approval = await service.get_approval(db, ctx.organization_id, approval_id)
    if approval.requested_by is not None and approval.requested_by == ctx.user.id:
        raise PermissionDeniedError(
            "This organization requires independent approval: you started this task, "
            "so another reviewer must decide.",
            error_code="independent_approval_required",
        )


async def _decide(
    db: AsyncSession,
    ctx: TenantContext,
    runtime: AgentRuntime,
    approval_id: uuid.UUID,
    approved: bool,
    *,
    note: str | None = None,
    modified_payload: dict[str, Any] | None = None,
) -> ApprovalDecisionOut:
    await _enforce_separation_of_duties(db, ctx, approval_id)
    await _enforce_eligibility(db, ctx, approval_id)
    approval = (
        await service.approve(
            db,
            ctx.organization_id,
            approval_id,
            ctx.user.id,
            note=note,
            modified_payload=modified_payload,
        )
        if approved
        else await service.reject(db, ctx.organization_id, approval_id, ctx.user.id, note=note)
    )
    await record_audit(
        db,
        action="agent.approval_decided",
        user_id=ctx.user.id,
        organization_id=ctx.organization_id,
        target_type="approval",
        target_id=str(approval.id),
        metadata={
            "decision": approval.status,
            "modified": modified_payload is not None,
            "tool": approval.tool_name,
            # M9.7: whether this gated a restricted publication, and whether its
            # provenance is restricted now (the decision-time evaluation).
            "restricted_publication": approval.restricted_publication,
            "restricted": await _restricted_now(db, approval),
        },
    )
    execution = None
    # Resume the run so the agent can react to the tool result / rejection.
    if approval.conversation_id is not None and approval.agent_id is not None:
        try:
            result = await runtime.resume_after_approval(
                db,
                organization_id=ctx.organization_id,
                user_id=ctx.user.id,
                approval=approval,
                approved=approved,
            )
        except AIError as err:
            raise translate_ai_error(err) from err
        execution = dataclasses.asdict(result)
        for key in ("conversation_id", "agent_id", "agent_version_id", "approval_id", "run_id"):
            if execution.get(key) is not None:
                execution[key] = str(execution[key])
        # The resumed run acts for its initiator; the approver sees its status, and
        # the agent's answer only under the run rule (ADR-0035, M8).
        execution = await present_execution(
            db, execution, ctx.organization_id, approval.requested_by, ctx.viewer
        )
    await db.commit()
    await db.refresh(approval)
    [out] = await present_approvals(db, [approval], ctx.viewer)
    return ApprovalDecisionOut(approval=out, execution=execution)


@router.post("/{approval_id}/approve", response_model=ApprovalDecisionOut)
async def approve_action(
    approval_id: uuid.UUID,
    body: ApprovalDecisionIn | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_APPROVE_ACTIONS)),
    runtime: AgentRuntime = Depends(get_agent_runtime),
    db: AsyncSession = Depends(get_db),
) -> ApprovalDecisionOut:
    return await _decide(
        db, ctx, runtime, approval_id, approved=True, note=body.note if body else None
    )


@router.post("/{approval_id}/modify", response_model=ApprovalDecisionOut)
async def approve_with_modifications(
    approval_id: uuid.UUID,
    body: ApprovalModifyIn,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_APPROVE_ACTIONS)),
    runtime: AgentRuntime = Depends(get_agent_runtime),
    db: AsyncSession = Depends(get_db),
) -> ApprovalDecisionOut:
    """Approve the action with reviewer-edited arguments."""
    await _enforce_separation_of_duties(db, ctx, approval_id)
    await _enforce_eligibility(db, ctx, approval_id)
    approval = await service.get_approval(db, ctx.organization_id, approval_id)
    tool = (
        await db.execute(select(Tool).where(Tool.name == approval.tool_name))
    ).scalar_one_or_none()
    if tool is None:
        raise ValidationError(f"Tool '{approval.tool_name}' is no longer available.")
    error = validate_arguments(tool.input_schema or {}, body.arguments)
    if error is not None:
        raise ValidationError(f"Invalid arguments: {error}")
    return await _decide(
        db,
        ctx,
        runtime,
        approval_id,
        approved=True,
        note=body.note,
        modified_payload=body.arguments,
    )


@router.post("/{approval_id}/reject", response_model=ApprovalDecisionOut)
async def reject_action(
    approval_id: uuid.UUID,
    body: ApprovalDecisionIn | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_APPROVE_ACTIONS)),
    runtime: AgentRuntime = Depends(get_agent_runtime),
    db: AsyncSession = Depends(get_db),
) -> ApprovalDecisionOut:
    return await _decide(
        db, ctx, runtime, approval_id, approved=False, note=body.note if body else None
    )
