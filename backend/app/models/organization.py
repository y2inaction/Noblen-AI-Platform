"""Organization (tenant) model."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.membership import OrganizationMember
    from app.models.team import Team


class Organization(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Localisation (per-organization, never hard-coded globally).
    currency: Mapped[str] = mapped_column(String(8), default=settings.DEFAULT_CURRENCY)
    timezone: Mapped[str] = mapped_column(String(64), default=settings.DEFAULT_TIMEZONE)
    locale: Mapped[str] = mapped_column(String(16), default=settings.DEFAULT_LOCALE)

    members: Mapped[list[OrganizationMember]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    teams: Mapped[list[Team]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
