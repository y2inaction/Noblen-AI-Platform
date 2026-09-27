"""Permission catalogue and default role -> permission mappings.

Permissions are checked SERVER-SIDE only; the client is never trusted
(ARCHITECTURE.md §5). Roles map to permission sets so new roles/permissions can
be added as data. A `*` wildcard grants everything (used by SUPER_ADMIN).
"""

from __future__ import annotations

from app.models.enums import RoleName


class Permission:
    """Stable permission codes, grouped by resource. Add new ones here."""

    # Organization
    ORG_VIEW = "org:view"
    ORG_MANAGE = "org:manage"
    ORG_MANAGE_MEMBERS = "org:manage_members"
    ORG_MANAGE_BILLING = "org:manage_billing"

    # Users / members
    MEMBER_VIEW = "member:view"
    MEMBER_INVITE = "member:invite"
    MEMBER_UPDATE_ROLE = "member:update_role"
    MEMBER_REMOVE = "member:remove"

    # Teams
    TEAM_VIEW = "team:view"
    TEAM_MANAGE = "team:manage"

    # Agents (Phase 3)
    AGENT_VIEW = "agent:view"
    AGENT_CREATE = "agent:create"
    AGENT_UPDATE = "agent:update"
    AGENT_DELETE = "agent:delete"
    AGENT_RUN = "agent:run"  # execute
    AGENT_MANAGE_VERSIONS = "agent:manage_versions"
    AGENT_APPROVE_ACTIONS = "agent:approve_actions"
    # Change an agent's operating state (activate/pause) without editing its config.
    AGENT_OPERATE = "agent:operate"

    # AI Operations (Noblen AI 3.0): run traces and workforce metrics
    RUN_VIEW = "run:view"
    OPERATIONS_VIEW = "operations:view"

    # Conversations (Phase 3)
    CONVERSATION_CREATE = "conversation:create"
    CONVERSATION_WRITE = "conversation:write"

    # Tools (Phase 3)
    TOOL_VIEW = "tool:view"
    TOOL_MANAGE = "tool:manage"

    # AI Core (Phase 2)
    AI_GENERATE = "ai:generate"
    AI_STREAM = "ai:stream"
    AI_EMBED = "ai:embed"
    AI_VIEW_USAGE = "ai:view_usage"

    # Conversations / CRM / knowledge / workflows / tasks (used from later phases)
    CONVERSATION_VIEW = "conversation:view"
    LEAD_VIEW = "lead:view"
    LEAD_MANAGE = "lead:manage"
    KNOWLEDGE_VIEW = "knowledge:view"
    KNOWLEDGE_MANAGE = "knowledge:manage"
    # Knowledge + RAG (Phase 4) — granular
    KNOWLEDGE_CREATE = "knowledge:create"
    KNOWLEDGE_UPDATE = "knowledge:update"
    KNOWLEDGE_DELETE = "knowledge:delete"
    KNOWLEDGE_INGEST = "knowledge:ingest"
    KNOWLEDGE_SEARCH = "knowledge:search"
    KNOWLEDGE_MANAGE_SOURCES = "knowledge:manage_sources"
    # M3: manage who can read a knowledge base/document; read everything (admins).
    KNOWLEDGE_MANAGE_ACCESS = "knowledge:manage_access"
    KNOWLEDGE_READ_ALL = "knowledge:read_all"
    WORKFLOW_VIEW = "workflow:view"
    WORKFLOW_MANAGE = "workflow:manage"
    TASK_VIEW = "task:view"
    TASK_MANAGE = "task:manage"
    # Memory (M4): read memories; write your own; manage agent/organization memory.
    MEMORY_VIEW = "memory:view"
    MEMORY_WRITE = "memory:write"
    MEMORY_MANAGE = "memory:manage"

    # Analytics / audit / settings
    ANALYTICS_VIEW = "analytics:view"
    AUDIT_VIEW = "audit:view"
    SETTINGS_MANAGE = "settings:manage"

    WILDCARD = "*"


# Complete list of concrete (non-wildcard) permissions — used for seeding.
ALL_PERMISSIONS: list[str] = [
    v
    for k, v in vars(Permission).items()
    if not k.startswith("_") and isinstance(v, str) and v != Permission.WILDCARD
]

# Read-only subset granted to VIEWER.
_VIEW_PERMISSIONS = [p for p in ALL_PERMISSIONS if p.endswith(":view")]

# MEMBER: normal business usage (view + run/use, no administration).
_MEMBER_PERMISSIONS = _VIEW_PERMISSIONS + [
    Permission.AGENT_RUN,
    Permission.LEAD_MANAGE,
    Permission.TASK_MANAGE,
    Permission.KNOWLEDGE_MANAGE,
    Permission.AI_GENERATE,
    Permission.AI_STREAM,
    Permission.AI_EMBED,
    Permission.CONVERSATION_CREATE,
    Permission.CONVERSATION_WRITE,
    Permission.KNOWLEDGE_SEARCH,
    Permission.MEMORY_WRITE,
]

# OPERATOR (AI Operator): supervises deployed agents — decides approvals,
# activates/pauses agents, watches runs, usage and operations. Cannot author or
# reconfigure agents, and has no member/org administration.
_OPERATOR_PERMISSIONS = _MEMBER_PERMISSIONS + [
    Permission.AGENT_APPROVE_ACTIONS,
    Permission.AGENT_OPERATE,
    Permission.ANALYTICS_VIEW,
    Permission.AI_VIEW_USAGE,
]

# MANAGER: operational management (operator work + team + agent/workflow authoring).
_MANAGER_PERMISSIONS = _OPERATOR_PERMISSIONS + [
    Permission.TEAM_MANAGE,
    Permission.MEMBER_INVITE,
    Permission.AGENT_CREATE,
    Permission.AGENT_UPDATE,
    Permission.AGENT_MANAGE_VERSIONS,
    Permission.AGENT_APPROVE_ACTIONS,
    Permission.TOOL_MANAGE,
    Permission.WORKFLOW_MANAGE,
    Permission.ANALYTICS_VIEW,
    Permission.AI_VIEW_USAGE,
    Permission.KNOWLEDGE_CREATE,
    Permission.KNOWLEDGE_UPDATE,
    Permission.KNOWLEDGE_INGEST,
    Permission.KNOWLEDGE_MANAGE_SOURCES,
    Permission.KNOWLEDGE_MANAGE_ACCESS,
    Permission.MEMORY_MANAGE,
]

# ADMIN: full organization administration.
_ADMIN_PERMISSIONS = _MANAGER_PERMISSIONS + [
    Permission.ORG_MANAGE,
    Permission.ORG_MANAGE_MEMBERS,
    Permission.ORG_MANAGE_BILLING,
    Permission.MEMBER_UPDATE_ROLE,
    Permission.MEMBER_REMOVE,
    Permission.AGENT_DELETE,
    Permission.AUDIT_VIEW,
    Permission.SETTINGS_MANAGE,
    Permission.KNOWLEDGE_DELETE,
    Permission.KNOWLEDGE_READ_ALL,
]

# Default role -> permission-code mapping. SUPER_ADMIN gets the wildcard.
DEFAULT_ROLE_PERMISSIONS: dict[str, list[str]] = {
    RoleName.SUPER_ADMIN.value: [Permission.WILDCARD],
    RoleName.ADMIN.value: sorted(set(_ADMIN_PERMISSIONS)),
    RoleName.MANAGER.value: sorted(set(_MANAGER_PERMISSIONS)),
    RoleName.OPERATOR.value: sorted(set(_OPERATOR_PERMISSIONS)),
    RoleName.MEMBER.value: sorted(set(_MEMBER_PERMISSIONS)),
    RoleName.VIEWER.value: sorted(set(_VIEW_PERMISSIONS)),
}


# Roles only a platform superuser may grant. SUPER_ADMIN carries the wildcard, so
# letting an org ADMIN assign it would be a privilege escalation.
PLATFORM_ONLY_ROLES: frozenset[str] = frozenset({RoleName.SUPER_ADMIN.value})


def permissions_for_role(role_name: str) -> set[str]:
    """Return the set of permission codes granted to a role name."""
    return set(DEFAULT_ROLE_PERMISSIONS.get(role_name, []))


def role_has_permission(role_name: str, permission: str) -> bool:
    """Server-side permission check for a role name."""
    granted = permissions_for_role(role_name)
    return Permission.WILDCARD in granted or permission in granted
