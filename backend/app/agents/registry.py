"""Agent registry service: CRUD, lifecycle, and immutable versioning.

All reads/writes are tenant-scoped by organization_id. Business logic lives here,
never in route handlers.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.errors import (
    AgentNotFound,
    AgentVersionNotFound,
    NoActiveAgentVersion,
)
from app.agents.lifecycle import ensure_transition
from app.core.config import settings
from app.core.exceptions import ConflictError, ValidationError
from app.core.utils import slugify, unique_suffix
from app.db.tenant import tenant_scoped
from app.models.agent import Agent, AgentVersion
from app.models.enums import AgentStatus, AgentType, MemoryMode

_VALID_TYPES = {t.value for t in AgentType}
_VALID_MEMORY_MODES = {m.value for m in MemoryMode}


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def validate_configuration(
    *,
    system_instructions: str | None = None,
    temperature: float | None = None,
    memory_mode: str | None = None,
    memory_configuration: dict[str, Any] | None = None,
    agent_type: str | None = None,
) -> None:
    if system_instructions is not None and len(system_instructions) > (
        settings.AGENT_MAX_SYSTEM_INSTRUCTIONS_CHARS
    ):
        raise ValidationError("system_instructions is too long.")
    if temperature is not None and not (0.0 <= temperature <= 2.0):
        raise ValidationError("temperature must be between 0.0 and 2.0.")
    if agent_type is not None and agent_type not in _VALID_TYPES:
        raise ValidationError(f"Unknown agent_type '{agent_type}'.")
    if memory_mode is not None and memory_mode not in _VALID_MEMORY_MODES:
        raise ValidationError(f"Unknown memory mode '{memory_mode}'.")
    if memory_configuration is not None:
        max_messages = memory_configuration.get("max_messages")
        if max_messages is not None:
            if not isinstance(max_messages, int) or max_messages < 1:
                raise ValidationError("memory max_messages must be a positive integer.")
            if max_messages > settings.AGENT_MEMORY_MAX_MESSAGES:
                raise ValidationError(
                    f"memory max_messages cannot exceed {settings.AGENT_MEMORY_MAX_MESSAGES}."
                )


# --------------------------------------------------------------------------- #
# Lookups
# --------------------------------------------------------------------------- #
async def get_agent(db: AsyncSession, organization_id: uuid.UUID, agent_id: uuid.UUID) -> Agent:
    agent = (
        await db.execute(
            tenant_scoped(select(Agent), Agent, organization_id).where(Agent.id == agent_id)
        )
    ).scalar_one_or_none()
    if agent is None:
        raise AgentNotFound("Agent not found.")
    return agent


async def list_agents(
    db: AsyncSession,
    organization_id: uuid.UUID,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Agent], int]:
    stmt = tenant_scoped(select(Agent), Agent, organization_id)
    count_stmt = tenant_scoped(select(func.count(Agent.id)), Agent, organization_id)
    if status:
        stmt = stmt.where(Agent.status == status)
        count_stmt = count_stmt.where(Agent.status == status)
    total = (await db.execute(count_stmt)).scalar_one()
    rows = (
        (await db.execute(stmt.order_by(Agent.created_at.desc()).limit(limit).offset(offset)))
        .scalars()
        .all()
    )
    return list(rows), int(total)


async def _unique_slug(db: AsyncSession, organization_id: uuid.UUID, name: str) -> str:
    base = slugify(name)
    slug = base
    while (
        await db.execute(
            tenant_scoped(select(Agent.id), Agent, organization_id).where(Agent.slug == slug)
        )
    ).first():
        slug = f"{base}-{unique_suffix()}"
    return slug


# --------------------------------------------------------------------------- #
# Create / update
# --------------------------------------------------------------------------- #
async def create_agent(
    db: AsyncSession,
    organization_id: uuid.UUID,
    created_by: uuid.UUID,
    *,
    name: str,
    description: str | None = None,
    agent_type: str = AgentType.GENERAL.value,
    default_provider: str | None = None,
    default_model: str | None = None,
    system_instructions: str = "",
    temperature: float = 0.7,
    max_tokens: int | None = None,
    memory_mode: str = MemoryMode.CONVERSATION.value,
    configuration: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Agent:
    validate_configuration(
        system_instructions=system_instructions,
        temperature=temperature,
        memory_mode=memory_mode,
        agent_type=agent_type,
    )
    slug = await _unique_slug(db, organization_id, name)
    agent = Agent(
        organization_id=organization_id,
        created_by=created_by,
        name=name,
        slug=slug,
        description=description,
        status=AgentStatus.DRAFT.value,
        agent_type=agent_type,
        default_provider=default_provider,
        default_model=default_model,
        system_instructions=system_instructions,
        temperature=temperature,
        max_tokens=max_tokens,
        memory_mode=memory_mode,
        configuration=configuration or {},
        agent_metadata=metadata or {},
    )
    db.add(agent)
    await db.flush()
    return agent


async def update_agent_draft(
    db: AsyncSession,
    organization_id: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    updates: dict[str, Any],
) -> Agent:
    """Update the agent's DRAFT/default configuration. Never mutates a version."""
    agent = await get_agent(db, organization_id, agent_id)
    if agent.status == AgentStatus.ARCHIVED.value:
        raise ConflictError("Archived agents cannot be modified.")
    validate_configuration(
        system_instructions=updates.get("system_instructions"),
        temperature=updates.get("temperature"),
        memory_mode=updates.get("memory_mode"),
        agent_type=updates.get("agent_type"),
    )
    allowed = {
        "name",
        "description",
        "agent_type",
        "default_provider",
        "default_model",
        "system_instructions",
        "temperature",
        "max_tokens",
        "memory_mode",
        "configuration",
    }
    for key, value in updates.items():
        if key in allowed and value is not None:
            setattr(agent, key, value)
    if updates.get("metadata") is not None:
        agent.agent_metadata = updates["metadata"]
    await db.flush()
    return agent


async def set_status(
    db: AsyncSession, organization_id: uuid.UUID, agent_id: uuid.UUID, target: str
) -> Agent:
    agent = await get_agent(db, organization_id, agent_id)
    ensure_transition(agent.status, target)
    if target == AgentStatus.ACTIVE.value and agent.active_version_id is None:
        raise NoActiveAgentVersion(
            "Agent has no active version. Create and activate a version first."
        )
    agent.status = target
    await db.flush()
    return agent


# --------------------------------------------------------------------------- #
# Versioning (immutable)
# --------------------------------------------------------------------------- #
async def create_version(
    db: AsyncSession,
    organization_id: uuid.UUID,
    agent_id: uuid.UUID,
    created_by: uuid.UUID,
    *,
    overrides: dict[str, Any] | None = None,
) -> AgentVersion:
    """Snapshot the agent's current draft config into a new immutable version."""
    agent = await get_agent(db, organization_id, agent_id)
    overrides = overrides or {}
    memory_configuration = overrides.get(
        "memory_configuration", {"mode": agent.memory_mode, "max_messages": 20}
    )
    validate_configuration(
        system_instructions=overrides.get("system_instructions", agent.system_instructions),
        temperature=overrides.get("temperature", agent.temperature),
        memory_configuration=memory_configuration,
    )
    next_number = (
        (
            await db.execute(
                select(func.coalesce(func.max(AgentVersion.version_number), 0)).where(
                    AgentVersion.agent_id == agent_id
                )
            )
        ).scalar_one()
    ) + 1
    version = AgentVersion(
        organization_id=organization_id,
        agent_id=agent_id,
        version_number=next_number,
        system_instructions=overrides.get("system_instructions", agent.system_instructions),
        provider=overrides.get("provider", agent.default_provider),
        model=overrides.get("model", agent.default_model),
        temperature=overrides.get("temperature", agent.temperature),
        max_tokens=overrides.get("max_tokens", agent.max_tokens),
        memory_configuration=memory_configuration,
        tool_configuration=overrides.get("tool_configuration", {}),
        configuration=overrides.get("configuration", agent.configuration),
        status=AgentStatus.DRAFT.value,
        created_by=created_by,
    )
    db.add(version)
    await db.flush()
    return version


async def list_versions(
    db: AsyncSession, organization_id: uuid.UUID, agent_id: uuid.UUID
) -> list[AgentVersion]:
    await get_agent(db, organization_id, agent_id)  # tenant + existence check
    rows = (
        (
            await db.execute(
                tenant_scoped(select(AgentVersion), AgentVersion, organization_id)
                .where(AgentVersion.agent_id == agent_id)
                .order_by(AgentVersion.version_number.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def get_version(
    db: AsyncSession, organization_id: uuid.UUID, agent_id: uuid.UUID, version_id: uuid.UUID
) -> AgentVersion:
    version = (
        await db.execute(
            tenant_scoped(select(AgentVersion), AgentVersion, organization_id).where(
                AgentVersion.id == version_id, AgentVersion.agent_id == agent_id
            )
        )
    ).scalar_one_or_none()
    if version is None:
        raise AgentVersionNotFound("Agent version not found.")
    return version


async def activate_version(
    db: AsyncSession, organization_id: uuid.UUID, agent_id: uuid.UUID, version_id: uuid.UUID
) -> Agent:
    """Make a version the agent's active production configuration.

    The previously active version is archived; the agent becomes ACTIVE (unless
    archived). Versions themselves are never edited — activation only flips status.
    """
    agent = await get_agent(db, organization_id, agent_id)
    if agent.status == AgentStatus.ARCHIVED.value:
        raise ConflictError("Archived agents cannot have versions activated.")
    version = await get_version(db, organization_id, agent_id, version_id)

    # Archive the currently-active version, if any and different.
    if agent.active_version_id and agent.active_version_id != version.id:
        prev = (
            await db.execute(select(AgentVersion).where(AgentVersion.id == agent.active_version_id))
        ).scalar_one_or_none()
        if prev is not None:
            prev.status = AgentStatus.ARCHIVED.value

    version.status = AgentStatus.ACTIVE.value
    agent.active_version_id = version.id
    agent.status = AgentStatus.ACTIVE.value
    await db.flush()
    return agent


async def resolve_runnable_version(
    db: AsyncSession,
    organization_id: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    version_id: uuid.UUID | None = None,
) -> tuple[Agent, AgentVersion]:
    """Resolve the version to execute: an explicit test version, else the active one."""
    agent = await get_agent(db, organization_id, agent_id)
    if version_id is not None:
        version = await get_version(db, organization_id, agent_id, version_id)
        return agent, version
    if agent.active_version_id is None:
        raise NoActiveAgentVersion("Agent has no active version to execute.")
    version = await get_version(db, organization_id, agent_id, agent.active_version_id)
    return agent, version
