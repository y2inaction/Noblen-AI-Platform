"""Integrations (Noblen AI 3.0, M6): credential references and imported MCP tools.

An `IntegrationConnection` is a named, tenant-owned connection to an external
service. Its non-secret settings live in `config`; its secrets are encrypted at
rest (`secret_ciphertext`, Fernet) and are never returned by the API or handed
to agents, tools or models. Tools refer to a connection (a *credential
reference*) and the integration gateway uses the secret on their behalf.

`IntegrationTool` records a tool discovered on a remote MCP server, with the
risk level and permission an administrator declared for it. It is linked to an
organization-owned `tools` catalogue row, so agents bind it like any other tool.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDMixin
from app.models.enums import IntegrationStatus, ToolRiskLevel


class IntegrationConnection(UUIDMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "integration_connections"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_integration_connection_name"),
    )

    provider: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default=IntegrationStatus.ACTIVE.value, nullable=False
    )
    # Non-secret settings (host, URL, sender address, ...).
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Fernet token of the JSON secret object. Never serialised to clients.
    secret_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Short, client-safe description of the last failure (never secrets).
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class IntegrationTool(UUIDMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "integration_tools"
    __table_args__ = (
        UniqueConstraint("connection_id", "remote_name", name="uq_integration_tool_remote"),
    )

    connection_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("integration_connections.id", ondelete="CASCADE"), nullable=False
    )
    tool_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tools.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    remote_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # Declared by an administrator; imported tools start HIGH (always approval).
    risk_level: Mapped[str] = mapped_column(
        String(16), default=ToolRiskLevel.HIGH.value, nullable=False
    )
    # Present on the remote server at the last sync.
    available: Mapped[bool] = mapped_column(default=True, nullable=False)
