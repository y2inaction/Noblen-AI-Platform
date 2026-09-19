"""Tool catalogue + per-agent tool binding API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import registry as agent_registry
from app.agents.tools.registry import tool_registry
from app.api.deps import TenantContext, require_permission
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.db.session import get_db
from app.models.enums import ToolPermissionMode
from app.models.tool import AgentTool, Tool
from app.rbac.permissions import Permission
from app.schemas.tool import AgentToolAttach, AgentToolOut, ToolListOut, ToolOut

router = APIRouter(tags=["tools"])

_VALID_MODES = {m.value for m in ToolPermissionMode}


@router.get("/tools", response_model=ToolListOut)
async def list_tools(
    _ctx: TenantContext = Depends(require_permission(Permission.TOOL_VIEW)),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ToolListOut:
    total = (await db.execute(select(func.count(Tool.id)))).scalar_one()
    rows = (
        (await db.execute(select(Tool).order_by(Tool.name).limit(limit).offset(offset)))
        .scalars()
        .all()
    )
    return ToolListOut(items=[ToolOut.model_validate(t) for t in rows], total=int(total))


@router.get("/tools/{tool_id}", response_model=ToolOut)
async def get_tool(
    tool_id: uuid.UUID,
    _ctx: TenantContext = Depends(require_permission(Permission.TOOL_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> ToolOut:
    tool = (await db.execute(select(Tool).where(Tool.id == tool_id))).scalar_one_or_none()
    if tool is None:
        raise NotFoundError("Tool not found.")
    return ToolOut.model_validate(tool)


@router.get("/agents/{agent_id}/tools", response_model=list[AgentToolOut])
async def list_agent_tools(
    agent_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> list[AgentToolOut]:
    await agent_registry.get_agent(db, ctx.organization_id, agent_id)  # tenant check
    rows = (
        (await db.execute(select(AgentTool).where(AgentTool.agent_id == agent_id))).scalars().all()
    )
    return [AgentToolOut.model_validate(r) for r in rows]


@router.post("/agents/{agent_id}/tools", response_model=AgentToolOut, status_code=201)
async def attach_agent_tool(
    agent_id: uuid.UUID,
    body: AgentToolAttach,
    ctx: TenantContext = Depends(require_permission(Permission.TOOL_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> AgentToolOut:
    await agent_registry.get_agent(db, ctx.organization_id, agent_id)  # tenant check
    tool = (await db.execute(select(Tool).where(Tool.id == body.tool_id))).scalar_one_or_none()
    if tool is None:
        raise NotFoundError("Tool not found.")
    if tool_registry.get_by_identifier(tool.handler_identifier) is None:
        raise ValidationError("Tool has no registered handler and cannot be attached.")
    if body.permission_mode is not None and body.permission_mode not in _VALID_MODES:
        raise ValidationError(f"Unknown permission_mode '{body.permission_mode}'.")

    existing = (
        await db.execute(
            select(AgentTool).where(
                AgentTool.agent_id == agent_id, AgentTool.tool_id == body.tool_id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("Tool is already attached to this agent.")

    binding = AgentTool(
        agent_id=agent_id,
        tool_id=body.tool_id,
        enabled=body.enabled,
        permission_mode=body.permission_mode,
    )
    db.add(binding)
    await db.commit()
    await db.refresh(binding)
    return AgentToolOut.model_validate(binding)


@router.delete("/agents/{agent_id}/tools/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def detach_agent_tool(
    agent_id: uuid.UUID,
    tool_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TOOL_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    await agent_registry.get_agent(db, ctx.organization_id, agent_id)  # tenant check
    binding = (
        await db.execute(
            select(AgentTool).where(AgentTool.agent_id == agent_id, AgentTool.tool_id == tool_id)
        )
    ).scalar_one_or_none()
    if binding is None:
        raise NotFoundError("Tool is not attached to this agent.")
    await db.delete(binding)
    await db.commit()
