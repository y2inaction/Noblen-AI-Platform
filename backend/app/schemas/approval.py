"""Approval schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    agent_id: uuid.UUID | None
    conversation_id: uuid.UUID | None
    tool_call_id: str
    tool_name: str
    request_payload: dict[str, Any]
    reason: str | None
    status: str
    requested_by: uuid.UUID | None
    expires_at: datetime | None
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    rejected_by: uuid.UUID | None
    rejected_at: datetime | None
    created_at: datetime


class ApprovalListOut(BaseModel):
    items: list[ApprovalOut]
    total: int


class ApprovalDecisionOut(BaseModel):
    approval: ApprovalOut
    # Present when approving resumed the run to completion.
    execution: dict[str, Any] | None = None
