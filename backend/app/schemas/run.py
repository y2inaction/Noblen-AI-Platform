"""Agent run trace and AI-operations schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RunStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sequence: int
    step_type: str
    status: str
    name: str | None
    tool_call_id: str | None
    detail: dict[str, Any] | None
    error: str | None
    provider: str | None
    model: str | None
    input_tokens: int
    output_tokens: int
    latency_ms: int
    created_at: datetime


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    agent_id: uuid.UUID
    agent_version_id: uuid.UUID | None
    conversation_id: uuid.UUID | None
    initiated_by: uuid.UUID | None
    status: str
    escalation_reason: str | None
    error_code: str | None
    step_count: int
    model_calls: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    estimated_cost: Decimal
    last_provider: str | None
    last_model: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class RunDetailOut(RunOut):
    steps: list[RunStepOut] = Field(default_factory=list)


class RunListOut(BaseModel):
    items: list[RunOut]
    total: int


class OperationsOverviewOut(BaseModel):
    """How the organization's AI workforce is performing over a time window."""

    window_days: int
    agents_by_status: dict[str, int]
    runs_total: int
    runs_by_status: dict[str, int]
    success_rate: float | None
    escalation_rate: float | None
    failure_rate: float | None
    avg_run_seconds: float | None
    pending_approvals: int
    approvals_by_status: dict[str, int]
    tool_calls: int
    tool_failures: int
    tool_denials: int
    input_tokens: int
    output_tokens: int
    estimated_cost: float
    usage_by_model: list[dict[str, Any]]
    recent_escalations: list[dict[str, Any]]
