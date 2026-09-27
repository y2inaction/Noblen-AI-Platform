"""Workflow work for the background worker (Noblen AI 3.0, M5).

One `tick` does, in order: queue due scheduled runs; re-queue runs whose agent
step's AgentRun has finished (e.g. after its approval was decided); then claim
and advance one queued run. Claims use `FOR UPDATE SKIP LOCKED`, so several
workers can share the load.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.runtime import AgentRuntime
from app.core.logging import clear_context, get_logger
from app.models.enums import RunStatus, WorkflowRunStatus, WorkflowStepStatus
from app.models.run import AgentRun
from app.models.workflow import WorkflowRun, WorkflowStepRun
from app.workflows import service
from app.workflows.engine import WorkflowEngine

logger = get_logger("workflows.worker")


async def wake_finished_agent_steps(db: AsyncSession) -> int:
    """Re-queue WAITING runs whose agent step's run has reached a final state."""
    finished = (
        select(WorkflowStepRun.run_id)
        .join(AgentRun, AgentRun.id == WorkflowStepRun.agent_run_id)
        .where(
            WorkflowStepRun.status == WorkflowStepStatus.WAITING.value,
            AgentRun.status.in_(
                (RunStatus.COMPLETED.value, RunStatus.ESCALATED.value, RunStatus.FAILED.value)
            ),
        )
    )
    result = await db.execute(
        update(WorkflowRun)
        .where(
            WorkflowRun.status == WorkflowRunStatus.WAITING.value,
            WorkflowRun.id.in_(finished),
        )
        .values(status=WorkflowRunStatus.QUEUED.value)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def claim_next_workflow_run(db: AsyncSession) -> uuid.UUID | None:
    now = datetime.now(UTC)
    run = (
        await db.execute(
            select(WorkflowRun)
            .where(
                WorkflowRun.status == WorkflowRunStatus.QUEUED.value,
                or_(WorkflowRun.next_attempt_at.is_(None), WorkflowRun.next_attempt_at <= now),
            )
            .order_by(WorkflowRun.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if run is None:
        await db.rollback()
        return None
    run.status = WorkflowRunStatus.RUNNING.value
    await db.commit()
    return run.id


async def process_next_workflow_run(
    session_factory: Callable[[], AsyncSession], runtime: AgentRuntime
) -> uuid.UUID | None:
    async with session_factory() as db:
        run_id = await claim_next_workflow_run(db)
    if run_id is None:
        return None
    clear_context()
    async with session_factory() as db:
        run = await db.get(WorkflowRun, run_id)
        if run is None:  # pragma: no cover
            return run_id
        try:
            await WorkflowEngine(runtime).advance(db, run)
        except Exception:
            logger.exception("workflow_run_crashed", run_id=str(run_id))
            await db.rollback()
            crashed = await db.get(WorkflowRun, run_id)
            if crashed is not None and crashed.status == WorkflowRunStatus.RUNNING.value:
                crashed.status = WorkflowRunStatus.FAILED.value
                crashed.error_code = "worker_error"
                crashed.finished_at = datetime.now(UTC)
                await db.commit()
    return run_id


async def tick(
    session_factory: Callable[[], AsyncSession], runtime: AgentRuntime
) -> uuid.UUID | None:
    """Housekeeping, then advance one workflow run. Returns its id, or None if idle."""
    async with session_factory() as db:
        queued = await service.queue_due_schedules(db)
        woken = await wake_finished_agent_steps(db)
        await db.commit()
    if queued or woken:
        logger.info("workflow_tick", scheduled=queued, woken=woken)
    return await process_next_workflow_run(session_factory, runtime)
