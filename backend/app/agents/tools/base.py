"""Tool execution contract.

A tool receives ONLY a controlled `ToolContext` and validated `arguments`. It never
gets the DB session, environment, filesystem, secrets, or OS access. The context
carries a safe, read-only snapshot of just what tools may need.
"""

from __future__ import annotations

import abc
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.models.enums import ToolPermissionMode


@dataclass(frozen=True)
class ToolContext:
    organization_id: uuid.UUID
    user_id: uuid.UUID | None
    agent_id: uuid.UUID | None
    conversation_id: uuid.UUID | None
    # A safe, read-only subset of organization settings (never secrets).
    org_settings: dict[str, Any] = field(default_factory=dict)
    # Controlled capability injected by the runtime for knowledge-enabled agents.
    # An async callable(query, knowledge_base_ids, top_k) -> dict that performs a
    # tenant- AND agent-scoped retrieval. Tools never get raw DB/gateway access.
    knowledge_search: Any = None


@dataclass
class ToolResult:
    ok: bool
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @classmethod
    def success(cls, **output: Any) -> ToolResult:
        return cls(ok=True, output=output)

    @classmethod
    def failure(cls, error: str) -> ToolResult:
        return cls(ok=False, error=error)


class ToolHandler(abc.ABC):
    """Base class for all registered tool handlers."""

    #: Stable identifier linking a DB `Tool.handler_identifier` to this handler.
    handler_identifier: str = ""
    name: str = ""
    description: str = ""
    tool_type: str = "system"
    version: str = "1"
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}
    output_schema: dict[str, Any] = {"type": "object", "properties": {}}
    default_permission_mode: str = ToolPermissionMode.AUTO.value

    @abc.abstractmethod
    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        """Run the tool. Must not raise for expected failures — return ToolResult.failure."""
