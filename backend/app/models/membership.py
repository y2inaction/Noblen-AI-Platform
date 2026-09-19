"""OrganizationMember: links a user to an organization with a role.

This is the join that grants a user access to a tenant's data. The role name is
stored denormalised for fast permission checks and also linked to the Role row.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.models.enums import MembershipStatus, RoleName

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.rbac import Role
    from app.models.user import User


class OrganizationMember(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "organization_members"
    __table_args__ = (UniqueConstraint("user_id", "organization_id", name="uq_member_user_org"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("roles.id", ondelete="SET NULL"), nullable=True
    )
    # Denormalised role name for fast checks (kept in sync with role_id).
    role_name: Mapped[str] = mapped_column(
        String(64), default=RoleName.MEMBER.value, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), default=MembershipStatus.ACTIVE.value, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="memberships")
    organization: Mapped[Organization] = relationship(back_populates="members")
    role: Mapped[Role | None] = relationship(lazy="selectin")
