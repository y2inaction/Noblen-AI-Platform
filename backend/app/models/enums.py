"""Shared enumerations used across models."""

from __future__ import annotations

import enum


class RoleName(str, enum.Enum):
    """Built-in roles. Additional roles can be added without code changes to
    the permission checker (roles map to permission sets in the DB)."""

    SUPER_ADMIN = "SUPER_ADMIN"  # platform-wide administration
    ADMIN = "ADMIN"  # organization administration
    MANAGER = "MANAGER"  # team & operational management
    MEMBER = "MEMBER"  # normal business usage
    VIEWER = "VIEWER"  # read-only access


class MembershipStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    INVITED = "INVITED"
    SUSPENDED = "SUSPENDED"


class UserStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    PENDING = "PENDING"
    DISABLED = "DISABLED"


# ---- Agent Engine (Phase 3) ----
class AgentStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    TESTING = "TESTING"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ARCHIVED = "ARCHIVED"


class AgentType(str, enum.Enum):
    """Classification only — commercial behavior is attached in later phases."""

    GENERAL = "GENERAL"
    ASSISTANT = "ASSISTANT"
    SALES = "SALES"
    CUSTOMER_SERVICE = "CUSTOMER_SERVICE"
    CONTENT = "CONTENT"
    EXECUTIVE = "EXECUTIVE"
    RESEARCH = "RESEARCH"


class MemoryMode(str, enum.Enum):
    NONE = "NONE"
    CONVERSATION = "CONVERSATION"
    PERSISTENT = "PERSISTENT"


class ToolPermissionMode(str, enum.Enum):
    AUTO = "AUTO"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    DISABLED = "DISABLED"


class ConversationStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class ConversationMessageRole(str, enum.Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ApprovalStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


# ruff: noqa: UP042  (str+Enum kept intentionally for JSON/DB value compatibility)
