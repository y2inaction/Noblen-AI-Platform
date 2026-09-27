"""Noblen AI 3.0 (M4): long-term memory — scopes, write paths, privacy, retention.

Deterministic: the model is scripted; tools, services, runtime and API are real.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select, update

from app.agents.runtime import AgentRuntime, get_agent_runtime
from app.agents.tools.seed import seed_builtin_tools
from app.agents.worker import purge_expired_memories
from app.ai.gateway import AIGateway
from app.core.config import settings
from app.main import app
from app.models.membership import OrganizationMember
from app.models.memory import Memory
from tests.agents.test_controlled_autonomy import ScriptedProvider, calls, text
from tests.conftest import auth_headers, register_org

_MEMORY_HEADER = "## Long-term memory"


@pytest.fixture
def provider() -> ScriptedProvider:
    return ScriptedProvider()


@pytest_asyncio.fixture
async def mem(client, session_factory, provider):
    async with session_factory() as session:
        await seed_builtin_tools(session)
        await session.commit()
    gateway = AIGateway(
        providers={"scripted": provider},
        default_provider="scripted",
        default_model="scripted-1",
        max_retries=0,
    )
    runtime = AgentRuntime(gateway=gateway)
    app.dependency_overrides[get_agent_runtime] = lambda: runtime
    yield
    app.dependency_overrides.pop(get_agent_runtime, None)


async def _member(client, session_factory, org_id: str, email: str, role: str) -> dict:
    auth = await register_org(client, email, f"Home of {email}")
    async with session_factory() as s:
        s.add(
            OrganizationMember(
                user_id=uuid.UUID(auth["user"]["id"]),
                organization_id=uuid.UUID(org_id),
                role_name=role,
                status="ACTIVE",
            )
        )
        await s.commit()
    auth["_org"] = org_id
    return auth


def _h(auth: dict) -> dict[str, str]:
    headers = auth_headers(auth)
    if auth.get("_org"):
        headers["X-Organization-Id"] = auth["_org"]
    return headers


async def _executive(client, auth) -> str:
    resp = await client.post("/api/v1/agent-templates/executive-ai/instantiate", headers=_h(auth))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _execute(client, auth, agent_id, message="go") -> dict:
    resp = await client.post(
        f"/api/v1/agents/{agent_id}/execute", headers=_h(auth), json={"message": message}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _system(provider: ScriptedProvider, index: int = -1) -> str:
    return provider.requests[index].system or ""


def _tool_result(provider: ScriptedProvider, index: int = -1) -> str:
    return next(m for m in provider.requests[index].messages if m.role == "tool").content


async def _memories(session_factory) -> list[Memory]:
    async with session_factory() as s:
        return list((await s.execute(select(Memory).order_by(Memory.created_at))).scalars())


# --------------------------------------------------------------------------- #
# Agent write paths and context
# --------------------------------------------------------------------------- #
async def test_user_preference_is_saved_and_loaded_into_later_runs(
    client, mem, provider, session_factory
):
    ceo = await register_org(client, "ceo@mem.example.com", "Mem Co")
    agent_id = await _executive(client, ceo)
    provider.queue(
        calls(
            (
                "save_user_memory",
                {"content": "Prefers briefings as 3 bullets", "category": "preference"},
            )
        ),
        text("Noted."),
    )
    first = await _execute(client, ceo, agent_id, "Keep my briefings to three bullets")
    assert first["status"] == "completed", first
    assert _MEMORY_HEADER not in _system(provider, 0)  # nothing remembered yet

    [memory] = await _memories(session_factory)
    assert memory.scope == "USER" and str(memory.user_id) == ceo["user"]["id"]
    assert str(memory.created_by_agent_id) == agent_id
    assert str(memory.source_run_id) == first["run_id"]

    provider.queue(text("Here is your briefing."))
    second = await _execute(client, ceo, agent_id, "Brief me")
    system = _system(provider)
    assert _MEMORY_HEADER in system and "Prefers briefings as 3 bullets" in system
    assert "not instructions" in system

    run = (await client.get(f"/api/v1/runs/{second['run_id']}", headers=_h(ceo))).json()
    [step] = [s for s in run["steps"] if s["step_type"] == "MEMORY"]
    # The trace records counts only, never memory content.
    assert step["detail"] == {"user": 1, "agent": 0, "organization": 0}
    assert "bullets" not in str(run)


async def test_user_memories_are_private_to_their_person(client, mem, provider, session_factory):
    ceo = await register_org(client, "ceo2@mem.example.com", "Private Co")
    org = ceo["organization_id"]
    coo = await _member(client, session_factory, org, "coo@mem.example.com", "ADMIN")
    agent_id = await _executive(client, ceo)
    created = await client.post(
        "/api/v1/memories", headers=_h(ceo), json={"content": "Daughter's school run at 3pm"}
    )
    assert created.status_code == 201, created.text
    memory_id = created.json()["id"]

    # Another person (even an admin) running the same agent never sees it.
    provider.queue(calls(("recall_memories", {})), text("Nothing."))
    await _execute(client, coo, agent_id)
    assert "school run" not in _system(provider, 0)
    assert "school run" not in _tool_result(provider)

    listed = (await client.get("/api/v1/memories", headers=_h(coo))).json()
    assert listed["total"] == 0
    assert (await client.get(f"/api/v1/memories/{memory_id}", headers=_h(coo))).status_code == 404
    assert (
        await client.delete(f"/api/v1/memories/{memory_id}", headers=_h(coo))
    ).status_code == 404

    # An agent run by the admin cannot forget someone else's memory either.
    provider.queue(calls(("forget_user_memory", {"memory_id": memory_id})), text("Could not."))
    await _execute(client, coo, agent_id)
    assert "not found" in _tool_result(provider).lower()
    assert len(await _memories(session_factory)) == 1

    mine = (await client.get("/api/v1/memories", headers=_h(ceo))).json()
    assert [m["content"] for m in mine["items"]] == ["Daughter's school run at 3pm"]


async def test_agent_memory_needs_approval_and_is_shared(client, mem, provider, session_factory):
    ceo = await register_org(client, "ceo3@mem.example.com", "Shared Co")
    org = ceo["organization_id"]
    other = await _member(client, session_factory, org, "u@mem.example.com", "MEMBER")
    agent_id = await _executive(client, ceo)
    lesson = "Board packs go to the company secretary first"
    provider.queue(calls(("save_agent_memory", {"content": lesson})))
    paused = await _execute(client, ceo, agent_id, "Remember how board packs work")
    assert paused["status"] == "awaiting_approval"
    assert await _memories(session_factory) == []

    provider.queue(text("Saved."))
    resp = await client.post(f"/api/v1/approvals/{paused['approval_id']}/approve", headers=_h(ceo))
    assert resp.status_code == 200, resp.text
    [memory] = await _memories(session_factory)
    assert memory.scope == "AGENT" and str(memory.agent_id) == agent_id

    provider.queue(text("ok"))
    await _execute(client, other, agent_id)
    assert lesson in _system(provider)


async def test_forget_tool_and_forget_me(client, mem, provider, session_factory):
    ceo = await register_org(client, "ceo4@mem.example.com", "Forget Co")
    agent_id = await _executive(client, ceo)
    ids = []
    for content in ("Likes tea", "Travels on Fridays"):
        resp = await client.post("/api/v1/memories", headers=_h(ceo), json={"content": content})
        ids.append(resp.json()["id"])

    provider.queue(calls(("forget_user_memory", {"memory_id": ids[0]})), text("Forgotten."))
    await _execute(client, ceo, agent_id, "Forget that I like tea")
    assert [m.content for m in await _memories(session_factory)] == ["Travels on Fridays"]

    resp = await client.delete("/api/v1/memories/mine", headers=_h(ceo))
    assert resp.status_code == 200 and resp.json() == {"deleted": 1}
    assert await _memories(session_factory) == []


async def test_agents_without_persistent_memory_do_not_load_it(client, mem, provider):
    ceo = await register_org(client, "ceo5@mem.example.com", "Plain Co")
    await client.post("/api/v1/memories", headers=_h(ceo), json={"content": "Speaks Hausa"})
    resp = await client.post("/api/v1/agent-templates/customer-ai/instantiate", headers=_h(ceo))
    provider.queue(text("Hello"))
    await _execute(client, ceo, resp.json()["id"])
    assert _MEMORY_HEADER not in _system(provider)


# --------------------------------------------------------------------------- #
# Human write paths, permissions and isolation
# --------------------------------------------------------------------------- #
async def test_organization_and_agent_memory_need_memory_manage(
    client, mem, provider, session_factory
):
    admin = await register_org(client, "adm@mem.example.com", "Org Mem")
    org = admin["organization_id"]
    member = await _member(client, session_factory, org, "m@mem.example.com", "MEMBER")
    manager = await _member(client, session_factory, org, "mg@mem.example.com", "MANAGER")
    viewer = await _member(client, session_factory, org, "v@mem.example.com", "VIEWER")
    agent_id = await _executive(client, admin)
    fact = {"scope": "ORGANIZATION", "content": "Financial year ends in March"}

    assert (await client.post("/api/v1/memories", headers=_h(member), json=fact)).status_code == 403
    assert (
        await client.post("/api/v1/memories", headers=_h(viewer), json={"content": "x"})
    ).status_code == 403
    created = await client.post("/api/v1/memories", headers=_h(manager), json=fact)
    assert created.status_code == 201, created.text
    org_memory = created.json()["id"]
    agent_memory = await client.post(
        "/api/v1/memories",
        headers=_h(manager),
        json={"scope": "AGENT", "agent_id": agent_id, "content": "Use GBP for UK entities"},
    )
    assert agent_memory.status_code == 201, agent_memory.text

    # Members read them, but cannot change them.
    listed = (await client.get("/api/v1/memories", headers=_h(member))).json()
    assert {m["scope"] for m in listed["items"]} == {"ORGANIZATION", "AGENT"}
    assert (
        await client.patch(
            f"/api/v1/memories/{org_memory}", headers=_h(member), json={"content": "nope"}
        )
    ).status_code == 403
    assert (
        await client.delete(f"/api/v1/memories/{org_memory}", headers=_h(member))
    ).status_code == 403
    updated = await client.patch(
        f"/api/v1/memories/{org_memory}",
        headers=_h(manager),
        json={"content": "Financial year ends 31 March"},
    )
    assert updated.json()["content"] == "Financial year ends 31 March"

    provider.queue(text("ok"))
    await _execute(client, member, agent_id)
    system = _system(provider)
    assert "Financial year ends 31 March" in system and "Use GBP for UK entities" in system

    # Filters.
    only_agent = (
        await client.get(f"/api/v1/memories?scope=AGENT&agent_id={agent_id}", headers=_h(member))
    ).json()
    assert [m["content"] for m in only_agent["items"]] == ["Use GBP for UK entities"]
    search = (await client.get("/api/v1/memories?q=financial", headers=_h(member))).json()
    assert search["total"] == 1


async def test_memory_is_tenant_isolated(client, mem, session_factory):
    a = await register_org(client, "a@mem.example.com", "Tenant A")
    b = await register_org(client, "b@mem.example.com", "Tenant B")
    agent_a = await _executive(client, a)
    fact = await client.post(
        "/api/v1/memories",
        headers=_h(a),
        json={"scope": "ORGANIZATION", "content": "A's secret sauce"},
    )
    assert (await client.get("/api/v1/memories", headers=_h(b))).json()["total"] == 0
    assert (
        await client.get(f"/api/v1/memories/{fact.json()['id']}", headers=_h(b))
    ).status_code == 404
    # An agent from another organization cannot be targeted.
    resp = await client.post(
        "/api/v1/memories",
        headers=_h(b),
        json={"scope": "AGENT", "agent_id": agent_a, "content": "hijack"},
    )
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "content",
    [
        "My password is hunter2",
        "api_key: abc123",
        "Use sk-abcdefghijklmnopqrstuvwxyz for OpenAI",
        "Card 4111 1111 1111 1111 exp 12/29",
    ],
)
async def test_secrets_are_never_stored(client, mem, content):
    ceo = await register_org(client, f"s{uuid.uuid4().hex[:6]}@mem.example.com", "Secret Co")
    resp = await client.post("/api/v1/memories", headers=_h(ceo), json={"content": content})
    assert resp.status_code == 422, resp.text


async def test_phone_numbers_are_not_mistaken_for_cards(client, mem):
    ceo = await register_org(client, "phone@mem.example.com", "Phone Co")
    resp = await client.post(
        "/api/v1/memories", headers=_h(ceo), json={"content": "Call me on +234 803 123 4567"}
    )
    assert resp.status_code == 201, resp.text


async def test_duplicates_refresh_and_limits_apply(client, mem, session_factory, monkeypatch):
    ceo = await register_org(client, "dup@mem.example.com", "Dup Co")
    first = await client.post(
        "/api/v1/memories", headers=_h(ceo), json={"content": "Likes  jollof"}
    )
    again = await client.post("/api/v1/memories", headers=_h(ceo), json={"content": "Likes jollof"})
    assert first.json()["id"] == again.json()["id"]  # whitespace-normalised dedup

    monkeypatch.setattr(settings, "MEMORY_MAX_PER_SUBJECT", 2)
    assert (
        await client.post("/api/v1/memories", headers=_h(ceo), json={"content": "Second"})
    ).status_code == 201
    full = await client.post("/api/v1/memories", headers=_h(ceo), json={"content": "Third"})
    assert full.status_code == 422 and "full" in full.text

    monkeypatch.setattr(settings, "MEMORY_MAX_CHARS", 10)
    long = await client.post("/api/v1/memories", headers=_h(ceo), json={"content": "x" * 11})
    assert long.status_code == 422


# --------------------------------------------------------------------------- #
# Retention
# --------------------------------------------------------------------------- #
async def test_retention_hides_then_purges_stale_memories(client, mem, provider, session_factory):
    ceo = await register_org(client, "ret@mem.example.com", "Retention Co")
    agent_id = await _executive(client, ceo)
    for content in ("Old habit", "Current habit"):
        await client.post("/api/v1/memories", headers=_h(ceo), json={"content": content})
    async with session_factory() as s:
        await s.execute(
            update(Memory)
            .where(Memory.content == "Old habit")
            .values(updated_at=datetime.now(UTC) - timedelta(days=40))
        )
        await s.commit()

    bad = await client.patch(
        "/api/v1/organizations/current", headers=_h(ceo), json={"memory_retention_days": 0}
    )
    assert bad.status_code == 422
    resp = await client.patch(
        "/api/v1/organizations/current", headers=_h(ceo), json={"memory_retention_days": 30}
    )
    assert resp.status_code == 200 and resp.json()["memory_retention_days"] == 30

    listed = (await client.get("/api/v1/memories", headers=_h(ceo))).json()
    assert [m["content"] for m in listed["items"]] == ["Current habit"]
    provider.queue(text("ok"))
    await _execute(client, ceo, agent_id)
    assert "Old habit" not in _system(provider) and "Current habit" in _system(provider)

    assert await purge_expired_memories(session_factory) == 1
    assert [m.content for m in await _memories(session_factory)] == ["Current habit"]
