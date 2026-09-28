"""Integration connections and imported MCP tools (Noblen AI 3.0, M6).

Secrets are accepted on create/update and never returned: responses show only
which secret fields are set, masked.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantContext, require_permission
from app.db.session import get_db
from app.integrations import service
from app.integrations.net import IntegrationError
from app.models.integration import IntegrationConnection, IntegrationTool
from app.models.tool import Tool
from app.rbac.permissions import Permission
from app.schemas.integration import (
    ConnectionCreate,
    ConnectionOut,
    ConnectionTestOut,
    ConnectionUpdate,
    McpToolOut,
    McpToolUpdate,
)
from app.services.audit_service import record_audit

router = APIRouter(prefix="/integrations", tags=["integrations"])


def _out(connection: IntegrationConnection) -> ConnectionOut:
    out = ConnectionOut.model_validate(connection)
    out.secret_fields = service.secret_hints(connection)
    return out


def _tool_out(integration_tool: IntegrationTool, tool: Tool) -> McpToolOut:
    return McpToolOut(
        id=integration_tool.id,
        tool_id=tool.id,
        name=tool.name,
        remote_name=integration_tool.remote_name,
        description=tool.description,
        input_schema=tool.input_schema,
        risk_level=integration_tool.risk_level,
        permission_mode=tool.permission_mode,
        enabled=tool.enabled,
        available=integration_tool.available,
    )


async def _audit(
    db: AsyncSession, ctx: TenantContext, action: str, connection_id: uuid.UUID, **meta
) -> None:
    await record_audit(
        db,
        action=action,
        user_id=ctx.user.id,
        organization_id=ctx.organization_id,
        target_type="integration_connection",
        target_id=str(connection_id),
        metadata=meta or None,
    )


@router.get("", response_model=list[ConnectionOut])
async def list_connections(
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> list[ConnectionOut]:
    return [_out(c) for c in await service.list_connections(db, ctx.organization_id)]


@router.post("", response_model=ConnectionOut, status_code=status.HTTP_201_CREATED)
async def create_connection(
    body: ConnectionCreate,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> ConnectionOut:
    connection = await service.create_connection(
        db,
        ctx.organization_id,
        ctx.user.id,
        provider=body.provider.value,
        name=body.name,
        config=body.config,
        secret=body.secret,
    )
    await _audit(db, ctx, "integration.created", connection.id, provider=connection.provider)
    await db.commit()
    await db.refresh(connection)
    return _out(connection)


@router.get("/{connection_id}", response_model=ConnectionOut)
async def get_connection(
    connection_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> ConnectionOut:
    return _out(await service.get_connection(db, ctx.organization_id, connection_id))


@router.patch("/{connection_id}", response_model=ConnectionOut)
async def update_connection(
    connection_id: uuid.UUID,
    body: ConnectionUpdate,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> ConnectionOut:
    connection = await service.get_connection(db, ctx.organization_id, connection_id)
    await service.update_connection(
        db,
        connection,
        name=body.name,
        status=body.status.value if body.status else None,
        config=body.config,
        secret=body.secret,
    )
    # Which fields changed, never their values.
    changed = sorted(k for k, v in body.model_dump(exclude_unset=True).items() if v is not None)
    await _audit(db, ctx, "integration.updated", connection.id, fields=changed)
    await db.commit()
    await db.refresh(connection)
    return _out(connection)


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    connection = await service.get_connection(db, ctx.organization_id, connection_id)
    await _audit(db, ctx, "integration.deleted", connection.id, provider=connection.provider)
    await service.delete_connection(db, connection)
    await db.commit()


@router.post("/{connection_id}/test", response_model=ConnectionTestOut)
async def test_connection(
    connection_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> ConnectionTestOut:
    connection = await service.get_connection(db, ctx.organization_id, connection_id)
    try:
        await service.test_connection(db, connection)
        result = ConnectionTestOut(ok=True)
    except IntegrationError as exc:
        result = ConnectionTestOut(ok=False, error=exc.message)
    await db.commit()
    return result


@router.get("/{connection_id}/tools", response_model=list[McpToolOut])
async def list_mcp_tools(
    connection_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> list[McpToolOut]:
    connection = await service.get_connection(db, ctx.organization_id, connection_id)
    return [_tool_out(it, t) for it, t in await service.list_mcp_tools(db, connection)]


@router.post("/{connection_id}/tools/sync", response_model=list[McpToolOut])
async def sync_mcp_tools(
    connection_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> list[McpToolOut]:
    """Discover the MCP server's tools. New tools arrive disabled and HIGH risk."""
    connection = await service.get_connection(db, ctx.organization_id, connection_id)
    tools = await service.sync_mcp_tools(db, connection)
    await _audit(db, ctx, "integration.mcp_synced", connection.id, tools=len(tools))
    await db.commit()
    return [_tool_out(it, t) for it, t in tools]


@router.patch("/{connection_id}/tools/{integration_tool_id}", response_model=McpToolOut)
async def update_mcp_tool(
    connection_id: uuid.UUID,
    integration_tool_id: uuid.UUID,
    body: McpToolUpdate,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> McpToolOut:
    """Enable an imported tool and declare its risk level and approval policy."""
    connection = await service.get_connection(db, ctx.organization_id, connection_id)
    integration_tool, tool = await service.update_mcp_tool(
        db,
        connection,
        integration_tool_id,
        enabled=body.enabled,
        risk_level=body.risk_level.value if body.risk_level else None,
        permission_mode=body.permission_mode.value if body.permission_mode else None,
    )
    await _audit(
        db,
        ctx,
        "integration.mcp_tool_updated",
        connection.id,
        tool=tool.name,
        enabled=tool.enabled,
        risk_level=integration_tool.risk_level,
        permission_mode=tool.permission_mode,
    )
    await db.commit()
    return _tool_out(integration_tool, tool)
