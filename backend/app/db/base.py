"""Declarative base and common column mixins.

Phase 1 uses portable SQLAlchemy types (Uuid, JSON, DateTime) so the suite can
run on SQLite while production targets PostgreSQL (see DECISIONS.md ADR-0006).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all ORM models."""


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class UUIDMixin:
    """Primary key as a UUID."""

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    """created_at / updated_at timestamps (UTC)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


class TenantMixin:
    """Every tenant-owned record MUST carry an organization_id.

    This is the backbone of row-level multi-tenancy (ARCHITECTURE.md §4).
    """

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


# Re-exported for convenience in model modules.
__all__ = ["Base", "UUIDMixin", "TimestampMixin", "TenantMixin", "String"]
