"""Workflow management, triggers and human decisions (tenant-scoped).

Triggers only *queue* runs; the engine (`app.workflows.engine`) executes them on
a worker. Every run acts under one person's authority (`initiated_by`):

- manual runs: the person who started them;
- schedule and event runs: the person who activated the workflow.

That person's current role is re-checked before every step, so revoking a role
or a membership stops their automations.
"""

from __future__ import annotations

import contextvars
import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools.registry import tool_registry
from app.core.config import settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.db.tenant import tenant_scoped
from app.models.agent import Agent
from app.models.enums import (
    WorkflowRunStatus,
    WorkflowStatus,
    WorkflowStepStatus,
    WorkflowTriggerType,
)
from app.models.organization import Organization
from app.models.workflow import Workflow, WorkflowRun, WorkflowStepRun, WorkflowVersion
from app.services.audit_service import record_audit
from app.workflows.definition import AgentStep, ToolStep, WorkflowDefinition, parse
from app.workflows.templating import TemplateError

logger = get_logger("workflows")

_TERMINAL = {
    WorkflowRunStatus.COMPLETED.value,
    WorkflowRunStatus.FAILED.value,
    WorkflowRunStatus.ESCALATED.value,
    WorkflowRunStatus.CANCELLED.value,
}
_TRIGGER = {
    "manual": WorkflowTriggerType.MANUAL.value,
    "schedule": WorkflowTriggerType.SCHEDULE.value,
    "event": WorkflowTriggerType.EVENT.value,
    "webhook": WorkflowTriggerType.WEBHOOK.value,
}

# Set by the engine while a run executes, so events it causes carry its depth.
current_depth: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "workflow_depth", default=None
)


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- #
# Definitions
# --------------------------------------------------------------------------- #
async def validate_definition(
    db: AsyncSession, organization_id: uuid.UUID, raw: dict[str, Any]
) -> WorkflowDefinition:
    """Schema + graph + references to agents and tools in this organization."""
    try:
        definition = parse(raw)
    except (PydanticValidationError, TemplateError) as exc:
        raise ValidationError(_first_error(exc)) from exc
    agent_ids = {s.agent_id for s in definition.steps if isinstance(s, AgentStep)}
    if agent_ids:
        found = set(
            (
                await db.execute(
                    select(Agent.id).where(
                        Agent.organization_id == organization_id, Agent.id.in_(agent_ids)
                    )
                )
            ).scalars()
        )
        if missing := agent_ids - found:
            raise ValidationError(f"Unknown agent: {sorted(str(a) for a in missing)[0]}.")
    for step in definition.steps:
        if isinstance(step, ToolStep):
            handler = tool_registry.get_by_name(step.tool)
            if handler is None:
                raise ValidationError(f"Step '{step.id}': unknown tool '{step.tool}'.")
            if not handler.available_in_workflows:
                raise ValidationError(
                    f"Step '{step.id}': '{step.tool}' needs an agent and cannot be a "
                    "workflow tool step. Use an agent step instead."
                )
    return definition


def _first_error(exc: Exception) -> str:
    if isinstance(exc, PydanticValidationError):
        err = exc.errors()[0]
        where = ".".join(str(p) for p in err.get("loc", ()))
        message = str(err.get("msg", "invalid")).removeprefix("Value error, ")
        return f"Invalid workflow definition ({where}): {message}" if where else message
    return str(exc)


# --------------------------------------------------------------------------- #
# Workflows and versions
# --------------------------------------------------------------------------- #
async def get_workflow(
    db: AsyncSession, organization_id: uuid.UUID, workflow_id: uuid.UUID
) -> Workflow:
    workflow = (
        await db.execute(
            tenant_scoped(select(Workflow), Workflow, organization_id).where(
                Workflow.id == workflow_id
            )
        )
    ).scalar_one_or_none()
    if workflow is None:
        raise NotFoundError("Workflow not found.")
    return workflow


async def list_workflows(
    db: AsyncSession,
    organization_id: uuid.UUID,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Workflow], int]:
    stmt = tenant_scoped(select(Workflow), Workflow, organization_id)
    count = tenant_scoped(select(func.count(Workflow.id)), Workflow, organization_id)
    if status:
        stmt, count = stmt.where(Workflow.status == status), count.where(Workflow.status == status)
    else:
        archived = WorkflowStatus.ARCHIVED.value
        stmt, count = (
            stmt.where(Workflow.status != archived),
            count.where(Workflow.status != archived),
        )
    total = int((await db.execute(count)).scalar_one())
    rows = await db.execute(stmt.order_by(Workflow.created_at.desc()).limit(limit).offset(offset))
    return list(rows.scalars()), total


async def create_workflow(
    db: AsyncSession,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    name: str,
    description: str | None,
    definition: dict[str, Any],
) -> tuple[Workflow, WorkflowVersion]:
    await validate_definition(db, organization_id, definition)
    workflow = Workflow(
        organization_id=organization_id,
        name=name.strip(),
        description=description,
        created_by=user_id,
    )
    db.add(workflow)
    await db.flush()
    version = await _add_version(db, workflow, user_id, definition)
    return workflow, version


async def _add_version(
    db: AsyncSession, workflow: Workflow, user_id: uuid.UUID, definition: dict[str, Any]
) -> WorkflowVersion:
    latest = (
        await db.execute(
            select(func.max(WorkflowVersion.version_number)).where(
                WorkflowVersion.workflow_id == workflow.id
            )
        )
    ).scalar_one()
    version = WorkflowVersion(
        organization_id=workflow.organization_id,
        workflow_id=workflow.id,
        version_number=(latest or 0) + 1,
        # Store the normalised form (defaults filled in) so runs are reproducible.
        definition=parse(definition).model_dump(mode="json", by_alias=True),
        created_by=user_id,
    )
    db.add(version)
    await db.flush()
    return version


async def create_version(
    db: AsyncSession,
    organization_id: uuid.UUID,
    workflow_id: uuid.UUID,
    user_id: uuid.UUID,
    definition: dict[str, Any],
) -> WorkflowVersion:
    workflow = await get_workflow(db, organization_id, workflow_id)
    if workflow.status == WorkflowStatus.ARCHIVED.value:
        raise ConflictError("Archived workflows cannot be changed.")
    await validate_definition(db, organization_id, definition)
    return await _add_version(db, workflow, user_id, definition)


async def list_versions(
    db: AsyncSession, organization_id: uuid.UUID, workflow_id: uuid.UUID
) -> list[WorkflowVersion]:
    await get_workflow(db, organization_id, workflow_id)
    rows = await db.execute(
        select(WorkflowVersion)
        .where(WorkflowVersion.workflow_id == workflow_id)
        .order_by(WorkflowVersion.version_number.desc())
    )
    return list(rows.scalars())


async def get_version(
    db: AsyncSession, organization_id: uuid.UUID, workflow_id: uuid.UUID, version_id: uuid.UUID
) -> WorkflowVersion:
    version = (
        await db.execute(
            select(WorkflowVersion).where(
                WorkflowVersion.id == version_id,
                WorkflowVersion.workflow_id == workflow_id,
                WorkflowVersion.organization_id == organization_id,
            )
        )
    ).scalar_one_or_none()
    if version is None:
        raise NotFoundError("Workflow version not found.")
    return version


async def latest_version(db: AsyncSession, workflow: Workflow) -> WorkflowVersion:
    version = (
        await db.execute(
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == workflow.id)
            .order_by(WorkflowVersion.version_number.desc())
            .limit(1)
        )
    ).scalar_one()
    return version


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #
async def _org_timezone(db: AsyncSession, organization_id: uuid.UUID) -> ZoneInfo:
    name = (
        await db.execute(select(Organization.timezone).where(Organization.id == organization_id))
    ).scalar_one_or_none()
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def next_fire(definition: WorkflowDefinition, after: datetime, tz: ZoneInfo) -> datetime | None:
    """The next scheduled time strictly after `after` (UTC), or None if unscheduled."""
    trigger = definition.trigger
    if trigger.type != "schedule":
        return None
    if trigger.every_minutes is not None:
        return after + timedelta(minutes=trigger.every_minutes)
    assert trigger.daily_at is not None
    hour, minute = (int(p) for p in trigger.daily_at.split(":"))
    local = after.astimezone(tz)
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local:
        candidate = (local + timedelta(days=1)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
    return candidate.astimezone(UTC)


async def activate(
    db: AsyncSession,
    organization_id: uuid.UUID,
    workflow_id: uuid.UUID,
    user_id: uuid.UUID,
    version_id: uuid.UUID | None = None,
) -> tuple[Workflow, str | None]:
    """Make a version live. Triggered runs will act under the activating user.

    For webhook triggers, returns a new secret token (shown once; only its hash
    is stored). Every activation rotates it."""
    workflow = await get_workflow(db, organization_id, workflow_id)
    if workflow.status == WorkflowStatus.ARCHIVED.value:
        raise ConflictError("Archived workflows cannot be activated.")
    version = (
        await get_version(db, organization_id, workflow_id, version_id)
        if version_id
        else await latest_version(db, workflow)
    )
    # Re-validate: agents or tools may have changed since the version was cut.
    definition = await validate_definition(db, organization_id, version.definition)
    workflow.active_version_id = version.id
    workflow.status = WorkflowStatus.ACTIVE.value
    workflow.run_as_user_id = user_id
    workflow.trigger_type = _TRIGGER[definition.trigger.type]
    workflow.event_name = definition.trigger.event
    workflow.next_run_at = next_fire(definition, _now(), await _org_timezone(db, organization_id))
    token: str | None = None
    if definition.trigger.type == "webhook":
        token = secrets.token_urlsafe(32)
        workflow.webhook_token_hash = hash_token(token)
    else:
        workflow.webhook_token_hash = None
    await db.flush()
    return workflow, token


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def trigger_webhook(
    db: AsyncSession, workflow_id: uuid.UUID, token: str | None, payload: dict[str, Any]
) -> WorkflowRun:
    """Queue a run for an inbound webhook. Any mismatch is a 404, so callers
    cannot probe which workflows exist."""
    workflow = await db.get(Workflow, workflow_id)
    if (
        workflow is None
        or not token
        or workflow.status != WorkflowStatus.ACTIVE.value
        or workflow.trigger_type != WorkflowTriggerType.WEBHOOK.value
        or workflow.webhook_token_hash is None
        or workflow.active_version_id is None
        or not hmac.compare_digest(workflow.webhook_token_hash, hash_token(token))
    ):
        raise NotFoundError("Not found.")
    version = await get_version(
        db, workflow.organization_id, workflow.id, workflow.active_version_id
    )
    return await queue_run(
        db,
        workflow,
        version,
        trigger_type=WorkflowTriggerType.WEBHOOK.value,
        initiated_by=workflow.run_as_user_id,
        input=payload,
        trigger_detail={"webhook": True},
    )


async def pause(db: AsyncSession, organization_id: uuid.UUID, workflow_id: uuid.UUID) -> Workflow:
    """Stop triggers. Queued and waiting runs are cancelled when next picked up."""
    workflow = await get_workflow(db, organization_id, workflow_id)
    if workflow.status != WorkflowStatus.ACTIVE.value:
        raise ConflictError("Only an active workflow can be paused.")
    workflow.status = WorkflowStatus.PAUSED.value
    workflow.next_run_at = None
    await db.flush()
    return workflow


async def archive(db: AsyncSession, organization_id: uuid.UUID, workflow_id: uuid.UUID) -> Workflow:
    workflow = await get_workflow(db, organization_id, workflow_id)
    workflow.status = WorkflowStatus.ARCHIVED.value
    workflow.next_run_at = None
    await db.flush()
    return workflow


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #
async def queue_run(
    db: AsyncSession,
    workflow: Workflow,
    version: WorkflowVersion,
    *,
    trigger_type: str,
    initiated_by: uuid.UUID | None,
    input: dict[str, Any] | None = None,
    trigger_detail: dict[str, Any] | None = None,
    depth: int = 0,
) -> WorkflowRun:
    definition = parse(version.definition)
    run = WorkflowRun(
        organization_id=workflow.organization_id,
        workflow_id=workflow.id,
        version_id=version.id,
        status=WorkflowRunStatus.QUEUED.value,
        trigger_type=trigger_type,
        trigger_detail=trigger_detail or {},
        input=input or {},
        context={},
        current_step=definition.steps[0].id,
        initiated_by=initiated_by,
        depth=depth,
    )
    db.add(run)
    await db.flush()
    await record_audit(
        db,
        action="workflow.run_queued",
        user_id=initiated_by,
        organization_id=workflow.organization_id,
        target_type="workflow_run",
        target_id=str(run.id),
        metadata={"workflow_id": str(workflow.id), "trigger": trigger_type},
    )
    return run


async def start_manual_run(
    db: AsyncSession,
    organization_id: uuid.UUID,
    workflow_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    input: dict[str, Any],
    version_id: uuid.UUID | None = None,
) -> WorkflowRun:
    """Queue a manual run of the active version, or a test run of `version_id`."""
    workflow = await get_workflow(db, organization_id, workflow_id)
    if version_id is not None:
        version = await get_version(db, organization_id, workflow_id, version_id)
        await validate_definition(db, organization_id, version.definition)
        detail = {"test": True}
    else:
        if workflow.status != WorkflowStatus.ACTIVE.value or workflow.active_version_id is None:
            raise ConflictError("The workflow is not active.")
        version = await get_version(db, organization_id, workflow_id, workflow.active_version_id)
        detail = {}
    return await queue_run(
        db,
        workflow,
        version,
        trigger_type=WorkflowTriggerType.MANUAL.value,
        initiated_by=user_id,
        input=input,
        trigger_detail=detail,
    )


async def get_run(db: AsyncSession, organization_id: uuid.UUID, run_id: uuid.UUID) -> WorkflowRun:
    run = (
        await db.execute(
            tenant_scoped(select(WorkflowRun), WorkflowRun, organization_id).where(
                WorkflowRun.id == run_id
            )
        )
    ).scalar_one_or_none()
    if run is None:
        raise NotFoundError("Workflow run not found.")
    return run


async def list_runs(
    db: AsyncSession,
    organization_id: uuid.UUID,
    *,
    workflow_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[WorkflowRun], int]:
    stmt = tenant_scoped(select(WorkflowRun), WorkflowRun, organization_id)
    count = tenant_scoped(select(func.count(WorkflowRun.id)), WorkflowRun, organization_id)
    filters = []
    if workflow_id:
        filters.append(WorkflowRun.workflow_id == workflow_id)
    if status:
        filters.append(WorkflowRun.status == status)
    stmt, count = stmt.where(*filters), count.where(*filters)
    total = int((await db.execute(count)).scalar_one())
    rows = await db.execute(
        stmt.order_by(WorkflowRun.created_at.desc()).limit(limit).offset(offset)
    )
    return list(rows.scalars()), total


async def step_runs(db: AsyncSession, run: WorkflowRun) -> list[WorkflowStepRun]:
    rows = await db.execute(
        select(WorkflowStepRun)
        .where(WorkflowStepRun.run_id == run.id)
        .order_by(WorkflowStepRun.sequence)
    )
    return list(rows.scalars())


async def pending_decision(db: AsyncSession, run: WorkflowRun) -> WorkflowStepRun:
    step = (
        await db.execute(
            select(WorkflowStepRun)
            .where(
                WorkflowStepRun.run_id == run.id,
                WorkflowStepRun.status == WorkflowStepStatus.WAITING.value,
                WorkflowStepRun.agent_run_id.is_(None),
                WorkflowStepRun.decision.is_(None),
            )
            .order_by(WorkflowStepRun.sequence.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if run.status != WorkflowRunStatus.WAITING.value or step is None:
        raise ConflictError("This run is not waiting for a decision.")
    return step


async def decide(
    db: AsyncSession,
    organization_id: uuid.UUID,
    run_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    approve: bool,
    note: str | None,
) -> WorkflowRun:
    """Record a reviewer's decision and hand the run back to the worker."""
    run = await get_run(db, organization_id, run_id)
    step = await pending_decision(db, run)
    step.decision = "approved" if approve else "rejected"
    step.decided_by = user_id
    step.decision_note = note
    run.status = WorkflowRunStatus.QUEUED.value
    run.next_attempt_at = None
    await db.flush()
    return run


async def cancel(db: AsyncSession, organization_id: uuid.UUID, run_id: uuid.UUID) -> WorkflowRun:
    run = await get_run(db, organization_id, run_id)
    if run.status in _TERMINAL:
        raise ConflictError("The run has already finished.")
    if run.status == WorkflowRunStatus.RUNNING.value:
        raise ConflictError("The run is executing a step; cancel it once that step ends.")
    run.status = WorkflowRunStatus.CANCELLED.value
    run.error_code = "cancelled"
    run.finished_at = _now()
    await db.flush()
    return run


# --------------------------------------------------------------------------- #
# Triggers
# --------------------------------------------------------------------------- #
async def emit_event(
    db: AsyncSession, organization_id: uuid.UUID, event: str, payload: dict[str, Any]
) -> int:
    """Queue a run for every active workflow in the organization listening to `event`.

    Events caused by a running workflow carry its depth + 1; chains deeper than
    WORKFLOW_MAX_EVENT_DEPTH are dropped (and logged), so workflows cannot
    trigger each other forever.
    """
    parent = current_depth.get()
    depth = 0 if parent is None else parent + 1
    workflows = (
        (
            await db.execute(
                select(Workflow).where(
                    Workflow.organization_id == organization_id,
                    Workflow.status == WorkflowStatus.ACTIVE.value,
                    Workflow.event_name == event,
                )
            )
        )
        .scalars()
        .all()
    )
    if not workflows:
        return 0
    if depth > settings.WORKFLOW_MAX_EVENT_DEPTH:
        logger.warning("workflow_event_depth_exceeded", workflow_event=event, depth=depth)
        return 0
    queued = 0
    for workflow in workflows:
        if workflow.active_version_id is None:  # pragma: no cover - set on activation
            continue
        version = await get_version(db, organization_id, workflow.id, workflow.active_version_id)
        await queue_run(
            db,
            workflow,
            version,
            trigger_type=WorkflowTriggerType.EVENT.value,
            initiated_by=workflow.run_as_user_id,
            input=payload,
            trigger_detail={"event": event},
            depth=depth,
        )
        queued += 1
    return queued


async def queue_due_schedules(db: AsyncSession, now: datetime | None = None) -> int:
    """Queue one run for each active scheduled workflow that is due (all orgs)."""
    now = now or _now()
    due = (
        (
            await db.execute(
                select(Workflow)
                .where(
                    Workflow.status == WorkflowStatus.ACTIVE.value,
                    Workflow.next_run_at.is_not(None),
                    Workflow.next_run_at <= now,
                )
                .order_by(Workflow.next_run_at)
                .limit(50)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for workflow in due:
        assert workflow.active_version_id is not None
        version = await get_version(
            db, workflow.organization_id, workflow.id, workflow.active_version_id
        )
        await queue_run(
            db,
            workflow,
            version,
            trigger_type=WorkflowTriggerType.SCHEDULE.value,
            initiated_by=workflow.run_as_user_id,
            trigger_detail={"scheduled_for": workflow.next_run_at.isoformat()}
            if workflow.next_run_at
            else {},
        )
        # Missed slots (worker down) are not replayed: one run, then the next slot.
        workflow.next_run_at = next_fire(
            parse(version.definition), now, await _org_timezone(db, workflow.organization_id)
        )
    await db.flush()
    return len(due)
