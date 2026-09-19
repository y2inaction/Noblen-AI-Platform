"""AI API endpoint tests: auth, RBAC, tenant isolation, usage, streaming, security."""

import json

import pytest
from sqlalchemy import select

from app.ai.errors import AIProviderUnavailableError
from app.ai.gateway import AIGateway, get_ai_gateway
from app.ai.providers.mock import MockProvider
from app.main import app
from app.models.membership import OrganizationMember
from tests.conftest import auth_headers, register_org


def _mock_gateway(fail_with=None) -> AIGateway:
    return AIGateway(
        providers={"mock": MockProvider(fail_with=fail_with)},
        default_provider="mock",
        default_model="mock-1",
        default_embedding_provider="mock",
        default_embedding_model="mock-embed-1",
        max_retries=0,
    )


@pytest.fixture
def use_mock_gateway(client):
    app.dependency_overrides[get_ai_gateway] = lambda: _mock_gateway()
    yield
    app.dependency_overrides.pop(get_ai_gateway, None)


@pytest.mark.asyncio
async def test_generate_requires_auth(client, use_mock_gateway):
    resp = await client.post(
        "/api/v1/ai/generate",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_generate_success_and_records_usage(client, use_mock_gateway, session_factory):
    auth = await register_org(client, "ai@acme.example.com", "Acme")
    resp = await client.post(
        "/api/v1/ai/generate",
        headers=auth_headers(auth),
        json={"messages": [{"role": "user", "content": "hello there"}]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provider"] == "mock"
    assert body["total_tokens"] >= 1
    assert body["request_id"]
    assert "estimate" in body["estimated_cost_note"].lower()

    # A usage record was persisted for this org.
    from app.models.ai_usage import AIUsageRecord

    async with session_factory() as s:
        rows = (await s.execute(select(AIUsageRecord))).scalars().all()
    assert len(rows) == 1
    assert rows[0].operation == "generate"
    assert rows[0].status == "success"
    assert rows[0].total_tokens == body["total_tokens"]


@pytest.mark.asyncio
async def test_viewer_role_cannot_generate(client, use_mock_gateway, session_factory):
    auth = await register_org(client, "viewer-ai@acme.example.com", "Acme")
    async with session_factory() as s:
        member = (await s.execute(select(OrganizationMember))).scalars().first()
        member.role_name = "VIEWER"
        await s.commit()

    resp = await client.post(
        "/api/v1/ai/generate",
        headers=auth_headers(auth),
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_embed_success(client, use_mock_gateway):
    auth = await register_org(client, "embed@acme.example.com", "Acme")
    resp = await client.post(
        "/api/v1/ai/embed",
        headers=auth_headers(auth),
        json={"texts": ["alpha", "beta"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["vectors"]) == 2
    assert body["dimensions"] == 8


@pytest.mark.asyncio
async def test_stream_returns_sse_events(client, use_mock_gateway):
    auth = await register_org(client, "stream@acme.example.com", "Acme")
    resp = await client.post(
        "/api/v1/ai/stream",
        headers=auth_headers(auth),
        json={"messages": [{"role": "user", "content": "one two three"}]},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = [
        json.loads(line[len("data: ") :])
        for line in resp.text.splitlines()
        if line.startswith("data: ")
    ]
    types = [e["type"] for e in events]
    assert "delta" in types
    assert types[-1] == "done"
    assert "".join(e.get("delta", "") for e in events).strip().endswith("three")


@pytest.mark.asyncio
async def test_provider_failure_returns_503_and_records_failure(client, session_factory):
    app.dependency_overrides[get_ai_gateway] = lambda: _mock_gateway(
        fail_with=AIProviderUnavailableError("down", provider="mock")
    )
    try:
        auth = await register_org(client, "fail@acme.example.com", "Acme")
        resp = await client.post(
            "/api/v1/ai/generate",
            headers=auth_headers(auth),
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 503
        from app.models.ai_usage import AIUsageRecord

        async with session_factory() as s:
            rows = (await s.execute(select(AIUsageRecord))).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "error"
        assert rows[0].error_type == "ai_provider_unavailable_error"
    finally:
        app.dependency_overrides.pop(get_ai_gateway, None)


@pytest.mark.asyncio
async def test_usage_endpoint_lists_own_org_only(client, use_mock_gateway):
    a = await register_org(client, "usage-a@orga.example.com", "Org A")
    b = await register_org(client, "usage-b@orgb.example.com", "Org B")

    # Org A makes two AI calls; Org B makes one.
    for _ in range(2):
        await client.post(
            "/api/v1/ai/generate",
            headers=auth_headers(a),
            json={"messages": [{"role": "user", "content": "a"}]},
        )
    await client.post(
        "/api/v1/ai/generate",
        headers=auth_headers(b),
        json={"messages": [{"role": "user", "content": "b"}]},
    )

    usage_a = await client.get("/api/v1/ai/usage", headers=auth_headers(a))
    usage_b = await client.get("/api/v1/ai/usage", headers=auth_headers(b))
    assert usage_a.status_code == 200
    assert usage_a.json()["total"] == 2
    assert usage_b.json()["total"] == 1
    # Every record returned to A belongs to A's org.
    org_a = a["organization_id"]
    assert all(item["organization_id"] == org_a for item in usage_a.json()["items"])


@pytest.mark.asyncio
async def test_usage_cannot_be_read_for_other_org_via_header(client, use_mock_gateway):
    a = await register_org(client, "spoof-a@orga.example.com", "Org A")
    b = await register_org(client, "spoof-b@orgb.example.com", "Org B")
    headers = auth_headers(a)
    headers["X-Organization-Id"] = str(b["organization_id"])
    resp = await client.get("/api/v1/ai/usage", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_arbitrary_provider_rejected(client, use_mock_gateway):
    auth = await register_org(client, "prov@acme.example.com", "Acme")
    resp = await client.post(
        "/api/v1/ai/generate",
        headers=auth_headers(auth),
        json={"messages": [{"role": "user", "content": "hi"}], "provider": "evil-provider"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_no_secrets_in_response(client, use_mock_gateway):
    auth = await register_org(client, "secret@acme.example.com", "Acme")
    resp = await client.post(
        "/api/v1/ai/generate",
        headers=auth_headers(auth),
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    text = resp.text.lower()
    for needle in ("api_key", "apikey", "authorization", "secret", "sk-"):
        assert needle not in text
