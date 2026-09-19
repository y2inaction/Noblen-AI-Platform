"""CRITICAL security tests: a user must NEVER access another org's data.

This suite is a permanent quality gate (ARCHITECTURE.md §4, spec §29).
"""

import pytest

from tests.conftest import auth_headers, register_org


@pytest.mark.asyncio
async def test_user_cannot_read_other_org_by_spoofing_header(client):
    a = await register_org(client, "a@orga.example.com", "Org A")
    b = await register_org(client, "b@orgb.example.com", "Org B")
    org_b_id = b["organization_id"]

    # User A presents their own valid token but points X-Organization-Id at Org B.
    headers = auth_headers(a)
    headers["X-Organization-Id"] = str(org_b_id)

    resp = await client.get("/api/v1/organizations/current", headers=headers)
    # Must be forbidden — A has no membership in B.
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_member_list_is_scoped_to_own_org(client):
    a = await register_org(client, "owner@orga.example.com", "Org A")
    await register_org(client, "owner@orgb.example.com", "Org B")

    resp = await client.get("/api/v1/organizations/current/members", headers=auth_headers(a))
    assert resp.status_code == 200
    members = resp.json()
    emails = {m["email"] for m in members}
    # Only Org A's own member is visible; Org B's owner must not leak.
    assert emails == {"owner@orga.example.com"}


@pytest.mark.asyncio
async def test_cannot_modify_member_in_other_org(client):
    a = await register_org(client, "a2@orga.example.com", "Org A")
    b = await register_org(client, "b2@orgb.example.com", "Org B")

    # Find Org B's member id (owner) via B's own token.
    b_members = (
        await client.get("/api/v1/organizations/current/members", headers=auth_headers(b))
    ).json()
    b_member_id = b_members[0]["id"]

    # User A (admin of A) tries to change a role of a member that lives in Org B.
    resp = await client.patch(
        f"/api/v1/organizations/current/members/{b_member_id}/role",
        headers=auth_headers(a),
        json={"role_name": "VIEWER"},
    )
    # A's active org is A; the member belongs to B, so it must be not-found (scoped out).
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_tenant_scoped_helper_rejects_non_tenant_model():
    import uuid

    from sqlalchemy import select

    from app.db.tenant import tenant_scoped
    from app.models.user import User  # User is intentionally NOT tenant-scoped

    with pytest.raises(ValueError):
        tenant_scoped(select(User), User, uuid.uuid4())
