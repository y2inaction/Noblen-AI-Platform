"""Approval API tests: pause → approve/reject, RBAC, and tenant isolation."""

import pytest
from sqlalchemy import select

from app.models.membership import OrganizationMember
from tests.agents.conftest import make_active_agent
from tests.conftest import auth_headers, register_org

_ECHO_MSG = 'please [[tool:echo|{"text":"hi there"}]]'


async def _execute_until_approval(client, auth, agent_id):
    resp = await client.post(
        f"/api/v1/agents/{agent_id}/execute",
        headers=auth_headers(auth),
        json={"message": _ECHO_MSG},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "awaiting_approval"
    assert body["approval_id"]
    return body["approval_id"]


@pytest.mark.asyncio
async def test_approve_resumes_and_completes(client, mock_runtime):
    auth = await register_org(client, "appr@acme.example.com", "Acme")
    agent_id = await make_active_agent(client, auth, auth_headers, tools=["echo"])
    approval_id = await _execute_until_approval(client, auth, agent_id)

    listing = await client.get("/api/v1/approvals", headers=auth_headers(auth))
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["status"] == "PENDING"

    approve = await client.post(
        f"/api/v1/approvals/{approval_id}/approve", headers=auth_headers(auth)
    )
    assert approve.status_code == 200, approve.text
    body = approve.json()
    assert body["approval"]["status"] == "APPROVED"
    assert body["execution"]["status"] == "completed"


@pytest.mark.asyncio
async def test_reject_does_not_execute(client, mock_runtime):
    auth = await register_org(client, "rej@acme.example.com", "Acme")
    agent_id = await make_active_agent(client, auth, auth_headers, tools=["echo"])
    approval_id = await _execute_until_approval(client, auth, agent_id)

    reject = await client.post(
        f"/api/v1/approvals/{approval_id}/reject", headers=auth_headers(auth)
    )
    assert reject.status_code == 200, reject.text
    assert reject.json()["approval"]["status"] == "REJECTED"

    # Re-deciding an already-decided approval is rejected by the state machine.
    again = await client.post(
        f"/api/v1/approvals/{approval_id}/approve", headers=auth_headers(auth)
    )
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_member_cannot_approve(client, mock_runtime, session_factory):
    auth = await register_org(client, "memappr@acme.example.com", "Acme")
    agent_id = await make_active_agent(client, auth, auth_headers, tools=["echo"])
    approval_id = await _execute_until_approval(client, auth, agent_id)

    # Demote to MEMBER (can run agents, but lacks agent:approve_actions).
    async with session_factory() as s:
        member = (await s.execute(select(OrganizationMember))).scalars().first()
        member.role_name = "MEMBER"
        await s.commit()

    resp = await client.post(f"/api/v1/approvals/{approval_id}/approve", headers=auth_headers(auth))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_approval_tenant_isolation(client, mock_runtime):
    a = await register_org(client, "iso-a@orga.example.com", "Org A")
    b = await register_org(client, "iso-b@orgb.example.com", "Org B")
    agent_id = await make_active_agent(client, a, auth_headers, tools=["echo"])
    approval_id = await _execute_until_approval(client, a, agent_id)

    # Org B must not see or act on Org A's approval.
    assert (
        await client.get(f"/api/v1/approvals/{approval_id}", headers=auth_headers(b))
    ).status_code == 404
    assert (
        await client.post(f"/api/v1/approvals/{approval_id}/approve", headers=auth_headers(b))
    ).status_code == 404
    assert (await client.get("/api/v1/approvals", headers=auth_headers(b))).json()["total"] == 0
