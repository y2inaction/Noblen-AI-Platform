"""RBAC enforcement tests: permissions are checked server-side."""

import pytest
from sqlalchemy import select

from app.models.membership import OrganizationMember
from tests.conftest import auth_headers, register_org


@pytest.mark.asyncio
async def test_admin_can_update_organization(client):
    auth = await register_org(client, "admin@acme.example.com", "Acme")
    resp = await client.patch(
        "/api/v1/organizations/current",
        headers=auth_headers(auth),
        json={"name": "Acme Renamed", "currency": "USD"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Acme Renamed"
    assert body["currency"] == "USD"


@pytest.mark.asyncio
async def test_viewer_cannot_update_organization(client, session_factory):
    auth = await register_org(client, "viewer@acme.example.com", "Acme")

    # Demote the caller to VIEWER directly in the DB.
    async with session_factory() as session:
        member = (await session.execute(select(OrganizationMember))).scalars().first()
        member.role_name = "VIEWER"
        await session.commit()

    # VIEWER may read...
    read = await client.get("/api/v1/organizations/current", headers=auth_headers(auth))
    assert read.status_code == 200

    # ...but not manage.
    write = await client.patch(
        "/api/v1/organizations/current",
        headers=auth_headers(auth),
        json={"name": "Nope"},
    )
    assert write.status_code == 403


@pytest.mark.asyncio
async def test_default_currency_is_ngn(client):
    """Localisation default is NGN but configurable (not hard-coded in logic)."""
    auth = await register_org(client, "ng@acme.example.com", "Naija Co")
    resp = await client.get("/api/v1/organizations/current", headers=auth_headers(auth))
    assert resp.status_code == 200
    assert resp.json()["currency"] == "NGN"
    assert resp.json()["timezone"] == "Africa/Lagos"


def test_role_permission_map_is_hierarchical():
    from app.rbac.permissions import Permission, permissions_for_role

    admin = permissions_for_role("ADMIN")
    manager = permissions_for_role("MANAGER")
    viewer = permissions_for_role("VIEWER")

    assert Permission.ORG_MANAGE in admin
    assert Permission.ORG_MANAGE not in viewer
    assert manager.issubset(admin)
    assert viewer.issubset(manager)
