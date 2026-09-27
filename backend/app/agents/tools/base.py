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

from app.models.enums import ToolPermissionMode, ToolRiskLevel


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
    # Tenant-, agent- and run-bound access to tasks and notifications
    # (`app.services.work_service.AgentWorkspace`), injected by the runtime.
    workspace: Any = None
    # Structured queries over tabular knowledge (M3), scoped like knowledge_search.
    knowledge_tables: Any = None
    # Run-bound long-term memory (`app.services.memory_service.AgentMemory`, M4).
    memory: Any = None


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
    #: Risk of the tool's side effects. Declared in code (not the DB) so it cannot
    #: be loosened by editing catalogue rows. HIGH always requires human approval.
    risk_level: str = ToolRiskLevel.LOW.value
    #: RBAC permission the *initiating user* must hold for an agent to use this
    #: tool on their behalf — an agent can never exceed its user's rights.
    required_permission: str | None = None
    #: Whether workflow tool steps may call this tool (M5). Tools that need an
    #: agent's capabilities (knowledge scoped to an agent, agent memory) cannot.
    available_in_workflows: bool = False

    def effective_mode(self, configured_mode: str) -> str:
        """Combine the configured permission mode with the tool's risk level.

        Configuration may tighten a policy or disable a tool, but a HIGH-risk
        tool can never run without approval.
        """
        if (
            self.risk_level == ToolRiskLevel.HIGH.value
            and configured_mode == ToolPermissionMode.AUTO.value
        ):
            return ToolPermissionMode.APPROVAL_REQUIRED.value
        return configured_mode

    @abc.abstractmethod
    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        """Run the tool. Must not raise for expected failures — return ToolResult.failure."""
