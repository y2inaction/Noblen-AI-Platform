"""Workforce tools: tasks and member notifications (Noblen AI 3.0, M2).

Real, useful organizational actions with no external integration required.
Handlers act only through `context.workspace`, which the runtime binds to the
run's organization, agent, run and initiating user.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from app.agents.tools.base import ToolContext, ToolHandler, ToolResult
from app.core.exceptions import AppError
from app.models.enums import TaskPriority, TaskStatus, ToolPermissionMode, ToolRiskLevel
from app.models.work import Task
from app.rbac.permissions import Permission

_PRIORITIES = [p.value for p in TaskPriority]
_STATUSES = [s.value for s in TaskStatus]


def _task_out(task: Task) -> dict[str, Any]:
    return {
        "id": str(task.id),
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "priority": task.priority,
        "due_at": task.due_at.isoformat() if task.due_at else None,
        "assignee_id": str(task.assignee_id) if task.assignee_id else None,
    }


def _parse_due(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("`due_at` must be an ISO 8601 date or datetime.") from exc


class _WorkTool(ToolHandler):
    """Shared plumbing: availability check and safe error mapping."""

    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        if context.workspace is None:
            return ToolResult.failure("Work items are not available in this context.")
        try:
            return await self.run(context, arguments)
        except (AppError, ValueError) as exc:  # validation/not-found: safe to show
            return ToolResult.failure(getattr(exc, "message", None) or str(exc))

    async def run(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        raise NotImplementedError


class CreateTaskTool(_WorkTool):
    handler_identifier = name = "create_task"
    description = (
        "Create a task (follow-up, action item or case) in the organization's task list, "
        "optionally assigned to a member by email and with a due date."
    )
    tool_type = "tasks"
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "priority": {"type": "string", "enum": _PRIORITIES},
            "due_at": {"type": "string", "description": "ISO 8601 date/datetime"},
            "assignee_email": {"type": "string"},
        },
        "required": ["title"],
        "additionalProperties": False,
    }
    risk_level = ToolRiskLevel.MEDIUM.value
    required_permission = Permission.TASK_MANAGE
    default_permission_mode = ToolPermissionMode.AUTO.value

    async def run(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        ws = context.workspace
        assignee = arguments.get("assignee_email")
        task = await ws.create_task(
            title=arguments["title"],
            description=arguments.get("description"),
            priority=arguments.get("priority") or TaskPriority.NORMAL.value,
            due_at=_parse_due(arguments.get("due_at")),
            assignee_id=await ws.member_id(assignee) if assignee else None,
        )
        return ToolResult.success(task=_task_out(task))


class ListTasksTool(_WorkTool):
    handler_identifier = name = "list_tasks"
    description = "List the organization's tasks, optionally only open ones or by status."
    tool_type = "tasks"
    input_schema = {
        "type": "object",
        "properties": {
            "open_only": {"type": "boolean"},
            "status": {"type": "string", "enum": _STATUSES},
            "limit": {"type": "integer"},
        },
        "additionalProperties": False,
    }
    risk_level = ToolRiskLevel.LOW.value
    required_permission = Permission.TASK_VIEW
    default_permission_mode = ToolPermissionMode.AUTO.value

    async def run(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        limit = arguments.get("limit") or 20
        tasks, total = await context.workspace.list_tasks(
            open_only=bool(arguments.get("open_only")),
            status=arguments.get("status"),
            limit=max(1, min(int(limit), 50)),
        )
        return ToolResult.success(total=total, tasks=[_task_out(t) for t in tasks])


class UpdateTaskTool(_WorkTool):
    handler_identifier = name = "update_task"
    description = "Update a task's status, priority, due date, assignee or details."
    tool_type = "tasks"
    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "status": {"type": "string", "enum": _STATUSES},
            "priority": {"type": "string", "enum": _PRIORITIES},
            "due_at": {"type": "string"},
            "assignee_email": {"type": "string"},
        },
        "required": ["task_id"],
        "additionalProperties": False,
    }
    risk_level = ToolRiskLevel.MEDIUM.value
    required_permission = Permission.TASK_MANAGE
    default_permission_mode = ToolPermissionMode.AUTO.value

    async def run(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        ws = context.workspace
        try:
            task_id = uuid.UUID(str(arguments["task_id"]))
        except ValueError:
            return ToolResult.failure("`task_id` is not a valid task id.")
        changes: dict[str, Any] = {
            k: arguments[k]
            for k in ("title", "description", "status", "priority")
            if k in arguments
        }
        if "due_at" in arguments:
            changes["due_at"] = _parse_due(arguments["due_at"])
        if "assignee_email" in arguments:
            email = arguments["assignee_email"]
            changes["assignee_id"] = await ws.member_id(email) if email else None
        task = await ws.update_task(task_id, changes)
        return ToolResult.success(task=_task_out(task))


class NotifyMemberTool(_WorkTool):
    handler_identifier = name = "notify_member"
    description = (
        "Send an in-app notification to a member of this organization (by email). "
        "Internal only — it cannot reach anyone outside the organization."
    )
    tool_type = "communication"
    input_schema = {
        "type": "object",
        "properties": {
            "recipient_email": {"type": "string"},
            "title": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["recipient_email", "title"],
        "additionalProperties": False,
    }
    risk_level = ToolRiskLevel.MEDIUM.value
    required_permission = Permission.MEMBER_VIEW
    default_permission_mode = ToolPermissionMode.AUTO.value

    async def run(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        ws = context.workspace
        title = str(arguments["title"]).strip()
        if not title:
            return ToolResult.failure("`title` must not be empty.")
        recipient = await ws.member_id(arguments["recipient_email"])
        note = await ws.notify_member(recipient, title[:255], arguments.get("body"))
        return ToolResult.success(
            notification_id=str(note.id), delivered_to=arguments["recipient_email"]
        )


WORK_TOOLS: list[ToolHandler] = [
    CreateTaskTool(),
    ListTasksTool(),
    UpdateTaskTool(),
    NotifyMemberTool(),
]
