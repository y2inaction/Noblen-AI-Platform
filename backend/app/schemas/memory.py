"""Long-term memory schemas (Noblen AI 3.0, M4)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import MemoryScope


class MemoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: MemoryScope = MemoryScope.USER
    content: str = Field(min_length=1, max_length=4000)
    category: str | None = Field(default=None, max_length=64)
    # Required for AGENT scope; ignored otherwise.
    agent_id: uuid.UUID | None = None


class MemoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str | None = Field(default=None, min_length=1, max_length=4000)
    category: str | None = Field(default=None, max_length=64)


class MemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    scope: str
    user_id: uuid.UUID | None
    agent_id: uuid.UUID | None
    content: str
    category: str | None
    created_by: uuid.UUID | None
    created_by_agent_id: uuid.UUID | None
    source_run_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class MemoryListOut(BaseModel):
    items: list[MemoryOut]
    total: int


class MemoryForgetOut(BaseModel):
    deleted: int
