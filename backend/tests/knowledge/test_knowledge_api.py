"""Knowledge API tests (PostgreSQL + pgvector): CRUD, ingestion, search, isolation."""

import pytest
from sqlalchemy import select

from app.models.membership import OrganizationMember
from tests.conftest import auth_headers

_REFUND = "Our refund policy allows returns within 30 days of purchase."


async def _register(pg_client, email, org="Acme"):
    r = await pg_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Password123!", "organization_name": org},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _make_kb(pg_client, auth, name="Docs"):
    r = await pg_client.post(
        "/api/v1/knowledge-bases", headers=auth_headers(auth), json={"name": name}
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _add_text_doc(pg_client, auth, kb_id, name, content):
    r = await pg_client.post(
        f"/api/v1/knowledge-bases/{kb_id}/documents/text",
        headers=auth_headers(auth),
        json={"name": name, "content": content},
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_knowledge_base_crud(pg_client):
    auth = await _register(pg_client, "kb@acme.example.com")
    kb_id = await _make_kb(pg_client, auth, "Policies")

    got = await pg_client.get(f"/api/v1/knowledge-bases/{kb_id}", headers=auth_headers(auth))
    assert got.status_code == 200
    assert got.json()["embedding_dimension"] == 1536

    listing = await pg_client.get("/api/v1/knowledge-bases", headers=auth_headers(auth))
    assert listing.json()["total"] == 1

    patched = await pg_client.patch(
        f"/api/v1/knowledge-bases/{kb_id}",
        headers=auth_headers(auth),
        json={"description": "Company policies"},
    )
    assert patched.json()["description"] == "Company policies"

    deleted = await pg_client.delete(f"/api/v1/knowledge-bases/{kb_id}", headers=auth_headers(auth))
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "ARCHIVED"


@pytest.mark.asyncio
async def test_ingest_text_document_becomes_ready(pg_client):
    auth = await _register(pg_client, "ingest@acme.example.com")
    kb_id = await _make_kb(pg_client, auth)
    doc = await _add_text_doc(pg_client, auth, kb_id, "Refund", _REFUND)
    assert doc["status"] == "READY"
    assert doc["chunk_count"] >= 1
    assert doc["checksum"]


@pytest.mark.asyncio
async def test_search_returns_chunk_with_citation(pg_client):
    auth = await _register(pg_client, "search@acme.example.com")
    kb_id = await _make_kb(pg_client, auth)
    await _add_text_doc(pg_client, auth, kb_id, "Refund", _REFUND)
    await _add_text_doc(pg_client, auth, kb_id, "Other", "Office hours are 9am to 5pm on weekdays.")

    # Query identical to a chunk → mock embedding matches exactly → similarity ~1.0.
    resp = await pg_client.post(
        "/api/v1/knowledge/search",
        headers=auth_headers(auth),
        json={"query": _REFUND, "top_k": 3},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] >= 1
    top = body["results"][0]
    assert _REFUND in top["content"]
    assert top["similarity"] > 0.99
    assert top["citation"]["document_name"] == "Refund"
    assert top["citation"]["label"].startswith("[1]")


@pytest.mark.asyncio
async def test_search_is_tenant_isolated(pg_client):
    a = await _register(pg_client, "iso-a@orga.example.com", "Org A")
    b = await _register(pg_client, "iso-b@orgb.example.com", "Org B")
    kb_a = await _make_kb(pg_client, a, "A KB")
    kb_b = await _make_kb(pg_client, b, "B KB")
    # Identical content in both orgs.
    await _add_text_doc(pg_client, a, kb_a, "Refund", _REFUND)
    await _add_text_doc(pg_client, b, kb_b, "Refund", _REFUND)

    resp = await pg_client.post(
        "/api/v1/knowledge/search", headers=auth_headers(a), json={"query": _REFUND}
    )
    assert resp.status_code == 200
    kb_ids = {r["knowledge_base_id"] for r in resp.json()["results"]}
    assert kb_ids <= {kb_a}  # only Org A's KB — never Org B's
    assert kb_b not in kb_ids


@pytest.mark.asyncio
async def test_viewer_cannot_create_knowledge_base(pg_client, pg_sessions):
    auth = await _register(pg_client, "viewer-kb@acme.example.com")
    async with pg_sessions() as s:
        member = (await s.execute(select(OrganizationMember))).scalars().first()
        member.role_name = "VIEWER"
        await s.commit()
    resp = await pg_client.post(
        "/api/v1/knowledge-bases", headers=auth_headers(auth), json={"name": "Nope"}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_unsupported_upload_marks_document_failed(pg_client):
    auth = await _register(pg_client, "bin@acme.example.com")
    kb_id = await _make_kb(pg_client, auth)
    files = {"file": ("data.bin", b"\x00\x01\x02binary", "application/octet-stream")}
    resp = await pg_client.post(
        f"/api/v1/knowledge-bases/{kb_id}/documents/upload",
        headers=auth_headers(auth),
        files=files,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "FAILED"
    assert body["error_message"]


@pytest.mark.asyncio
async def test_document_idempotency_and_reingest(pg_client):
    auth = await _register(pg_client, "idem@acme.example.com")
    kb_id = await _make_kb(pg_client, auth)
    d1 = await _add_text_doc(pg_client, auth, kb_id, "Refund", _REFUND)
    d2 = await _add_text_doc(pg_client, auth, kb_id, "Refund", _REFUND)
    assert d1["id"] == d2["id"]  # same checksum → same document (idempotent)

    reingest = await pg_client.post(
        f"/api/v1/documents/{d1['id']}/ingest", headers=auth_headers(auth)
    )
    assert reingest.status_code == 200
    assert reingest.json()["status"] == "READY"
