"""Agent API tests: auth, RBAC, lifecycle, execution, and tenant isolation."""

import pytest
from sqlalchemy import select

from app.models.membership import OrganizationMember
from tests.agents.conftest import create_agent, make_active_agent
from tests.conftest import auth_headers, register_org


@pytest.mark.asyncio
async def test_create_agent_requires_auth(client):
    resp = await client.post("/api/v1/agents", json={"name": "X"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_viewer_cannot_create_agent(client, session_factory):
    auth = await register_org(client, "viewer@acme.example.com", "Acme")
    async with session_factory() as s:
        member = (await s.execute(select(OrganizationMember))).scalars().first()
        member.role_name = "VIEWER"
        await s.commit()
    resp = await client.post("/api/v1/agents", headers=auth_headers(auth), json={"name": "Blocked"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_agent_crud_and_versioning(client, seeded_tools):
    auth = await register_org(client, "admin@acme.example.com", "Acme")
    agent = await create_agent(client, auth, auth_headers)
    agent_id = agent["id"]
    assert agent["status"] == "DRAFT"

    # Update draft.
    patched = await client.patch(
        f"/api/v1/agents/{agent_id}",
        headers=auth_headers(auth),
        json={"system_instructions": "Be concise."},
    )
    assert patched.status_code == 200
    assert patched.json()["system_instructions"] == "Be concise."

    # Cut and activate a version.
    ver = await client.post(
        f"/api/v1/agents/{agent_id}/versions", headers=auth_headers(auth), json={}
    )
    assert ver.status_code == 201
    version_id = ver.json()["id"]
    act = await client.post(
        f"/api/v1/agents/{agent_id}/versions/{version_id}/activate", headers=auth_headers(auth)
    )
    assert act.status_code == 200
    assert act.json()["status"] == "ACTIVE"
    assert act.json()["active_version_id"] == version_id


@pytest.mark.asyncio
async def test_execute_requires_active_version(client, mock_runtime):
    auth = await register_org(client, "noactive@acme.example.com", "Acme")
    agent = await create_agent(client, auth, auth_headers)
    resp = await client.post(
        f"/api/v1/agents/{agent['id']}/execute",
        headers=auth_headers(auth),
        json={"message": "hi"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "no_active_agent_version"


@pytest.mark.asyncio
async def test_execute_completed(client, mock_runtime):
    auth = await register_org(client, "run@acme.example.com", "Acme")
    agent_id = await make_active_agent(client, auth, auth_headers)
    resp = await client.post(
        f"/api/v1/agents/{agent_id}/execute",
        headers=auth_headers(auth),
        json={"message": "hello there"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["message"]["role"] == "assistant"
    assert body["conversation_id"]
    assert body["usage"]["requests"] >= 1


@pytest.mark.asyncio
async def test_get_agent_tenant_isolation(client, seeded_tools):
    a = await register_org(client, "a@orga.example.com", "Org A")
    b = await register_org(client, "b@orgb.example.com", "Org B")
    agent = await create_agent(client, a, auth_headers)
    # Org B cannot see Org A's agent.
    resp = await client.get(f"/api/v1/agents/{agent['id']}", headers=auth_headers(b))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_execute_other_org_agent_forbidden(client, mock_runtime):
    a = await register_org(client, "a2@orga.example.com", "Org A")
    b = await register_org(client, "b2@orgb.example.com", "Org B")
    agent_id = await make_active_agent(client, a, auth_headers)
    resp = await client.post(
        f"/api/v1/agents/{agent_id}/execute",
        headers=auth_headers(b),
        json={"message": "hi"},
    )
    # Agent belongs to Org A; Org B's tenant scope makes it not-found.
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_agent_list_scoped_to_org(client, seeded_tools):
    a = await register_org(client, "list-a@orga.example.com", "Org A")
    b = await register_org(client, "list-b@orgb.example.com", "Org B")
    await create_agent(client, a, auth_headers, name="A Agent")
    await create_agent(client, b, auth_headers, name="B Agent")
    resp = await client.get("/api/v1/agents", headers=auth_headers(a))
    assert resp.status_code == 200
    names = {item["name"] for item in resp.json()["items"]}
    assert names == {"A Agent"}
