"""Integration schemas (Noblen AI 3.0, M6). Secrets are write-only."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    IntegrationProvider,
    IntegrationStatus,
    ToolPermissionMode,
    ToolRiskLevel,
)


class ConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: IntegrationProvider
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9 _.\-]*$")
    config: dict[str, Any] = Field(default_factory=dict)
    secret: dict[str, Any] = Field(default_factory=dict)


class ConnectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9 _.\-]*$"
    )
    status: IntegrationStatus | None = None
    config: dict[str, Any] | None = None
    # Keys replace stored secret values; a key set to null removes it.
    secret: dict[str, Any] | None = None


class ConnectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    provider: str
    name: str
    status: str
    config: dict[str, Any]
    # Which secret fields are set, masked (e.g. "••••abcd"). Never the values.
    secret_fields: dict[str, str] = Field(default_factory=dict)
    last_used_at: datetime | None
    last_error: str | None
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class ConnectionTestOut(BaseModel):
    ok: bool
    error: str | None = None


class McpToolOut(BaseModel):
    id: uuid.UUID
    tool_id: uuid.UUID
    name: str
    remote_name: str
    description: str
    input_schema: dict[str, Any]
    risk_level: str
    permission_mode: str
    enabled: bool
    available: bool


class McpToolUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    risk_level: ToolRiskLevel | None = None
    permission_mode: ToolPermissionMode | None = None
