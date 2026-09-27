"""Workflow engine models (Noblen AI 3.0, M5).

A `Workflow` is a tenant-owned automation. Its definition (trigger + steps) lives
in immutable `WorkflowVersion`s, so a run always executes exactly the version it
started with. Each execution is a `WorkflowRun` with one `WorkflowStepRun` per
step attempt: an append-only trace, like agent runs.

Agent steps create ordinary `AgentRun`s (linked from the step run), so agent
traces, approvals and escalation are reused rather than duplicated.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDMixin
from app.models.enums import WorkflowRunStatus, WorkflowStatus


class Workflow(UUIDMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "workflows"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default=WorkflowStatus.DRAFT.value, nullable=False, index=True
    )
    # The version triggers run. Integrity is kept by the service (no FK, which
    # would make workflows <-> workflow_versions circular).
    active_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    # Denormalised from the active version so the worker can find due work cheaply.
    trigger_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    event_name: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # Whose authority scheduled and event-triggered runs act under: the person
    # who activated the workflow. Re-checked against their current role per step.
    run_as_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class WorkflowVersion(UUIDMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "workflow_versions"
    __table_args__ = (
        UniqueConstraint("workflow_id", "version_number", name="uq_workflow_version"),
    )

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class WorkflowRun(UUIDMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "workflow_runs"

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workflow_versions.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), default=WorkflowRunStatus.QUEUED.value, nullable=False, index=True
    )
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # e.g. {"event": "task.created"} or {"test": true}. Never secrets.
    trigger_detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    input: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Step outputs, addressable from templates as steps.<id>.output.
    context: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    current_step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    steps_executed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # The person whose current permissions every step is checked against.
    initiated_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # How many workflow → event → workflow hops led here (loop guard).
    depth: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowStepRun(UUIDMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "workflow_step_runs"

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    step_id: Mapped[str] = mapped_column(String(64), nullable=False)
    step_type: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Agent steps: the AgentRun doing the work.
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Approval steps (and tool steps that require approval).
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
