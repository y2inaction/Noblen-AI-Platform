"""Recovery of runs interrupted by a crashed worker or server (hardening).

A process that dies mid-run leaves the run `RUNNING` with nobody working on it.
This module finds such runs (no row change for `STALE_RUN_SECONDS`; every step
updates the row, and runtime budgets and timeouts keep live runs far below that)
and resolves them **without ever repeating a side effect**:

- **Agent runs** are escalated to a person. The interrupted step may already have
  sent an email or created a task, so re-running it could do it twice; a human
  checks the trace instead.
- **Workflow runs** interrupted between steps, or during a step that has no
  external effect (condition, approval request), are re-queued and continue where
  they stopped. Runs interrupted during an agent or tool step are escalated.

Claims use `FOR UPDATE SKIP LOCKED`, so several workers can run recovery safely.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.agent import Agent
from app.models.enums import (
    RunStatus,
    RunStepStatus,
    RunStepType,
    WorkflowRunStatus,
    WorkflowStepStatus,
)
from app.models.run import AgentRun, AgentRunStep
from app.models.workflow import Workflow, WorkflowRun, WorkflowStepRun
from app.rbac.permissions import Permission
from app.services import work_service
from app.services.audit_service import record_audit

logger = get_logger("recovery")

AGENT_REASON = (
    "The run stopped unexpectedly (its worker or server was interrupted). "
    "It was not retried automatically; check the trace for what it already did."
)
# Workflow steps whose interrupted attempt can safely run again.
_SAFE_STEP_TYPES = {"condition", "approval"}


def _now() -> datetime:
    return datetime.now(UTC)


def _cutoff(now: datetime | None = None) -> datetime:
    return (now or _now()) - timedelta(seconds=settings.STALE_RUN_SECONDS)


async def recover_agent_runs(db: AsyncSession, now: datetime | None = None) -> int:
    stale = (
        (
            await db.execute(
                select(AgentRun)
                .where(
                    AgentRun.status == RunStatus.RUNNING.value, AgentRun.updated_at < _cutoff(now)
                )
                .order_by(AgentRun.updated_at)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for run in stale:
        run.step_count += 1
        db.add(
            AgentRunStep(
                organization_id=run.organization_id,
                run_id=run.id,
                sequence=run.step_count,
                step_type=RunStepType.ESCALATION.value,
                status=RunStepStatus.SUCCEEDED.value,
                name="recovery",
                detail={"reason": "interrupted", "last_activity": run.updated_at.isoformat()},
            )
        )
        run.status = RunStatus.ESCALATED.value
        run.escalation_reason = AGENT_REASON
        run.error_code = "interrupted"
        run.completed_at = _now()
        agent_name = (
            await db.execute(select(Agent.name).where(Agent.id == run.agent_id))
        ).scalar_one_or_none()
        await work_service.notify_permission_holders(
            db,
            run.organization_id,
            Permission.AGENT_APPROVE_ACTIONS,
            kind="run_escalated",
            title=f"Interrupted: {agent_name or 'an agent'} needs a human check",
            body=AGENT_REASON,
            link={"type": "agent_run", "id": str(run.id)},
            also=run.initiated_by,
        )
        await record_audit(
            db,
            action="agent.run_recovered",
            user_id=None,
            organization_id=run.organization_id,
            target_type="agent_run",
            target_id=str(run.id),
            metadata={"outcome": "escalated", "agent_id": str(run.agent_id)},
        )
    await db.flush()
    return len(stale)


async def recover_workflow_runs(db: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    stale = (
        (
            await db.execute(
                select(WorkflowRun)
                .where(
                    WorkflowRun.status == WorkflowRunStatus.RUNNING.value,
                    WorkflowRun.updated_at < _cutoff(now),
                )
                .order_by(WorkflowRun.updated_at)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    outcome = {"requeued": 0, "escalated": 0}
    for run in stale:
        in_flight = (
            await db.execute(
                select(WorkflowStepRun)
                .where(
                    WorkflowStepRun.run_id == run.id,
                    WorkflowStepRun.status == WorkflowStepStatus.RUNNING.value,
                )
                .order_by(WorkflowStepRun.sequence.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        safe = in_flight is None or in_flight.step_type in _SAFE_STEP_TYPES
        if in_flight is not None:
            in_flight.status = WorkflowStepStatus.FAILED.value
            in_flight.error = "Interrupted: the worker stopped during this step."
            in_flight.finished_at = _now()
        detail: dict[str, Any] = {
            "step": in_flight.step_id if in_flight else run.current_step,
            "step_type": in_flight.step_type if in_flight else None,
        }
        if safe:
            run.status = WorkflowRunStatus.QUEUED.value
            run.next_attempt_at = None
            outcome["requeued"] += 1
            detail["outcome"] = "requeued"
        else:
            run.status = WorkflowRunStatus.ESCALATED.value
            run.error_code = "interrupted"
            run.error = (
                f"The worker stopped during step '{detail['step']}' ({detail['step_type']}). "
                "It was not retried automatically because it may already have acted; "
                "check the step before re-running the workflow."
            )
            run.finished_at = _now()
            outcome["escalated"] += 1
            detail["outcome"] = "escalated"
            name = (
                await db.execute(select(Workflow.name).where(Workflow.id == run.workflow_id))
            ).scalar_one_or_none()
            await work_service.notify_permission_holders(
                db,
                run.organization_id,
                Permission.AGENT_OPERATE,
                kind="workflow_escalated",
                title=f"Interrupted: workflow {name or ''} needs a human check".replace("  ", " "),
                body=run.error,
                link={"type": "workflow_run", "id": str(run.id)},
                also=run.initiated_by,
            )
        await record_audit(
            db,
            action="workflow.run_recovered",
            user_id=None,
            organization_id=run.organization_id,
            target_type="workflow_run",
            target_id=str(run.id),
            metadata=detail,
        )
    await db.flush()
    return outcome


async def recover_stale_runs(db: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    agents = await recover_agent_runs(db, now)
    workflows = await recover_workflow_runs(db, now)
    result = {
        "agent_runs_escalated": agents,
        **{f"workflow_runs_{k}": v for k, v in workflows.items()},
    }
    if any(result.values()):
        logger.warning("stale_runs_recovered", **result)
    return result
