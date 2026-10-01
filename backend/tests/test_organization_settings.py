"""`require_approval_to_publish_restricted` organization setting (M9.3, ADR-0038).

The setting is stored and exposed through the existing organization settings API.
Enforcement is M9.4 onwards, so these tests cover only the setting itself.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.models.membership import OrganizationMember
from tests.conftest import auth_headers, register_org

SETTING = "require_approval_to_publish_restricted"
URL = "/api/v1/organizations/current"


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
    return auth


def _h(auth: dict, org_id: str | None = None) -> dict[str, str]:
    headers = auth_headers(auth)
    if org_id:
        headers["X-Organization-Id"] = org_id
    return headers


async def test_a_new_organization_defaults_to_off(client):
    owner = await register_org(client, "new@settings.example.com", "Settings New")
    body = (await client.get(URL, headers=_h(owner))).json()
    assert body[SETTING] is False


async def test_an_existing_organization_reads_off(client, session_factory):
    # A row written without the column, as before the migration, gets the
    # server default.
    owner = await register_org(client, "old@settings.example.com", "Settings Old")
    org_id = uuid.uuid4()
    async with session_factory() as s:

        def db_id(value: uuid.UUID) -> object:
            return value.hex if s.bind.dialect.name == "sqlite" else value

        await s.execute(
            text(
                "INSERT INTO organizations (id, name, slug, is_active, currency, timezone, "
                "locale, require_independent_approval) VALUES (:id, 'Legacy Org', 'legacy', "
                "true, 'USD', 'UTC', 'en', false)"
            ),
            {"id": db_id(org_id)},
        )
        await s.execute(
            text(
                "INSERT INTO organization_members (id, user_id, organization_id, role_name, "
                "status) VALUES (:id, :user_id, :org_id, 'ADMIN', 'ACTIVE')"
            ),
            {
                "id": db_id(uuid.uuid4()),
                "user_id": db_id(uuid.UUID(owner["user"]["id"])),
                "org_id": db_id(org_id),
            },
        )
        await s.commit()

    body = (await client.get(URL, headers=_h(owner, str(org_id)))).json()
    assert body["name"] == "Legacy Org"
    assert body[SETTING] is False


async def test_an_admin_can_turn_it_on_and_off_and_it_persists(client):
    owner = await register_org(client, "toggle@settings.example.com", "Settings Toggle")

    on = await client.patch(URL, headers=_h(owner), json={SETTING: True})
    assert on.status_code == 200 and on.json()[SETTING] is True
    assert (await client.get(URL, headers=_h(owner))).json()[SETTING] is True

    off = await client.patch(URL, headers=_h(owner), json={SETTING: False})
    assert off.status_code == 200 and off.json()[SETTING] is False
    assert (await client.get(URL, headers=_h(owner))).json()[SETTING] is False


async def test_other_updates_leave_it_unchanged(client):
    owner = await register_org(client, "keep@settings.example.com", "Settings Keep")
    await client.patch(URL, headers=_h(owner), json={SETTING: True})

    renamed = await client.patch(
        URL, headers=_h(owner), json={"name": "Settings Kept", "require_independent_approval": True}
    )
    assert renamed.status_code == 200
    body = (await client.get(URL, headers=_h(owner))).json()
    assert body[SETTING] is True and body["require_independent_approval"] is True


async def test_it_is_independent_of_separation_of_duties(client):
    owner = await register_org(client, "indep@settings.example.com", "Settings Indep")
    await client.patch(URL, headers=_h(owner), json={SETTING: True})
    body = (await client.get(URL, headers=_h(owner))).json()
    assert body[SETTING] is True and body["require_independent_approval"] is False


@pytest.mark.parametrize("role", ["MANAGER", "MEMBER", "VIEWER"])
async def test_only_org_manage_may_change_it(client, session_factory, role):
    owner = await register_org(client, f"own-{role}@settings.example.com", f"Settings {role}")
    org = owner["organization_id"]
    person = await _member(client, session_factory, org, f"{role}@settings.example.com", role)

    refused = await client.patch(URL, headers=_h(person, org), json={SETTING: True})
    assert refused.status_code == 403
    read = await client.get(URL, headers=_h(person, org))
    assert read.status_code == 200 and read.json()[SETTING] is False
    assert (await client.get(URL, headers=_h(owner))).json()[SETTING] is False


async def test_an_outsider_can_neither_read_nor_change_it(client):
    owner = await register_org(client, "own-out@settings.example.com", "Settings Out")
    outsider = await register_org(client, "outsider@settings.example.com", "Elsewhere")
    org = owner["organization_id"]

    assert (await client.get(URL, headers=_h(outsider, org))).status_code in (403, 404)
    changed = await client.patch(URL, headers=_h(outsider, org), json={SETTING: True})
    assert changed.status_code in (403, 404)
    assert (await client.get(URL, headers=_h(owner))).json()[SETTING] is False


@pytest.mark.parametrize("value", ["maybe", 2, [], {}, None])
async def test_non_boolean_values_are_rejected(client, value):
    owner = await register_org(client, "bad@settings.example.com", "Settings Bad")
    await client.patch(URL, headers=_h(owner), json={SETTING: True})

    resp = await client.patch(URL, headers=_h(owner), json={SETTING: value})
    assert resp.status_code == 422
    assert (await client.get(URL, headers=_h(owner))).json()[SETTING] is True
