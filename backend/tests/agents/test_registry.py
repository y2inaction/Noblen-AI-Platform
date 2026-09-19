"""Agent registry + lifecycle + versioning (service-level, tenant-scoped)."""

import uuid

import pytest

from app.agents import registry
from app.agents.errors import (
    AgentNotFound,
    InvalidAgentStateTransition,
    NoActiveAgentVersion,
)
from app.agents.lifecycle import can_transition
from app.core.exceptions import ValidationError
from app.models.enums import AgentStatus


async def _make_org(db_session):
    from app.models.organization import Organization

    org = Organization(name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}")
    db_session.add(org)
    await db_session.flush()
    return org.id, uuid.uuid4()


@pytest.mark.asyncio
async def test_create_and_get_agent(db_session):
    org_id, user_id = await _make_org(db_session)
    agent = await registry.create_agent(db_session, org_id, user_id, name="Sales Bot")
    assert agent.status == AgentStatus.DRAFT.value
    assert agent.slug == "sales-bot"
    fetched = await registry.get_agent(db_session, org_id, agent.id)
    assert fetched.id == agent.id


@pytest.mark.asyncio
async def test_slug_uniqueness_within_org(db_session):
    org_id, user_id = await _make_org(db_session)
    a1 = await registry.create_agent(db_session, org_id, user_id, name="Bot")
    a2 = await registry.create_agent(db_session, org_id, user_id, name="Bot")
    assert a1.slug != a2.slug


@pytest.mark.asyncio
async def test_get_agent_tenant_scoped(db_session):
    org_a, user = await _make_org(db_session)
    org_b, _ = await _make_org(db_session)
    agent = await registry.create_agent(db_session, org_a, user, name="Bot")
    with pytest.raises(AgentNotFound):
        await registry.get_agent(db_session, org_b, agent.id)


@pytest.mark.asyncio
async def test_versioning_increments_and_snapshots(db_session):
    org_id, user_id = await _make_org(db_session)
    agent = await registry.create_agent(
        db_session, org_id, user_id, name="Bot", system_instructions="v1 prompt"
    )
    v1 = await registry.create_version(db_session, org_id, agent.id, user_id)
    assert v1.version_number == 1
    assert v1.system_instructions == "v1 prompt"

    # Change the draft, cut v2 — v1 must remain immutable (unchanged).
    await registry.update_agent_draft(
        db_session, org_id, agent.id, updates={"system_instructions": "v2 prompt"}
    )
    v2 = await registry.create_version(db_session, org_id, agent.id, user_id)
    assert v2.version_number == 2
    assert v2.system_instructions == "v2 prompt"

    v1_again = await registry.get_version(db_session, org_id, agent.id, v1.id)
    assert v1_again.system_instructions == "v1 prompt"  # unchanged


@pytest.mark.asyncio
async def test_activate_version_sets_active_and_status(db_session):
    org_id, user_id = await _make_org(db_session)
    agent = await registry.create_agent(db_session, org_id, user_id, name="Bot")
    v1 = await registry.create_version(db_session, org_id, agent.id, user_id)
    updated = await registry.activate_version(db_session, org_id, agent.id, v1.id)
    assert updated.active_version_id == v1.id
    assert updated.status == AgentStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_cannot_activate_agent_without_version(db_session):
    org_id, user_id = await _make_org(db_session)
    agent = await registry.create_agent(db_session, org_id, user_id, name="Bot")
    # DRAFT -> TESTING is allowed; TESTING -> ACTIVE requires an active version.
    await registry.set_status(db_session, org_id, agent.id, AgentStatus.TESTING.value)
    with pytest.raises(NoActiveAgentVersion):
        await registry.set_status(db_session, org_id, agent.id, AgentStatus.ACTIVE.value)


@pytest.mark.asyncio
async def test_invalid_state_transition_rejected(db_session):
    org_id, user_id = await _make_org(db_session)
    agent = await registry.create_agent(db_session, org_id, user_id, name="Bot")
    # DRAFT -> PAUSED is not allowed.
    with pytest.raises(InvalidAgentStateTransition):
        await registry.set_status(db_session, org_id, agent.id, AgentStatus.PAUSED.value)


@pytest.mark.asyncio
async def test_resolve_runnable_requires_active_version(db_session):
    org_id, user_id = await _make_org(db_session)
    agent = await registry.create_agent(db_session, org_id, user_id, name="Bot")
    with pytest.raises(NoActiveAgentVersion):
        await registry.resolve_runnable_version(db_session, org_id, agent.id)


@pytest.mark.asyncio
async def test_memory_config_validation(db_session):
    org_id, user_id = await _make_org(db_session)
    agent = await registry.create_agent(db_session, org_id, user_id, name="Bot")
    with pytest.raises(ValidationError):
        await registry.create_version(
            db_session,
            org_id,
            agent.id,
            user_id,
            overrides={"memory_configuration": {"mode": "CONVERSATION", "max_messages": 100000}},
        )


def test_lifecycle_transition_table():
    assert can_transition("DRAFT", "TESTING")
    assert not can_transition("DRAFT", "ACTIVE")
    assert can_transition("ACTIVE", "PAUSED")
    assert can_transition("PAUSED", "ACTIVE")
    assert not can_transition("ARCHIVED", "ACTIVE")
