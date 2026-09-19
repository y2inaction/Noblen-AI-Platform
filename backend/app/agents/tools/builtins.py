"""Safe built-in tools for Phase 3.

These prove the runtime without touching anything dangerous. No shell, no arbitrary
HTTP, no SQL, no secrets, no filesystem.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.agents.tools.base import ToolContext, ToolHandler, ToolResult
from app.models.enums import ToolPermissionMode


class GetCurrentTimeTool(ToolHandler):
    handler_identifier = "get_current_time"
    name = "get_current_time"
    description = "Return the current UTC time (ISO 8601) and the organization timezone."
    tool_type = "system"
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    output_schema = {
        "type": "object",
        "properties": {"utc": {"type": "string"}, "timezone": {"type": "string"}},
    }
    default_permission_mode = ToolPermissionMode.AUTO.value

    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult.success(
            utc=datetime.now(UTC).isoformat(),
            timezone=context.org_settings.get("timezone", "UTC"),
        )


class GetOrganizationSettingsTool(ToolHandler):
    handler_identifier = "get_organization_settings"
    name = "get_organization_settings"
    description = "Return the current organization's non-sensitive settings."
    tool_type = "system"
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    output_schema = {"type": "object"}
    default_permission_mode = ToolPermissionMode.AUTO.value

    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        # Only the safe subset placed on the context — never secrets.
        return ToolResult.success(**context.org_settings)


class EchoTool(ToolHandler):
    """A trivial tool used to prove the runtime loop and approval flow end-to-end."""

    handler_identifier = "echo"
    name = "echo"
    description = "Echo back the provided text. Useful for testing the agent runtime."
    tool_type = "system"
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }
    output_schema = {"type": "object", "properties": {"text": {"type": "string"}}}
    # Demonstrates the human-in-the-loop path by default.
    default_permission_mode = ToolPermissionMode.APPROVAL_REQUIRED.value

    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        text = arguments.get("text")
        if not isinstance(text, str):
            return ToolResult.failure("`text` must be a string.")
        return ToolResult.success(text=text)


BUILTIN_TOOLS: list[ToolHandler] = [
    GetCurrentTimeTool(),
    GetOrganizationSettingsTool(),
    EchoTool(),
]
