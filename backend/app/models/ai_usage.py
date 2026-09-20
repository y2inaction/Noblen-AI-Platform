"""AI usage metering model.

One row per AI request (success or failure). Tenant-scoped via `organization_id`.
Stores OPERATIONAL METADATA ONLY — never prompt/response content (privacy: see
docs/ai-core.md). `agent_id`/`workflow_id` are nullable and intentionally without
FKs yet, since those tables arrive in later phases.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Integer, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDMixin


class AIUsageRecord(UUIDMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "ai_usage_records"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    agent_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    # Knowledge attribution (Phase 4) — embeddings generated during ingestion/search.
    knowledge_base_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)

    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)  # generate|stream|embed
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Estimated cost only — never a provider invoice. Null when price is unknown.
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    estimated_cost_currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)

    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # status is "success" or "error"
    status: Mapped[str] = mapped_column(String(16), default="success", nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
