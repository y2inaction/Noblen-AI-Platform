"""GET /organizations/current/access and organization names in /auth/me (M7)."""

from __future__ import annotations

import uuid

from app.models.membership import OrganizationMember
from tests.conftest import auth_headers, register_org


async def test_access_reflects_the_role_in_the_selected_organization(client, session_factory):
    owner = await register_org(client, "own@acme.example.com", "Acme Ops")
    viewer = await register_org(client, "view@acme.example.com", "Viewer Home")
    async with session_factory() as s:
        s.add(
            OrganizationMember(
                user_id=uuid.UUID(viewer["user"]["id"]),
                organization_id=uuid.UUID(owner["organization_id"]),
                role_name="VIEWER",
                status="ACTIVE",
            )
        )
        await s.commit()

    mine = (
        await client.get("/api/v1/organizations/current/access", headers=auth_headers(owner))
    ).json()
    assert mine["organization_name"] == "Acme Ops"
    assert {"org:manage", "agent:run", "workflow:manage"} <= set(mine["permissions"])

    headers = {**auth_headers(viewer), "X-Organization-Id": owner["organization_id"]}
    theirs = (await client.get("/api/v1/organizations/current/access", headers=headers)).json()
    assert theirs["role_name"] == "VIEWER" and theirs["organization_name"] == "Acme Ops"
    assert "agent:run" not in theirs["permissions"]
    assert "run:view" in theirs["permissions"]

    me = (await client.get("/api/v1/auth/me", headers=auth_headers(viewer))).json()
    assert sorted(m["organization_name"] for m in me["memberships"]) == ["Acme Ops", "Viewer Home"]

    outsider = await register_org(client, "out@other.example.com", "Other")
    blocked = await client.get(
        "/api/v1/organizations/current/access",
        headers={**auth_headers(outsider), "X-Organization-Id": owner["organization_id"]},
    )
    assert blocked.status_code in (403, 404)
