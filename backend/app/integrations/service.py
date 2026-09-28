"""Integration connections, MCP tool import, and the run-bound gateway (M6).

Connections are managed by administrators (`integration:manage`). Agents and
workflows never see a connection's secret: tools ask the `IntegrationGateway`
(injected by the runtime, bound to one organization and run) to act, and the
gateway decrypts the secret only for the duration of the call.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar
from urllib.parse import urlsplit

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.db.tenant import tenant_scoped
from app.integrations import secrets
from app.integrations.net import IntegrationError
from app.integrations.providers import caldav, hubspot, mcp, smtp, webhook
from app.models.enums import (
    IntegrationProvider,
    IntegrationStatus,
    ToolPermissionMode,
    ToolRiskLevel,
)
from app.models.integration import IntegrationConnection, IntegrationTool
from app.models.tool import Tool
from app.services.audit_service import record_audit

T = TypeVar("T")
MCP_HANDLER = "mcp"


@dataclass(frozen=True)
class ProviderSpec:
    config: type[BaseModel]
    secret: type[BaseModel]
    test: Callable[[dict[str, Any], dict[str, Any]], Awaitable[None]]
    url_field: str | None = None


PROVIDERS: dict[str, ProviderSpec] = {
    IntegrationProvider.SMTP.value: ProviderSpec(smtp.SmtpConfig, smtp.SmtpSecret, smtp.test),
    IntegrationProvider.WEBHOOK.value: ProviderSpec(
        webhook.WebhookConfig, webhook.WebhookSecret, webhook.test, "url"
    ),
    IntegrationProvider.MCP.value: ProviderSpec(mcp.McpConfig, mcp.McpSecret, mcp.test, "url"),
    IntegrationProvider.CALDAV.value: ProviderSpec(
        caldav.CaldavConfig, caldav.CaldavSecret, caldav.test, "calendar_url"
    ),
    IntegrationProvider.HUBSPOT.value: ProviderSpec(
        hubspot.HubspotConfig, hubspot.HubspotSecret, hubspot.test
    ),
}


def _now() -> datetime:
    return datetime.now(UTC)


def _first_error(exc: PydanticValidationError) -> str:
    err = exc.errors()[0]
    where = ".".join(str(p) for p in err.get("loc", ()))
    return f"{where}: {err.get('msg', 'invalid')}" if where else str(err.get("msg"))


def _validated(provider: str, config: dict[str, Any], secret: dict[str, Any]) -> tuple[dict, dict]:
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise ValidationError(f"Unknown provider '{provider}'.")
    try:
        cfg = spec.config.model_validate(config).model_dump(mode="json", exclude_none=True)
        sec = spec.secret.model_validate(secret).model_dump(mode="json", exclude_none=True)
    except PydanticValidationError as exc:
        raise ValidationError(
            f"Invalid {provider.lower()} settings ({_first_error(exc)})."
        ) from exc
    if spec.url_field:
        parts = urlsplit(str(cfg[spec.url_field]))
        allowed = {"https", "http"} if settings.INTEGRATIONS_ALLOW_PRIVATE_NETWORKS else {"https"}
        if parts.scheme not in allowed or not parts.hostname:
            raise ValidationError("The URL must be an https:// address.")
        if parts.username or parts.password:
            raise ValidationError("Put credentials in the secret, not in the URL.")
    return cfg, sec


def secret_hints(connection: IntegrationConnection) -> dict[str, str]:
    """Which secret fields are set, masked. Never the values."""
    return {
        k: secrets.mask(str(v)) for k, v in secrets.decrypt(connection.secret_ciphertext).items()
    }


# --------------------------------------------------------------------------- #
# Connections
# --------------------------------------------------------------------------- #
async def list_connections(
    db: AsyncSession, organization_id: uuid.UUID
) -> list[IntegrationConnection]:
    rows = await db.execute(
        tenant_scoped(
            select(IntegrationConnection), IntegrationConnection, organization_id
        ).order_by(IntegrationConnection.provider, IntegrationConnection.name)
    )
    return list(rows.scalars())


async def get_connection(
    db: AsyncSession, organization_id: uuid.UUID, connection_id: uuid.UUID
) -> IntegrationConnection:
    connection = (
        await db.execute(
            tenant_scoped(
                select(IntegrationConnection), IntegrationConnection, organization_id
            ).where(IntegrationConnection.id == connection_id)
        )
    ).scalar_one_or_none()
    if connection is None:
        raise NotFoundError("Integration connection not found.")
    return connection


async def create_connection(
    db: AsyncSession,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    provider: str,
    name: str,
    config: dict[str, Any],
    secret: dict[str, Any],
) -> IntegrationConnection:
    cfg, sec = _validated(provider, config, secret)
    name = name.strip()
    clash = (
        await db.execute(
            select(IntegrationConnection.id).where(
                IntegrationConnection.organization_id == organization_id,
                IntegrationConnection.name == name,
            )
        )
    ).scalar_one_or_none()
    if clash is not None:
        raise ConflictError(f"A connection named '{name}' already exists.")
    connection = IntegrationConnection(
        organization_id=organization_id,
        provider=provider,
        name=name,
        config=cfg,
        secret_ciphertext=secrets.encrypt(sec) if sec else None,
        created_by=user_id,
    )
    db.add(connection)
    await db.flush()
    return connection


async def update_connection(
    db: AsyncSession,
    connection: IntegrationConnection,
    *,
    name: str | None = None,
    status: str | None = None,
    config: dict[str, Any] | None = None,
    secret: dict[str, Any] | None = None,
) -> IntegrationConnection:
    """Secret keys in `secret` replace stored ones; a key set to null removes it."""
    merged = secrets.decrypt(connection.secret_ciphertext)
    if secret is not None:
        for key, value in secret.items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
    cfg, sec = _validated(
        connection.provider, config if config is not None else connection.config, merged
    )
    connection.config = cfg
    connection.secret_ciphertext = secrets.encrypt(sec) if sec else None
    if name is not None:
        connection.name = name.strip()
    if status is not None:
        connection.status = status
    connection.last_error = None
    await db.flush()
    return connection


async def delete_connection(db: AsyncSession, connection: IntegrationConnection) -> None:
    tool_ids = (
        (
            await db.execute(
                select(IntegrationTool.tool_id).where(
                    IntegrationTool.connection_id == connection.id
                )
            )
        )
        .scalars()
        .all()
    )
    for tool in (await db.execute(select(Tool).where(Tool.id.in_(tool_ids)))).scalars():
        await db.delete(tool)
    await db.delete(connection)
    await db.flush()


async def test_connection(db: AsyncSession, connection: IntegrationConnection) -> None:
    spec = PROVIDERS[connection.provider]
    try:
        await spec.test(connection.config, secrets.decrypt(connection.secret_ciphertext))
    except IntegrationError as exc:
        connection.last_error = exc.message[:500]
        await db.flush()
        raise
    connection.last_error = None
    connection.last_used_at = _now()
    await db.flush()


# --------------------------------------------------------------------------- #
# MCP tools
# --------------------------------------------------------------------------- #
def _tool_name(connection: IntegrationConnection, remote: str, taken: set[str]) -> str:
    """A provider-safe (^[A-Za-z0-9_-]{1,64}$), unique catalogue name."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", remote)
    base = f"mcp_{connection.id.hex[:8]}_{safe}"[:64]
    name, n = base, 2
    while name in taken:  # e.g. "a.b" and "a_b" both sanitise to "a_b"
        suffix = f"_{n}"
        name, n = base[: 64 - len(suffix)] + suffix, n + 1
    taken.add(name)
    return name


async def list_mcp_tools(
    db: AsyncSession, connection: IntegrationConnection
) -> list[tuple[IntegrationTool, Tool]]:
    rows = await db.execute(
        select(IntegrationTool, Tool)
        .join(Tool, Tool.id == IntegrationTool.tool_id)
        .where(IntegrationTool.connection_id == connection.id)
        .order_by(IntegrationTool.remote_name)
    )
    return [(it, t) for it, t in rows.tuples().all()]


async def sync_mcp_tools(
    db: AsyncSession, connection: IntegrationConnection
) -> list[tuple[IntegrationTool, Tool]]:
    """Discover the server's tools. New tools arrive disabled, HIGH risk,
    approval-required: an administrator decides before any agent can use them."""
    if connection.provider != IntegrationProvider.MCP.value:
        raise ValidationError("Only MCP connections have tools to sync.")
    remote = await mcp.list_tools(connection.config, secrets.decrypt(connection.secret_ciphertext))
    existing = {it.remote_name: (it, t) for it, t in await list_mcp_tools(db, connection)}
    taken = {t.name for _, t in existing.values()}
    seen: set[str] = set()
    for item in remote:
        remote_name = str(item["name"])[:128]
        schema = item.get("inputSchema")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            schema = {"type": "object", "properties": {}}
        if len(json.dumps(schema, default=str)) > 20_000:
            continue  # refuse oversized schemas rather than truncate them
        seen.add(remote_name)
        description = f"[{connection.name} via MCP] {str(item.get('description') or '')}"[:2000]
        if remote_name in existing:
            integration_tool, tool = existing[remote_name]
            tool.description, tool.input_schema = description, schema
            integration_tool.available = True
            continue
        tool = Tool(
            name=_tool_name(connection, remote_name, taken),
            description=description,
            input_schema=schema,
            output_schema={"type": "object"},
            tool_type="mcp",
            permission_mode=ToolPermissionMode.APPROVAL_REQUIRED.value,
            enabled=False,
            handler_identifier=MCP_HANDLER,
            organization_id=connection.organization_id,
        )
        db.add(tool)
        await db.flush()
        db.add(
            IntegrationTool(
                organization_id=connection.organization_id,
                connection_id=connection.id,
                tool_id=tool.id,
                remote_name=remote_name,
                risk_level=ToolRiskLevel.HIGH.value,
            )
        )
    for remote_name, (integration_tool, tool) in existing.items():
        if remote_name not in seen:
            integration_tool.available = False
            tool.enabled = False
    connection.last_used_at, connection.last_error = _now(), None
    await db.flush()
    return await list_mcp_tools(db, connection)


async def update_mcp_tool(
    db: AsyncSession,
    connection: IntegrationConnection,
    integration_tool_id: uuid.UUID,
    *,
    enabled: bool | None,
    risk_level: str | None,
    permission_mode: str | None,
) -> tuple[IntegrationTool, Tool]:
    for integration_tool, tool in await list_mcp_tools(db, connection):
        if integration_tool.id != integration_tool_id:
            continue
        if risk_level is not None:
            integration_tool.risk_level = risk_level
        if permission_mode is not None:
            tool.permission_mode = permission_mode
        if enabled is not None:
            if enabled and not integration_tool.available:
                raise ConflictError("The MCP server no longer offers this tool.")
            tool.enabled = enabled
        await db.flush()
        return integration_tool, tool
    raise NotFoundError("MCP tool not found on this connection.")


# --------------------------------------------------------------------------- #
# Gateway (injected into tool contexts)
# --------------------------------------------------------------------------- #
@dataclass
class IntegrationGateway:
    """Run-bound access to the organization's connections for tool handlers."""

    db: AsyncSession
    organization_id: uuid.UUID
    user_id: uuid.UUID | None
    agent_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None

    async def _connection(self, provider: str, name: str | None) -> IntegrationConnection:
        stmt = select(IntegrationConnection).where(
            IntegrationConnection.organization_id == self.organization_id,
            IntegrationConnection.provider == provider,
            IntegrationConnection.status == IntegrationStatus.ACTIVE.value,
        )
        if name:
            stmt = stmt.where(IntegrationConnection.name == name)
        found = list((await self.db.execute(stmt)).scalars())
        label = provider.lower()
        if not found:
            raise IntegrationError(
                f"No active {label} connection"
                + (f" named '{name}'." if name else " is set up for this organization.")
            )
        if len(found) > 1:
            names = ", ".join(sorted(c.name for c in found))
            raise IntegrationError(f"Several {label} connections exist ({names}); name one.")
        return found[0]

    async def use(
        self,
        provider: str,
        name: str | None,
        action: str,
        call: Callable[[dict[str, Any], dict[str, Any]], Awaitable[T]],
        *,
        audit: bool = True,
    ) -> T:
        connection = await self._connection(provider, name)
        try:
            result = await call(connection.config, secrets.decrypt(connection.secret_ciphertext))
        except IntegrationError as exc:
            connection.last_error = exc.message[:500]
            await self.db.flush()
            raise
        connection.last_used_at, connection.last_error = _now(), None
        if audit:
            await record_audit(
                self.db,
                action="integration.used",
                user_id=self.user_id,
                organization_id=self.organization_id,
                target_type="integration_connection",
                target_id=str(connection.id),
                metadata={
                    "provider": provider,
                    "action": action,
                    "agent_id": str(self.agent_id) if self.agent_id else None,
                    "run_id": str(self.run_id) if self.run_id else None,
                },
            )
        await self.db.flush()
        return result

    async def call_mcp(
        self, integration_tool_id: uuid.UUID, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        row = (
            await self.db.execute(
                select(IntegrationTool, IntegrationConnection)
                .join(
                    IntegrationConnection,
                    IntegrationConnection.id == IntegrationTool.connection_id,
                )
                .where(
                    IntegrationTool.id == integration_tool_id,
                    IntegrationTool.organization_id == self.organization_id,
                )
            )
        ).first()
        if row is None:
            raise IntegrationError("This MCP tool no longer exists.")
        integration_tool, connection = row
        if connection.status != IntegrationStatus.ACTIVE.value or not integration_tool.available:
            raise IntegrationError("This MCP tool is not available right now.")
        remote = integration_tool.remote_name
        return await self.use(
            IntegrationProvider.MCP.value,
            connection.name,
            f"tools/call:{remote}",
            lambda cfg, sec: mcp.call_tool(cfg, sec, remote, arguments),
        )


async def mcp_tool_row(db: AsyncSession, tool: Tool) -> IntegrationTool | None:
    return (
        await db.execute(select(IntegrationTool).where(IntegrationTool.tool_id == tool.id))
    ).scalar_one_or_none()
