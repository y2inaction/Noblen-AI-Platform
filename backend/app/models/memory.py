"""Long-term memory (Noblen AI 3.0, M4).

One tenant-owned table with an explicit `scope`, instead of one unstructured
store:

- USER: what a person wants agents to remember about them. Private: only that
  person, and agents running on their behalf, can read it.
- AGENT: what one agent has learned about its tasks and workflows. Shared by
  everyone who uses that agent, so agent writes need approval by default.
- ORGANIZATION: short institutional facts every agent in the organization may
  use. Written only by people with `memory:manage`.

Rows are short text facts, not documents (those belong in the Knowledge Engine).
Provenance (who or which agent/run wrote it) is recorded for auditability.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDMixin


class Memory(UUIDMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "memories"
    __table_args__ = (Index("ix_memories_org_scope", "organization_id", "scope"),)

    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    # Subject: the person (USER scope) or agent (AGENT scope) the memory belongs to.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="CASCADE"), nullable=True, index=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Provenance.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
