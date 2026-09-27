"""Conversations are private to their participants (security review fix).

They hold what an agent retrieved for someone, including private user memories
and knowledge restricted to that person, so other members of the organization
(admins included) must not be able to list, read, write to or continue them.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from app.agents.runtime import AgentRuntime, get_agent_runtime
from app.agents.tools.seed import seed_builtin_tools
from app.ai.gateway import AIGateway
from app.main import app
from app.models.membership import OrganizationMember
from tests.agents.test_controlled_autonomy import ScriptedProvider, calls, text
from tests.conftest import auth_headers, register_org

SECRET = "Interviewing with a competitor next week"


@pytest.fixture
def provider() -> ScriptedProvider:
    return ScriptedProvider()


@pytest_asyncio.fixture
async def env(client, session_factory, provider):
    async with session_factory() as session:
        await seed_builtin_tools(session)
        await session.commit()
    gateway = AIGateway(
        providers={"scripted": provider},
        default_provider="scripted",
        default_model="scripted-1",
        max_retries=0,
    )
    app.dependency_overrides[get_agent_runtime] = lambda: AgentRuntime(gateway=gateway)
    yield
    app.dependency_overrides.pop(get_agent_runtime, None)


async def _join(client, session_factory, org_id: str, email: str, role: str) -> dict:
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
    return auth


def _h(auth: dict, org_id: str) -> dict[str, str]:
    return {**auth_headers(auth), "X-Organization-Id": org_id}


@pytest.mark.parametrize("role", ["VIEWER", "MEMBER", "ADMIN"])
async def test_other_members_cannot_reach_someone_elses_conversation(
    client, env, provider, session_factory, role
):
    owner = await register_org(client, f"own-{role}@acme.example.com", "Acme Private")
    org = owner["organization_id"]
    alice = await _join(client, session_factory, org, f"alice-{role}@acme.example.com", "MEMBER")
    other = await _join(client, session_factory, org, f"other-{role}@acme.example.com", role)

    agent = (
        await client.post(
            "/api/v1/agent-templates/executive-ai/instantiate", headers=_h(owner, org)
        )
    ).json()
    saved = await client.post("/api/v1/memories", headers=_h(alice, org), json={"content": SECRET})
    assert saved.status_code == 201, saved.text
    # Alice's agent recalls her private memory: the tool result lands in her conversation.
    provider.queue(calls(("recall_memories", {})), text("Noted."))
    result = (
        await client.post(
            f"/api/v1/agents/{agent['id']}/execute", headers=_h(alice, org), json={"message": "hi"}
        )
    ).json()
    conversation_id = result["conversation_id"]

    mine = await client.get(
        f"/api/v1/conversations/{conversation_id}/messages", headers=_h(alice, org)
    )
    assert mine.status_code == 200 and SECRET in mine.text

    theirs = _h(other, org)
    listed = (await client.get("/api/v1/conversations", headers=theirs)).json()
    assert conversation_id not in [c["id"] for c in listed["items"]]
    for path in (
        f"/api/v1/conversations/{conversation_id}",
        f"/api/v1/conversations/{conversation_id}/messages",
    ):
        response = await client.get(path, headers=theirs)
        assert response.status_code in (403, 404) and SECRET not in response.text
    posted = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages", headers=theirs, json={"content": "x"}
    )
    assert posted.status_code in (403, 404)
    # Continuing Alice's conversation would load her history into their run.
    if role != "VIEWER":
        calls_before = len(provider.requests)
        continued = await client.post(
            f"/api/v1/agents/{agent['id']}/execute",
            headers=theirs,
            json={"message": "What did you recall?", "conversation_id": conversation_id},
        )
        assert continued.status_code == 404 and SECRET not in continued.text
        assert len(provider.requests) == calls_before  # the model never saw her history
