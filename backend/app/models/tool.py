"""Tool registry models.

`Tool` is the catalogue row (schema + default permission mode + handler id). Tools
are platform-global (organization_id NULL) by default. Since M6 an organization
can own tools too: those imported from its MCP servers, visible only to it.
`AgentTool` binds a tool to a specific agent with a per-agent permission-mode
override.
"""

from __future__ import annotations

import uuid

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.models.enums import ToolPermissionMode


class Tool(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "tools"

    name: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    version: Mapped[str] = mapped_column(String(16), default="1", nullable=False)
    input_schema: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    output_schema: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # "system" | "communication" | "crm" | ... (classification only in Phase 3)
    tool_type: Mapped[str] = mapped_column(String(32), default="system", nullable=False)
    # Default policy; an AgentTool may override per agent.
    permission_mode: Mapped[str] = mapped_column(
        String(24), default=ToolPermissionMode.AUTO.value, nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Maps to a registered handler in the in-code tool registry.
    handler_identifier: Mapped[str] = mapped_column(String(64), nullable=False)
    # Owning organization for tenant-specific tools (M6, e.g. MCP); NULL = global.
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )


class AgentTool(UUIDMixin, TimestampMixin, Base):
    """Binds a tool to an agent, with an optional per-agent permission override."""

    __tablename__ = "agent_tools"
    __table_args__ = (UniqueConstraint("agent_id", "tool_id", name="uq_agent_tool"),)

    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tool_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Null => inherit the tool's default permission_mode.
    permission_mode: Mapped[str | None] = mapped_column(String(24), nullable=True)
