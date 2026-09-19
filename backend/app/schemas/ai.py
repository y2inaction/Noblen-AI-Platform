"""Request/response schemas for the AI API.

The client may choose a model and (from a restricted allow-list) a provider, but
never arbitrary provider configuration and never any credential. Attribution
(organization/user) is set server-side from the authenticated context.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.ai.types import MessageRole

# Providers a client is allowed to request explicitly. `mock` is never client-selectable.
ALLOWED_CHAT_PROVIDERS = {"anthropic", "openai"}
ALLOWED_EMBEDDING_PROVIDERS = {"openai"}


class AIMessageIn(BaseModel):
    role: MessageRole
    content: str = Field(min_length=1, max_length=100_000)


class GenerateRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[AIMessageIn] = Field(min_length=1, max_length=200)
    system: str | None = Field(default=None, max_length=100_000)
    provider: str | None = Field(default=None)
    model: str | None = Field(default=None, max_length=128)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_output_tokens: int | None = Field(default=None, ge=1, le=32000)


class GenerateResponseOut(BaseModel):
    content: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost: float | None
    estimated_cost_currency: str
    estimated_cost_note: str = "Estimate for cost visibility only — not a provider invoice."
    request_id: str
    finish_reason: str | None
    latency_ms: int


class EmbedRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    texts: list[str] = Field(min_length=1, max_length=256)
    provider: str | None = None
    model: str | None = Field(default=None, max_length=128)


class EmbedResponseOut(BaseModel):
    provider: str
    model: str
    vectors: list[list[float]]
    dimensions: int
    input_tokens: int
    estimated_cost: float | None
    estimated_cost_currency: str
    request_id: str
    latency_ms: int


class UsageRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    user_id: uuid.UUID | None
    agent_id: uuid.UUID | None
    workflow_id: uuid.UUID | None
    provider: str
    model: str
    operation: str
    request_id: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost: float | None
    estimated_cost_currency: str
    latency_ms: int
    status: str
    error_type: str | None
    created_at: datetime


class UsageListOut(BaseModel):
    items: list[UsageRecordOut]
    total: int
