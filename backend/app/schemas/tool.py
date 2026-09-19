"""Tool schemas (read + manage)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ToolOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str
    version: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    tool_type: str
    permission_mode: str
    enabled: bool
    handler_identifier: str
    created_at: datetime


class ToolListOut(BaseModel):
    items: list[ToolOut]
    total: int


class AgentToolAttach(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_id: uuid.UUID
    enabled: bool = True
    permission_mode: str | None = Field(default=None)


class AgentToolOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: uuid.UUID
    tool_id: uuid.UUID
    enabled: bool
    permission_mode: str | None
