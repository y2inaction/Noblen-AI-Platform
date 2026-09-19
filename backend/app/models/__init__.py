"""Import all models so that SQLAlchemy's metadata is fully populated.

Alembic autogeneration and `Base.metadata.create_all` both rely on every model
being imported here.
"""

from app.db.base import Base
from app.models.ai_usage import AIUsageRecord
from app.models.audit import AuditLog
from app.models.auth import (
    EmailVerificationToken,
    PasswordResetToken,
    RefreshToken,
)
from app.models.membership import OrganizationMember
from app.models.organization import Organization
from app.models.rbac import Permission, Role, role_permissions
from app.models.team import Team, TeamMember
from app.models.user import User

__all__ = [
    "Base",
    "AIUsageRecord",
    "AuditLog",
    "EmailVerificationToken",
    "PasswordResetToken",
    "RefreshToken",
    "OrganizationMember",
    "Organization",
    "Permission",
    "Role",
    "role_permissions",
    "Team",
    "TeamMember",
    "User",
]
