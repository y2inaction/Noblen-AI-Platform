"""Conversation, message, and participant models (tenant-scoped).

Messages store operational events only — user/assistant/tool content and tool
call/result metadata. No hidden chain-of-thought is ever persisted.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDMixin
from app.models.enums import ConversationStatus

if TYPE_CHECKING:
    pass


class Conversation(UUIDMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "conversations"

    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default=ConversationStatus.ACTIVE.value, nullable=False, index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    conversation_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    messages: Mapped[list[ConversationMessage]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    participants: Mapped[list[ConversationParticipant]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class ConversationMessage(UUIDMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "conversation_messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Monotonic per-conversation ordering (created_at can tie at second precision).
    sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    # system | user | assistant | tool
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Structured operational metadata: tool_calls, tool_call_id, provider/model,
    # usage, etc. NEVER chain-of-thought.
    message_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Attribution
    agent_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class ConversationParticipant(UUIDMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "conversation_participants"
    __table_args__ = (
        UniqueConstraint("conversation_id", "user_id", name="uq_conversation_participant"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # "owner" | "member" | "agent"
    role: Mapped[str] = mapped_column(String(16), default="member", nullable=False)

    conversation: Mapped[Conversation] = relationship(back_populates="participants")
