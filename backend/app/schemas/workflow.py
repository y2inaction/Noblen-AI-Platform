"""Workflow schemas (Noblen AI 3.0, M5). Definitions are validated by
`app.workflows.definition`; here they are opaque JSON objects."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkflowCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=5000)
    definition: dict[str, Any]


class WorkflowUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=5000)


class WorkflowVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition: dict[str, Any]


class WorkflowActivate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: uuid.UUID | None = None


class WorkflowRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: dict[str, Any] = Field(default_factory=dict)
    # Test a specific (e.g. draft) version; requires workflow:manage.
    version_id: uuid.UUID | None = None


class WorkflowDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=2000)


class WorkflowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    description: str | None
    status: str
    active_version_id: uuid.UUID | None
    trigger_type: str | None
    event_name: str | None
    next_run_at: datetime | None
    run_as_user_id: uuid.UUID | None
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    # Webhook triggers: returned only by the activation that created the token.
    webhook_token: str | None = None
    webhook_path: str | None = None


class WorkflowListOut(BaseModel):
    items: list[WorkflowOut]
    total: int


class WorkflowVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_id: uuid.UUID
    version_number: int
    definition: dict[str, Any]
    created_by: uuid.UUID | None
    created_at: datetime


class WorkflowStepRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sequence: int
    step_id: str
    step_type: str
    attempt: int
    status: str
    output: dict[str, Any] | None
    error: str | None
    agent_run_id: uuid.UUID | None
    decision: str | None
    decided_by: uuid.UUID | None
    decision_note: str | None
    started_at: datetime | None
    finished_at: datetime | None


class WorkflowRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    workflow_id: uuid.UUID
    version_id: uuid.UUID
    status: str
    trigger_type: str
    trigger_detail: dict[str, Any]
    # Content (ADR-0035, M8): None when withheld (see content_withheld_reason).
    input: dict[str, Any] | None
    current_step: str | None
    current_attempt: int
    steps_executed: int
    initiated_by: uuid.UUID | None
    depth: int
    next_attempt_at: datetime | None
    error_code: str | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    content_withheld: bool = False
    # Why content is withheld: "restricted_sources" or "unknown_provenance".
    content_withheld_reason: str | None = None


class WorkflowRunDetail(WorkflowRunOut):
    context: dict[str, Any] | None = None
    steps: list[WorkflowStepRunOut] = Field(default_factory=list)


class WorkflowRunListOut(BaseModel):
    items: list[WorkflowRunOut]
    total: int
