"""Agent runs and their step-by-step execution trace (tenant-scoped).

A run is one execution of an agent on a task: it starts with a user message and
ends COMPLETED, ESCALATED or FAILED, possibly pausing in AWAITING_APPROVAL. Every
model call, tool call, approval and escalation is an append-only `AgentRunStep`.
This is the execution memory and audit trail the AI Operations layer reads.

Privacy: prompts and model text are NOT stored here (see AI_LOG_PROMPTS); the
conversation holds the content, the trace holds the operational facts.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDMixin
from app.models.enums import RunStatus


class AgentRun(UUIDMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "agent_runs"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agent_versions.id", ondelete="SET NULL"), nullable=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    initiated_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(24), default=RunStatus.RUNNING.value, nullable=False, index=True
    )
    # Conversation sequence where this run's working context begins. Resuming
    # after an approval reloads exactly [context_start_sequence, ...], so the
    # model always sees its own tool calls and their results, whatever the
    # agent's memory mode.
    context_start_sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    escalation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    step_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    model_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost: Mapped[Decimal] = mapped_column(
        Numeric(14, 6), default=Decimal("0"), nullable=False
    )
    last_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_model: Mapped[str | None] = mapped_column(String(128), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Provenance (M8, ADR-0037): references to the protected sources this run's
    # content derives from, never the content. NULL means unknown (recorded
    # before M8) and fails closed; `acting_role` is the person's role at start.
    sources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    sources_truncated: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    acting_role: Mapped[str | None] = mapped_column(String(32), nullable=True)


class AgentRunStep(UUIDMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "agent_run_steps"

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Operational detail only (e.g. finish_reason, risk level, approval id,
    # argument keys) — never prompt/response text.
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
