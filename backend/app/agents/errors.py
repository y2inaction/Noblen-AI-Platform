"""Agent Engine domain errors.

All subclass `AppError`, so the existing FastAPI handler renders them as structured
JSON with a stable code and status — no stack traces leak to clients.
"""

from __future__ import annotations

from app.core.exceptions import AppError


class AgentNotFound(AppError):
    status_code = 404
    error_code = "agent_not_found"


class AgentInactive(AppError):
    status_code = 409
    error_code = "agent_inactive"


class AgentVersionNotFound(AppError):
    status_code = 404
    error_code = "agent_version_not_found"


class NoActiveAgentVersion(AppError):
    status_code = 409
    error_code = "no_active_agent_version"


class AgentVersionImmutable(AppError):
    status_code = 409
    error_code = "agent_version_immutable"


class InvalidAgentStateTransition(AppError):
    status_code = 409
    error_code = "invalid_agent_state_transition"


class ToolNotFound(AppError):
    status_code = 404
    error_code = "tool_not_found"


class ToolDisabled(AppError):
    status_code = 409
    error_code = "tool_disabled"


class ToolNotAuthorized(AppError):
    status_code = 403
    error_code = "tool_not_authorized"


class ApprovalRequired(AppError):
    status_code = 202
    error_code = "approval_required"


class ApprovalNotFound(AppError):
    status_code = 404
    error_code = "approval_not_found"


class ApprovalExpired(AppError):
    status_code = 409
    error_code = "approval_expired"


class ApprovalInvalidState(AppError):
    status_code = 409
    error_code = "approval_invalid_state"


class RuntimeLimitExceeded(AppError):
    status_code = 409
    error_code = "runtime_limit_exceeded"


class ConversationNotFound(AppError):
    status_code = 404
    error_code = "conversation_not_found"
