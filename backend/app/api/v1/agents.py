"""Agent API: CRUD, lifecycle, versioning, and controlled execution."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import conversations as conversation_service
from app.agents import registry
from app.agents.runtime import AgentRuntime, get_agent_runtime
from app.api.deps import TenantContext, require_permission
from app.db.session import get_db
from app.models.enums import AgentStatus
from app.rbac.permissions import Permission
from app.schemas.agent import (
    AgentCreate,
    AgentListOut,
    AgentOut,
    AgentUpdate,
    AgentVersionCreate,
    AgentVersionOut,
    ExecuteRequest,
    ExecutionResponse,
)

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post("", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: AgentCreate,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_CREATE)),
    db: AsyncSession = Depends(get_db),
) -> AgentOut:
    agent = await registry.create_agent(
        db,
        ctx.organization_id,
        ctx.user.id,
        name=body.name,
        description=body.description,
        agent_type=body.agent_type,
        default_provider=body.default_provider,
        default_model=body.default_model,
        system_instructions=body.system_instructions,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        memory_mode=body.memory_mode,
        configuration=body.configuration,
        metadata=body.metadata,
    )
    await db.commit()
    await db.refresh(agent)
    return AgentOut.model_validate(agent)


@router.get("", response_model=AgentListOut)
async def list_agents(
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_VIEW)),
    db: AsyncSession = Depends(get_db),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AgentListOut:
    agents, total = await registry.list_agents(
        db, ctx.organization_id, status=status_filter, limit=limit, offset=offset
    )
    return AgentListOut(items=[AgentOut.model_validate(a) for a in agents], total=total)


@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(
    agent_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> AgentOut:
    agent = await registry.get_agent(db, ctx.organization_id, agent_id)
    return AgentOut.model_validate(agent)


@router.patch("/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: uuid.UUID,
    body: AgentUpdate,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_UPDATE)),
    db: AsyncSession = Depends(get_db),
) -> AgentOut:
    agent = await registry.update_agent_draft(
        db, ctx.organization_id, agent_id, updates=body.model_dump(exclude_unset=True)
    )
    await db.commit()
    await db.refresh(agent)
    return AgentOut.model_validate(agent)


@router.delete("/{agent_id}", response_model=AgentOut)
async def archive_agent(
    agent_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_DELETE)),
    db: AsyncSession = Depends(get_db),
) -> AgentOut:
    """Soft-delete: archive the agent (history is preserved, never hard-deleted)."""
    agent = await registry.set_status(db, ctx.organization_id, agent_id, AgentStatus.ARCHIVED.value)
    await db.commit()
    await db.refresh(agent)
    return AgentOut.model_validate(agent)


# ---- lifecycle ----------------------------------------------------------- #
@router.post("/{agent_id}/activate", response_model=AgentOut)
async def activate_agent(
    agent_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_UPDATE)),
    db: AsyncSession = Depends(get_db),
) -> AgentOut:
    agent = await registry.set_status(db, ctx.organization_id, agent_id, AgentStatus.ACTIVE.value)
    await db.commit()
    await db.refresh(agent)
    return AgentOut.model_validate(agent)


@router.post("/{agent_id}/pause", response_model=AgentOut)
async def pause_agent(
    agent_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_UPDATE)),
    db: AsyncSession = Depends(get_db),
) -> AgentOut:
    agent = await registry.set_status(db, ctx.organization_id, agent_id, AgentStatus.PAUSED.value)
    await db.commit()
    await db.refresh(agent)
    return AgentOut.model_validate(agent)


@router.post("/{agent_id}/archive", response_model=AgentOut)
async def archive_agent_action(
    agent_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_UPDATE)),
    db: AsyncSession = Depends(get_db),
) -> AgentOut:
    agent = await registry.set_status(db, ctx.organization_id, agent_id, AgentStatus.ARCHIVED.value)
    await db.commit()
    await db.refresh(agent)
    return AgentOut.model_validate(agent)


# ---- versions ------------------------------------------------------------ #
@router.post("/{agent_id}/versions", response_model=AgentVersionOut, status_code=201)
async def create_version(
    agent_id: uuid.UUID,
    body: AgentVersionCreate,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_MANAGE_VERSIONS)),
    db: AsyncSession = Depends(get_db),
) -> AgentVersionOut:
    version = await registry.create_version(
        db,
        ctx.organization_id,
        agent_id,
        ctx.user.id,
        overrides=body.model_dump(exclude_unset=True),
    )
    await db.commit()
    await db.refresh(version)
    return AgentVersionOut.model_validate(version)


@router.get("/{agent_id}/versions", response_model=list[AgentVersionOut])
async def list_versions(
    agent_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> list[AgentVersionOut]:
    versions = await registry.list_versions(db, ctx.organization_id, agent_id)
    return [AgentVersionOut.model_validate(v) for v in versions]


@router.get("/{agent_id}/versions/{version_id}", response_model=AgentVersionOut)
async def get_version(
    agent_id: uuid.UUID,
    version_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> AgentVersionOut:
    version = await registry.get_version(db, ctx.organization_id, agent_id, version_id)
    return AgentVersionOut.model_validate(version)


@router.post("/{agent_id}/versions/{version_id}/activate", response_model=AgentOut)
async def activate_version(
    agent_id: uuid.UUID,
    version_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_MANAGE_VERSIONS)),
    db: AsyncSession = Depends(get_db),
) -> AgentOut:
    agent = await registry.activate_version(db, ctx.organization_id, agent_id, version_id)
    await db.commit()
    await db.refresh(agent)
    return AgentOut.model_validate(agent)


# ---- execution ----------------------------------------------------------- #
@router.post("/{agent_id}/execute", response_model=ExecutionResponse)
async def execute_agent(
    agent_id: uuid.UUID,
    body: ExecuteRequest,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_RUN)),
    runtime: AgentRuntime = Depends(get_agent_runtime),
    db: AsyncSession = Depends(get_db),
) -> ExecutionResponse:
    if body.conversation_id is None:
        conversation = await conversation_service.create_conversation(
            db, ctx.organization_id, ctx.user.id, agent_id=agent_id
        )
        conversation_id = conversation.id
    else:
        conversation_id = body.conversation_id

    result = await runtime.execute(
        db,
        organization_id=ctx.organization_id,
        user_id=ctx.user.id,
        agent_id=agent_id,
        conversation_id=conversation_id,
        input_message=body.message,
        version_id=body.version_id,
    )
    await db.commit()
    return ExecutionResponse(
        status=result.status,
        conversation_id=result.conversation_id,
        agent_id=result.agent_id,
        agent_version_id=result.agent_version_id,
        message=result.message,
        approval_id=result.approval_id,
        tool_name=result.tool_name,
        usage=result.usage,
    )
