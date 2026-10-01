"""Agent run traces and AI-operations metrics (tenant-scoped, read-only)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, require_permission
from app.db.session import get_db
from app.models.enums import RunStatus
from app.rbac.permissions import Permission
from app.rbac.visibility import present_agent_run_detail, present_agent_runs
from app.schemas.run import OperationsOverviewOut, RunDetailOut, RunListOut
from app.services import operations_service

router = APIRouter(tags=["operations"])


@router.get("/runs", response_model=RunListOut)
async def list_runs(
    agent_id: uuid.UUID | None = None,
    run_status: RunStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.RUN_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> RunListOut:
    items, total = await operations_service.list_runs(
        db,
        ctx.organization_id,
        agent_id=agent_id,
        status=run_status.value if run_status else None,
        limit=limit,
        offset=offset,
    )
    return RunListOut(items=await present_agent_runs(db, items, ctx.viewer), total=total)


@router.get("/runs/{run_id}", response_model=RunDetailOut)
async def get_run(
    run_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.RUN_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> RunDetailOut:
    run, steps = await operations_service.get_run(db, ctx.organization_id, run_id)
    return await present_agent_run_detail(db, run, steps, ctx.viewer)


@router.get("/operations/overview", response_model=OperationsOverviewOut)
async def operations_overview(
    window_days: int = Query(default=30, ge=1, le=365),
    ctx: TenantContext = Depends(require_permission(Permission.OPERATIONS_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> OperationsOverviewOut:
    return await operations_service.overview(
        db, ctx.organization_id, ctx.viewer, window_days=window_days
    )
