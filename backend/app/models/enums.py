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


# ruff: noqa: UP042  (str+Enum kept intentionally for JSON/DB value compatibility)
