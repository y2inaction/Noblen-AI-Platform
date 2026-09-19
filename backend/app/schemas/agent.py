"""Agent, version, and execution schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    agent_type: str = "GENERAL"
    default_provider: str | None = None
    default_model: str | None = Field(default=None, max_length=128)
    system_instructions: str = Field(default="", max_length=20000)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=32000)
    memory_mode: str = "CONVERSATION"
    configuration: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    agent_type: str | None = None
    default_provider: str | None = None
    default_model: str | None = Field(default=None, max_length=128)
    system_instructions: str | None = Field(default=None, max_length=20000)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=32000)
    memory_mode: str | None = None
    configuration: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    slug: str
    description: str | None
    status: str
    agent_type: str
    default_provider: str | None
    default_model: str | None
    system_instructions: str
    temperature: float
    max_tokens: int | None
    memory_mode: str
    configuration: dict[str, Any]
    active_version_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class AgentListOut(BaseModel):
    items: list[AgentOut]
    total: int


class AgentVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system_instructions: str | None = Field(default=None, max_length=20000)
    provider: str | None = None
    model: str | None = Field(default=None, max_length=128)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=32000)
    memory_configuration: dict[str, Any] | None = None
    tool_configuration: dict[str, Any] | None = None
    configuration: dict[str, Any] | None = None


class AgentVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: uuid.UUID
    version_number: int
    system_instructions: str
    provider: str | None
    model: str | None
    temperature: float
    max_tokens: int | None
    memory_configuration: dict[str, Any]
    tool_configuration: dict[str, Any]
    status: str
    created_at: datetime


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: uuid.UUID | None = None
    message: str = Field(min_length=1, max_length=20000)
    version_id: uuid.UUID | None = None


class ExecutionMessage(BaseModel):
    id: str
    role: str
    content: str


class ExecutionResponse(BaseModel):
    status: str  # completed | awaiting_approval
    conversation_id: uuid.UUID
    agent_id: uuid.UUID
    agent_version_id: uuid.UUID
    message: ExecutionMessage | None = None
    approval_id: uuid.UUID | None = None
    tool_name: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
