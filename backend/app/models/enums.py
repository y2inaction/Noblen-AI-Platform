"""Shared enumerations used across models."""

from __future__ import annotations

import enum


class RoleName(str, enum.Enum):
    """Built-in roles. Additional roles can be added without code changes to
    the permission checker (roles map to permission sets in the DB)."""

    SUPER_ADMIN = "SUPER_ADMIN"  # platform-wide administration
    ADMIN = "ADMIN"  # organization administration
    MANAGER = "MANAGER"  # team & operational management
    OPERATOR = "OPERATOR"  # AI Operator: supervises agents, decides approvals
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


class ToolRiskLevel(str, enum.Enum):
    """Risk of a tool's side effects. HIGH always requires human approval."""

    LOW = "LOW"  # read-only / internal
    MEDIUM = "MEDIUM"  # internal side effects
    HIGH = "HIGH"  # external, financial, destructive, contractual


# ---- AI Workforce operations (Noblen AI 3.0, M1) ----
class RunStatus(str, enum.Enum):
    QUEUED = "QUEUED"  # accepted for background execution (M2)
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    ESCALATED = "ESCALATED"
    FAILED = "FAILED"


class RunStepType(str, enum.Enum):
    MODEL_CALL = "MODEL_CALL"
    TOOL_CALL = "TOOL_CALL"
    APPROVAL = "APPROVAL"
    ESCALATION = "ESCALATION"


class RunStepStatus(str, enum.Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    DENIED = "DENIED"
    PENDING = "PENDING"


# ---- Work items (Noblen AI 3.0, M2) ----
class TaskStatus(str, enum.Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


class TaskPriority(str, enum.Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


# ---- Knowledge + RAG (Phase 4) ----
class KnowledgeBaseStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ARCHIVED = "ARCHIVED"


class KnowledgeVisibility(str, enum.Enum):
    """Who inside the organization may read a knowledge base (Noblen AI 3.0, M3)."""

    ORGANIZATION = "ORGANIZATION"  # every member with knowledge permissions
    RESTRICTED = "RESTRICTED"  # only granted users/roles (plus creator and read-all)


class DocumentVisibility(str, enum.Enum):
    INHERIT = "INHERIT"  # anyone who can read the knowledge base
    RESTRICTED = "RESTRICTED"  # additionally requires a document grant


class GrantPrincipalType(str, enum.Enum):
    USER = "USER"
    ROLE = "ROLE"


class KnowledgeResourceType(str, enum.Enum):
    KNOWLEDGE_BASE = "KNOWLEDGE_BASE"
    DOCUMENT = "DOCUMENT"


class DocumentSourceType(str, enum.Enum):
    UPLOAD = "UPLOAD"
    TEXT = "TEXT"
    URL = "URL"  # accepted for forward-compat; ingestion not implemented in Phase 4


class DocumentStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"


# ruff: noqa: UP042  (str+Enum kept intentionally for JSON/DB value compatibility)
