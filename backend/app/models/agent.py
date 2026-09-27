"""Agent and AgentVersion models (tenant-scoped, versioned).

An Agent is the mutable "handle" (name, status, active version pointer). Its
runnable configuration lives in immutable AgentVersion rows — an ACTIVE version is
never silently modified (see DECISIONS ADR-0014). Config that is genuinely flexible
(memory/tool/runtime settings) is stored as JSON; first-class fields stay relational.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDMixin
from app.models.enums import AgentStatus, AgentType, MemoryMode

if TYPE_CHECKING:
    pass


class Agent(UUIDMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "agents"
    __table_args__ = (UniqueConstraint("organization_id", "slug", name="uq_agent_org_slug"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(140), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(16), default=AgentStatus.DRAFT.value, nullable=False, index=True
    )
    agent_type: Mapped[str] = mapped_column(
        String(32), default=AgentType.GENERAL.value, nullable=False, index=True
    )

    # Draft/default configuration (the working copy edited before a version is cut).
    default_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    default_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    system_instructions: Mapped[str] = mapped_column(Text, default="", nullable=False)
    temperature: Mapped[float] = mapped_column(default=0.7, nullable=False)
    max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_mode: Mapped[str] = mapped_column(
        String(16), default=MemoryMode.CONVERSATION.value, nullable=False
    )
    configuration: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    agent_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    active_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "agent_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_agents_active_version_id",
        ),
        nullable=True,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    versions: Mapped[list[AgentVersion]] = relationship(
        back_populates="agent",
        cascade="all, delete-orphan",
        foreign_keys="AgentVersion.agent_id",
    )


class AgentVersion(UUIDMixin, TimestampMixin, TenantMixin, Base):
    """An immutable snapshot of an agent's runnable configuration."""

    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint("agent_id", "version_number", name="uq_agent_version_number"),
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)

    system_instructions: Mapped[str] = mapped_column(Text, default="", nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    temperature: Mapped[float] = mapped_column(default=0.7, nullable=False)
    max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    memory_configuration: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    tool_configuration: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    configuration: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # A version's own lifecycle marker (DRAFT/TESTING/ACTIVE/ARCHIVED).
    status: Mapped[str] = mapped_column(String(16), default=AgentStatus.DRAFT.value, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    agent: Mapped[Agent] = relationship(back_populates="versions", foreign_keys=[agent_id])
