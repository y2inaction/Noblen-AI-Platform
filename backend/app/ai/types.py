"""Provider-independent request/response types for the AI Core.

Nothing here is coupled to Anthropic or OpenAI. Provider adapters translate
these to/from vendor-specific shapes and never leak vendor objects outward.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MessageRole = Literal["system", "user", "assistant", "tool"]


class ToolSpec(BaseModel):
    """A normalized tool definition passed to a provider for tool-calling."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    """A normalized tool-call request emitted by the model."""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    role: MessageRole
    content: str = ""
    # For assistant messages that requested tools:
    tool_calls: list[ToolCall] | None = None
    # For tool-result messages (role == "tool"):
    tool_call_id: str | None = None
    name: str | None = None


class GenerationRequest(BaseModel):
    """A normalized generation request.

    Attribution fields (organization/user/agent/workflow) are populated
    server-side from the authenticated context — never trusted from a client.
    """

    model_config = ConfigDict(extra="forbid")

    messages: list[Message] = Field(min_length=1)
    system: str | None = None
    provider: str | None = None
    model: str | None = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_output_tokens: int | None = Field(default=None, ge=1, le=32000)
    stream: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Tool-calling (empty list = plain generation, identical to Phase 2 behavior).
    tools: list[ToolSpec] = Field(default_factory=list)

    # Attribution (set by the gateway/endpoint/runtime, not by the client).
    organization_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    agent_version_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    workflow_id: uuid.UUID | None = None


class EmbeddingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    texts: list[str] = Field(min_length=1)
    provider: str | None = None
    model: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    organization_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class GenerationResponse(BaseModel):
    content: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    # Cost is ALWAYS an estimate, never a provider invoice. None when the model's
    # price is not configured.
    estimated_cost: Decimal | None = None
    estimated_cost_currency: str = "USD"
    request_id: str
    finish_reason: str | None = None
    # Present when the model requested tool execution (finish_reason == "tool_use").
    tool_calls: list[ToolCall] = Field(default_factory=list)
    latency_ms: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class EmbeddingResponse(BaseModel):
    provider: str
    model: str
    vectors: list[list[float]]
    dimensions: int = 0
    input_tokens: int = 0
    estimated_cost: Decimal | None = None
    estimated_cost_currency: str = "USD"
    request_id: str
    latency_ms: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class StreamEventType(str, Enum):  # noqa: UP042 - str+Enum for JSON/value compatibility
    DELTA = "delta"
    DONE = "done"
    ERROR = "error"


class StreamChunk(BaseModel):
    """A normalized streaming event. Vendor stream event types are never exposed."""

    type: StreamEventType
    delta: str = ""
    # Populated on the terminal DONE chunk.
    finish_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    # Populated on an ERROR chunk.
    error_code: str | None = None
    error_message: str | None = None
