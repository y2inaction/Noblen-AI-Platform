"""Workflow API (Noblen AI 3.0, M5): definitions, lifecycle, runs, decisions."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    TenantContext,
    require_permission,
    user_is_platform_superuser,
)
from app.core.exceptions import PermissionDeniedError
from app.db.session import get_db
from app.models.enums import WorkflowRunStatus, WorkflowStatus
from app.models.organization import Organization
from app.rbac.permissions import Permission, role_has_permission
from app.rbac.visibility import present_workflow_run, present_workflow_run_detail
from app.schemas.workflow import (
    WorkflowActivate,
    WorkflowCreate,
    WorkflowDecision,
    WorkflowListOut,
    WorkflowOut,
    WorkflowRunCreate,
    WorkflowRunDetail,
    WorkflowRunListOut,
    WorkflowRunOut,
    WorkflowUpdate,
    WorkflowVersionCreate,
    WorkflowVersionOut,
)
from app.services.audit_service import record_audit
from app.workflows import service

router = APIRouter(tags=["workflows"])


async def _audit(
    db: AsyncSession,
    ctx: TenantContext,
    action: str,
    target_type: str,
    target_id: uuid.UUID,
    **meta,
) -> None:
    await record_audit(
        db,
        action=action,
        user_id=ctx.user.id,
        organization_id=ctx.organization_id,
        target_type=target_type,
        target_id=str(target_id),
        metadata=meta or None,
    )


# ---- workflows -------------------------------------------------------------- #
@router.get("/workflows", response_model=WorkflowListOut)
async def list_workflows(
    workflow_status: WorkflowStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.WORKFLOW_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> WorkflowListOut:
    items, total = await service.list_workflows(
        db,
        ctx.organization_id,
        status=workflow_status.value if workflow_status else None,
        limit=limit,
        offset=offset,
    )
    return WorkflowListOut(items=[WorkflowOut.model_validate(w) for w in items], total=total)


@router.post("/workflows", response_model=WorkflowOut, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    body: WorkflowCreate,
    ctx: TenantContext = Depends(require_permission(Permission.WORKFLOW_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> WorkflowOut:
    workflow, version = await service.create_workflow(
        db,
        ctx.organization_id,
        ctx.user.id,
        name=body.name,
        description=body.description,
        definition=body.definition,
    )
    await _audit(db, ctx, "workflow.created", "workflow", workflow.id, version_id=str(version.id))
    await db.commit()
    await db.refresh(workflow)
    return WorkflowOut.model_validate(workflow)


@router.get("/workflows/{workflow_id}", response_model=WorkflowOut)
async def get_workflow(
    workflow_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.WORKFLOW_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> WorkflowOut:
    return WorkflowOut.model_validate(
        await service.get_workflow(db, ctx.organization_id, workflow_id)
    )


@router.patch("/workflows/{workflow_id}", response_model=WorkflowOut)
async def update_workflow(
    workflow_id: uuid.UUID,
    body: WorkflowUpdate,
    ctx: TenantContext = Depends(require_permission(Permission.WORKFLOW_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> WorkflowOut:
    workflow = await service.get_workflow(db, ctx.organization_id, workflow_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        if key == "name" and value is None:
            continue
        setattr(workflow, key, value)
    await _audit(db, ctx, "workflow.updated", "workflow", workflow.id)
    await db.commit()
    await db.refresh(workflow)
    return WorkflowOut.model_validate(workflow)


@router.delete("/workflows/{workflow_id}", response_model=WorkflowOut)
async def archive_workflow(
    workflow_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.WORKFLOW_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> WorkflowOut:
    workflow = await service.archive(db, ctx.organization_id, workflow_id)
    await _audit(db, ctx, "workflow.archived", "workflow", workflow.id)
    await db.commit()
    await db.refresh(workflow)
    return WorkflowOut.model_validate(workflow)


@router.get("/workflows/{workflow_id}/versions", response_model=list[WorkflowVersionOut])
async def list_versions(
    workflow_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.WORKFLOW_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> list[WorkflowVersionOut]:
    versions = await service.list_versions(db, ctx.organization_id, workflow_id)
    return [WorkflowVersionOut.model_validate(v) for v in versions]


@router.post(
    "/workflows/{workflow_id}/versions",
    response_model=WorkflowVersionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_version(
    workflow_id: uuid.UUID,
    body: WorkflowVersionCreate,
    ctx: TenantContext = Depends(require_permission(Permission.WORKFLOW_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> WorkflowVersionOut:
    version = await service.create_version(
        db, ctx.organization_id, workflow_id, ctx.user.id, body.definition
    )
    await _audit(
        db, ctx, "workflow.version_created", "workflow", workflow_id, version=version.version_number
    )
    await db.commit()
    await db.refresh(version)
    return WorkflowVersionOut.model_validate(version)


@router.post("/workflows/{workflow_id}/activate", response_model=WorkflowOut)
async def activate_workflow(
    workflow_id: uuid.UUID,
    body: WorkflowActivate | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.WORKFLOW_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> WorkflowOut:
    workflow, token = await service.activate(
        db, ctx.organization_id, workflow_id, ctx.user.id, body.version_id if body else None
    )
    await _audit(
        db,
        ctx,
        "workflow.activated",
        "workflow",
        workflow.id,
        version_id=str(workflow.active_version_id),
    )
    await db.commit()
    await db.refresh(workflow)
    out = WorkflowOut.model_validate(workflow)
    if token:
        # Shown once. Callers send it as the X-Noblen-Webhook-Token header.
        out.webhook_token = token
        out.webhook_path = f"/api/v1/hooks/workflows/{workflow.id}"
    return out


@router.post("/workflows/{workflow_id}/pause", response_model=WorkflowOut)
async def pause_workflow(
    workflow_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.WORKFLOW_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> WorkflowOut:
    workflow = await service.pause(db, ctx.organization_id, workflow_id)
    await _audit(db, ctx, "workflow.paused", "workflow", workflow.id)
    await db.commit()
    await db.refresh(workflow)
    return WorkflowOut.model_validate(workflow)


# ---- runs ------------------------------------------------------------------- #
@router.post(
    "/workflows/{workflow_id}/runs",
    response_model=WorkflowRunOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_run(
    workflow_id: uuid.UUID,
    body: WorkflowRunCreate,
    ctx: TenantContext = Depends(require_permission(Permission.WORKFLOW_RUN)),
    db: AsyncSession = Depends(get_db),
) -> WorkflowRunOut:
    """Queue a manual run. A worker executes it; poll GET /workflow-runs/{id}."""
    if body.version_id is not None and not (
        user_is_platform_superuser(ctx.user)
        or role_has_permission(ctx.role_name, Permission.WORKFLOW_MANAGE)
    ):
        raise PermissionDeniedError("Test runs of a specific version need 'workflow:manage'.")
    run = await service.start_manual_run(
        db,
        ctx.organization_id,
        workflow_id,
        ctx.user.id,
        input=body.input,
        version_id=body.version_id,
    )
    await db.commit()
    await db.refresh(run)
    return present_workflow_run(run, ctx.viewer)


@router.get("/workflow-runs", response_model=WorkflowRunListOut)
async def list_runs(
    workflow_id: uuid.UUID | None = None,
    run_status: WorkflowRunStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.WORKFLOW_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> WorkflowRunListOut:
    items, total = await service.list_runs(
        db,
        ctx.organization_id,
        workflow_id=workflow_id,
        status=run_status.value if run_status else None,
        limit=limit,
        offset=offset,
    )
    return WorkflowRunListOut(
        items=[present_workflow_run(r, ctx.viewer) for r in items], total=total
    )


@router.get("/workflow-runs/{run_id}", response_model=WorkflowRunDetail)
async def get_run(
    run_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.WORKFLOW_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> WorkflowRunDetail:
    """Metadata for `workflow:view`; content only for the person the run acts for,
    plus the approval requests an approver decides (ADR-0035)."""
    run = await service.get_run(db, ctx.organization_id, run_id)
    return present_workflow_run_detail(run, await service.step_runs(db, run), ctx.viewer)


async def _decide(
    run_id: uuid.UUID, body: WorkflowDecision, ctx: TenantContext, db: AsyncSession, approve: bool
) -> WorkflowRunOut:
    run = await service.get_run(db, ctx.organization_id, run_id)
    org = await db.get(Organization, ctx.organization_id)
    if org is not None and org.require_independent_approval and run.initiated_by == ctx.user.id:
        raise PermissionDeniedError(
            "This organization requires someone other than the run's initiator to decide."
        )
    run = await service.decide(
        db, ctx.organization_id, run_id, ctx.user.id, approve=approve, note=body.note
    )
    await _audit(
        db,
        ctx,
        "workflow.approval_decided",
        "workflow_run",
        run.id,
        decision="approved" if approve else "rejected",
    )
    await db.commit()
    await db.refresh(run)
    return present_workflow_run(run, ctx.viewer)


@router.post("/workflow-runs/{run_id}/approve", response_model=WorkflowRunOut)
async def approve_run(
    run_id: uuid.UUID,
    body: WorkflowDecision | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_APPROVE_ACTIONS)),
    db: AsyncSession = Depends(get_db),
) -> WorkflowRunOut:
    return await _decide(run_id, body or WorkflowDecision(), ctx, db, approve=True)


@router.post("/workflow-runs/{run_id}/reject", response_model=WorkflowRunOut)
async def reject_run(
    run_id: uuid.UUID,
    body: WorkflowDecision | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_APPROVE_ACTIONS)),
    db: AsyncSession = Depends(get_db),
) -> WorkflowRunOut:
    return await _decide(run_id, body or WorkflowDecision(), ctx, db, approve=False)


@router.post("/workflow-runs/{run_id}/cancel", response_model=WorkflowRunOut)
async def cancel_run(
    run_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_OPERATE)),
    db: AsyncSession = Depends(get_db),
) -> WorkflowRunOut:
    run = await service.cancel(db, ctx.organization_id, run_id)
    await _audit(db, ctx, "workflow.run_cancelled", "workflow_run", run.id)
    await db.commit()
    await db.refresh(run)
    return present_workflow_run(run, ctx.viewer)
