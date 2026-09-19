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
    AGENT_RUN = "agent:run"

    # Conversations / CRM / knowledge / workflows / tasks (used from later phases)
    CONVERSATION_VIEW = "conversation:view"
    LEAD_VIEW = "lead:view"
    LEAD_MANAGE = "lead:manage"
    KNOWLEDGE_VIEW = "knowledge:view"
    KNOWLEDGE_MANAGE = "knowledge:manage"
    WORKFLOW_VIEW = "workflow:view"
    WORKFLOW_MANAGE = "workflow:manage"
    TASK_VIEW = "task:view"
    TASK_MANAGE = "task:manage"

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
]

# MANAGER: operational management (member work + team + agent/workflow authoring).
_MANAGER_PERMISSIONS = _MEMBER_PERMISSIONS + [
    Permission.TEAM_MANAGE,
    Permission.MEMBER_INVITE,
    Permission.AGENT_CREATE,
    Permission.AGENT_UPDATE,
    Permission.WORKFLOW_MANAGE,
    Permission.ANALYTICS_VIEW,
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
]

# Default role -> permission-code mapping. SUPER_ADMIN gets the wildcard.
DEFAULT_ROLE_PERMISSIONS: dict[str, list[str]] = {
    RoleName.SUPER_ADMIN.value: [Permission.WILDCARD],
    RoleName.ADMIN.value: sorted(set(_ADMIN_PERMISSIONS)),
    RoleName.MANAGER.value: sorted(set(_MANAGER_PERMISSIONS)),
    RoleName.MEMBER.value: sorted(set(_MEMBER_PERMISSIONS)),
    RoleName.VIEWER.value: sorted(set(_VIEW_PERMISSIONS)),
}


def permissions_for_role(role_name: str) -> set[str]:
    """Return the set of permission codes granted to a role name."""
    return set(DEFAULT_ROLE_PERMISSIONS.get(role_name, []))


def role_has_permission(role_name: str, permission: str) -> bool:
    """Server-side permission check for a role name."""
    granted = permissions_for_role(role_name)
    return Permission.WILDCARD in granted or permission in granted
