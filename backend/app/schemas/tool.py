"""Tool schemas (read + manage)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    # Set for organization-owned tools (e.g. imported from an MCP server).
    organization_id: uuid.UUID | None = None
    created_at: datetime
    # Declared in code by the tool handler (not editable through the catalogue).
    risk_level: str | None = None
    required_permission: str | None = None

    @model_validator(mode="after")
    def _from_handler(self) -> ToolOut:
        from app.agents.tools.registry import tool_registry

        handler = tool_registry.get_by_identifier(self.handler_identifier)
        if handler is not None:
            self.risk_level = handler.risk_level
            self.required_permission = handler.required_permission
        return self


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
