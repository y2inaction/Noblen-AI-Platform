"""User (platform account) model.

A user is a global identity that can belong to multiple organizations via
OrganizationMember. Users are NOT tenant-scoped themselves; their access to a
tenant's data is always mediated by a membership + role.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.models.enums import UserStatus

if TYPE_CHECKING:
    from app.models.membership import OrganizationMember


class User(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)

    status: Mapped[UserStatus] = mapped_column(
        String(16), default=UserStatus.PENDING.value, nullable=False
    )
    is_email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Platform-level superuser (distinct from an org's SUPER_ADMIN role membership).
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    memberships: Mapped[list[OrganizationMember]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
