import pytest

from tests.conftest import auth_headers, register_org


@pytest.mark.asyncio
async def test_register_creates_user_and_org(client):
    auth = await register_org(client, "founder@acme.example.com", "Acme Ltd")
    assert auth["user"]["email"] == "founder@acme.example.com"
    assert auth["role_name"] == "ADMIN"
    assert auth["tokens"]["access_token"]
    assert auth["tokens"]["refresh_token"]


@pytest.mark.asyncio
async def test_register_duplicate_email_conflicts(client):
    await register_org(client, "dup@acme.example.com", "Acme")
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "dup@acme.example.com",
            "password": "Password123!",
            "organization_name": "Other Org",
        },
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_login_success_and_wrong_password(client):
    await register_org(client, "login@acme.example.com", "Acme", password="Password123!")

    ok = await client.post(
        "/api/v1/auth/login",
        json={"email": "login@acme.example.com", "password": "Password123!"},
    )
    assert ok.status_code == 200
    assert ok.json()["tokens"]["access_token"]

    bad = await client.post(
        "/api/v1/auth/login",
        json={"email": "login@acme.example.com", "password": "wrong-password"},
    )
    assert bad.status_code == 401


@pytest.mark.asyncio
async def test_login_unknown_user(client):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "ghost@nowhere.example.com", "password": "whatever12"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_requires_auth(client):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_returns_membership(client):
    auth = await register_org(client, "me@acme.example.com", "Acme")
    resp = await client.get("/api/v1/auth/me", headers=auth_headers(auth))
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["email"] == "me@acme.example.com"
    assert len(body["memberships"]) == 1
    assert body["memberships"][0]["role_name"] == "ADMIN"


@pytest.mark.asyncio
async def test_refresh_rotates_token(client):
    auth = await register_org(client, "refresh@acme.example.com", "Acme")
    old_refresh = auth["tokens"]["refresh_token"]

    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert resp.status_code == 200
    new_tokens = resp.json()
    assert new_tokens["access_token"]

    # Old refresh token is now revoked and cannot be reused.
    reuse = await client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert reuse.status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_refresh(client):
    auth = await register_org(client, "logout@acme.example.com", "Acme")
    refresh = auth["tokens"]["refresh_token"]

    out = await client.post("/api/v1/auth/logout", json={"refresh_token": refresh})
    assert out.status_code == 200

    reuse = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert reuse.status_code == 401


@pytest.mark.asyncio
async def test_password_min_length_validation(client):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "short@acme.example.com", "password": "short", "organization_name": "Acme"},
    )
    assert resp.status_code == 422
