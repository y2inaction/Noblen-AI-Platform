"""AI Operations: run traces and workforce metrics (tenant-scoped, read-only).

Answers "how well is this AI workforce actually performing?" from the run trace
(`agent_runs`, `agent_run_steps`), approvals and the AI usage ledger.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.db.tenant import tenant_scoped
from app.models.agent import Agent
from app.models.ai_usage import AIUsageRecord
from app.models.approval import Approval
from app.models.enums import ApprovalStatus, RunStatus, RunStepStatus, RunStepType
from app.models.run import AgentRun, AgentRunStep
from app.rbac.visibility import (
    Viewer,
    present_escalation_reason,
    resolve_source_access,
    sees_run_content,
)
from app.schemas.run import OperationsOverviewOut


async def list_runs(
    db: AsyncSession,
    organization_id: uuid.UUID,
    *,
    agent_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[AgentRun], int]:
    stmt = tenant_scoped(select(AgentRun), AgentRun, organization_id)
    count_stmt = tenant_scoped(select(func.count(AgentRun.id)), AgentRun, organization_id)
    if agent_id is not None:
        stmt = stmt.where(AgentRun.agent_id == agent_id)
        count_stmt = count_stmt.where(AgentRun.agent_id == agent_id)
    if status is not None:
        stmt = stmt.where(AgentRun.status == status)
        count_stmt = count_stmt.where(AgentRun.status == status)
    total = int((await db.execute(count_stmt)).scalar_one())
    rows = await db.execute(stmt.order_by(AgentRun.created_at.desc()).limit(limit).offset(offset))
    return list(rows.scalars().all()), total


async def get_run(
    db: AsyncSession, organization_id: uuid.UUID, run_id: uuid.UUID
) -> tuple[AgentRun, list[AgentRunStep]]:
    run = (
        await db.execute(
            tenant_scoped(select(AgentRun), AgentRun, organization_id).where(AgentRun.id == run_id)
        )
    ).scalar_one_or_none()
    if run is None:
        raise NotFoundError("Run not found.")
    steps = await db.execute(
        tenant_scoped(select(AgentRunStep), AgentRunStep, organization_id)
        .where(AgentRunStep.run_id == run.id)
        .order_by(AgentRunStep.sequence)
    )
    return run, list(steps.scalars().all())


async def _counts(db: AsyncSession, column: Any, *where: Any) -> dict[str, int]:
    rows = await db.execute(select(column, func.count()).where(*where).group_by(column))
    return {str(key): int(count) for key, count in rows.tuples().all()}


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


async def overview(
    db: AsyncSession, organization_id: uuid.UUID, viewer: Viewer, *, window_days: int = 30
) -> OperationsOverviewOut:
    org = organization_id
    since = datetime.now(UTC) - timedelta(days=window_days)

    agents_by_status = await _counts(db, Agent.status, Agent.organization_id == org)
    runs_by_status = await _counts(
        db, AgentRun.status, AgentRun.organization_id == org, AgentRun.created_at >= since
    )
    approvals_by_status = await _counts(
        db, Approval.status, Approval.organization_id == org, Approval.created_at >= since
    )
    tool_steps = await _counts(
        db,
        AgentRunStep.status,
        AgentRunStep.organization_id == org,
        AgentRunStep.step_type == RunStepType.TOOL_CALL.value,
        AgentRunStep.created_at >= since,
    )
    pending = int(
        (
            await db.execute(
                select(func.count(Approval.id)).where(
                    Approval.organization_id == org,
                    Approval.status == ApprovalStatus.PENDING.value,
                )
            )
        ).scalar_one()
    )

    usage_rows = await db.execute(
        select(
            AIUsageRecord.provider,
            AIUsageRecord.model,
            func.count(AIUsageRecord.id),
            func.coalesce(func.sum(AIUsageRecord.input_tokens), 0),
            func.coalesce(func.sum(AIUsageRecord.output_tokens), 0),
            func.coalesce(func.sum(AIUsageRecord.estimated_cost), 0),
        )
        .where(AIUsageRecord.organization_id == org, AIUsageRecord.created_at >= since)
        .group_by(AIUsageRecord.provider, AIUsageRecord.model)
    )
    usage_by_model: list[dict[str, Any]] = [
        {
            "provider": provider,
            "model": model,
            "requests": int(requests),
            "input_tokens": int(inp),
            "output_tokens": int(out),
            "estimated_cost": round(float(cost or 0), 6),
        }
        for provider, model, requests, inp, out, cost in usage_rows.tuples().all()
    ]

    finished = await db.execute(
        select(AgentRun.started_at, AgentRun.completed_at).where(
            AgentRun.organization_id == org,
            AgentRun.created_at >= since,
            AgentRun.started_at.is_not(None),
            AgentRun.completed_at.is_not(None),
        )
    )
    durations = [
        (done - start).total_seconds() for start, done in finished.tuples().all() if start and done
    ]

    escalations = await db.execute(
        select(
            AgentRun.id,
            AgentRun.agent_id,
            AgentRun.escalation_reason,
            AgentRun.completed_at,
            AgentRun.initiated_by,
            AgentRun.sources,
            AgentRun.sources_truncated,
        )
        .where(
            AgentRun.organization_id == org,
            AgentRun.status == RunStatus.ESCALATED.value,
            AgentRun.created_at >= since,
        )
        .order_by(AgentRun.created_at.desc())
        .limit(10)
    )

    recent = escalations.tuples().all()
    # The model's reason is run content (ADR-0035): shown under the M8 run rule.
    access = await resolve_source_access(
        db,
        viewer,
        org,
        [(row[5], row[6]) for row in recent if not sees_run_content(viewer, row[4])],
    )

    completed = runs_by_status.get(RunStatus.COMPLETED.value, 0)
    escalated = runs_by_status.get(RunStatus.ESCALATED.value, 0)
    failed = runs_by_status.get(RunStatus.FAILED.value, 0)
    concluded = completed + escalated + failed
    return OperationsOverviewOut(
        window_days=window_days,
        agents_by_status=agents_by_status,
        runs_total=sum(runs_by_status.values()),
        runs_by_status=runs_by_status,
        success_rate=_rate(completed, concluded),
        escalation_rate=_rate(escalated, concluded),
        failure_rate=_rate(failed, concluded),
        avg_run_seconds=round(sum(durations) / len(durations), 3) if durations else None,
        pending_approvals=pending,
        approvals_by_status=approvals_by_status,
        tool_calls=sum(tool_steps.values()),
        tool_failures=tool_steps.get(RunStepStatus.FAILED.value, 0),
        tool_denials=tool_steps.get(RunStepStatus.DENIED.value, 0),
        input_tokens=sum(u["input_tokens"] for u in usage_by_model),
        output_tokens=sum(u["output_tokens"] for u in usage_by_model),
        estimated_cost=round(sum(u["estimated_cost"] for u in usage_by_model), 6),
        usage_by_model=usage_by_model,
        recent_escalations=[
            {
                "run_id": str(run_id),
                "agent_id": str(agent_id),
                "reason": present_escalation_reason(
                    reason, initiated_by, sources, truncated, viewer, access
                ),
                "at": at.isoformat() if at else None,
            }
            for run_id, agent_id, reason, at, initiated_by, sources, truncated in recent
        ],
    )
