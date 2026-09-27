"""Safe built-in tools for Phase 3.

These prove the runtime without touching anything dangerous. No shell, no arbitrary
HTTP, no SQL, no secrets, no filesystem.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.agents.tools.base import ToolContext, ToolHandler, ToolResult
from app.models.enums import ToolPermissionMode
from app.rbac.permissions import Permission


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
    required_permission = Permission.ORG_VIEW

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


class SearchKnowledgeTool(ToolHandler):
    """Retrieve relevant chunks from the agent's authorized knowledge bases (RAG).

    Authorization is enforced server-side by the runtime-provided
    ``context.knowledge_search`` capability — the model cannot widen scope or reach
    another organization's data through tool arguments.
    """

    handler_identifier = "search_knowledge"
    name = "search_knowledge"
    description = (
        "Search the organization's knowledge bases assigned to this agent for "
        "information relevant to a query. Returns matching passages with citations."
    )
    tool_type = "knowledge"
    required_permission = Permission.KNOWLEDGE_SEARCH
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer"},
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    output_schema = {"type": "object"}
    default_permission_mode = ToolPermissionMode.AUTO.value

    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return ToolResult.failure("`query` must be a non-empty string.")
        if context.knowledge_search is None:
            return ToolResult.failure("Knowledge search is not available for this agent.")
        top_k = arguments.get("top_k")
        result = await context.knowledge_search(query=query, top_k=top_k)
        return ToolResult.success(**result)


class ListDataTablesTool(ToolHandler):
    """List spreadsheet tables (from CSV/XLSX knowledge) the agent may query."""

    handler_identifier = "list_data_tables"
    name = "list_data_tables"
    description = (
        "List the data tables (from spreadsheets in this agent's knowledge bases) with "
        "their columns and types. Use before query_data_table."
    )
    tool_type = "knowledge"
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    output_schema = {"type": "object"}
    default_permission_mode = ToolPermissionMode.AUTO.value
    required_permission = Permission.KNOWLEDGE_SEARCH

    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        if context.knowledge_tables is None:
            return ToolResult.failure("Data tables are not available for this agent.")
        return ToolResult.success(**await context.knowledge_tables.list())


class QueryDataTableTool(ToolHandler):
    """Exact answers from tabular knowledge: filters, count/sum/avg/min/max, group-by."""

    handler_identifier = "query_data_table"
    name = "query_data_table"
    description = (
        "Query a data table exactly (for counts, totals, averages, rankings and lookups). "
        "filters: [{column, op: eq|ne|gt|gte|lt|lte|contains|is_null|not_null, value}]; "
        "aggregate: {op: count|sum|avg|min|max, column}; group_by: column; "
        "order_by: {column, descending}; columns: [...]; limit."
    )
    tool_type = "knowledge"
    input_schema = {
        "type": "object",
        "properties": {
            "table_id": {"type": "string"},
            "columns": {"type": "array", "items": {"type": "string"}},
            "filters": {"type": "array", "items": {"type": "object"}},
            "aggregate": {"type": "object"},
            "group_by": {"type": "string"},
            "order_by": {"type": "object"},
            "limit": {"type": "integer"},
        },
        "required": ["table_id"],
        "additionalProperties": False,
    }
    output_schema = {"type": "object"}
    default_permission_mode = ToolPermissionMode.AUTO.value
    required_permission = Permission.KNOWLEDGE_SEARCH

    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        if context.knowledge_tables is None:
            return ToolResult.failure("Data tables are not available for this agent.")
        spec = {k: v for k, v in arguments.items() if k != "table_id"}
        return await context.knowledge_tables.query(str(arguments["table_id"]), spec)


def _all_builtin_tools() -> list[ToolHandler]:
    from app.agents.tools.work_tools import WORK_TOOLS

    return [
        GetCurrentTimeTool(),
        GetOrganizationSettingsTool(),
        EchoTool(),
        SearchKnowledgeTool(),
        ListDataTablesTool(),
        QueryDataTableTool(),
        *WORK_TOOLS,
    ]


BUILTIN_TOOLS: list[ToolHandler] = _all_builtin_tools()
