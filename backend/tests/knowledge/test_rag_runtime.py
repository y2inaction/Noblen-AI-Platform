"""End-to-end RAG via the Agent Runtime (PostgreSQL + pgvector).

Proves the Phase 3 ↔ Phase 4 bridge: an agent calls the `search_knowledge` tool,
the runtime resolves the agent's authorized knowledge bases server-side, retrieves
from pgvector, and returns cited passages as tool data.
"""

import json

import pytest

from tests.conftest import auth_headers

_POLICY = "Our refund policy allows returns within 30 days of purchase."


async def _register(pg_client, email, org="Acme"):
    r = await pg_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Password123!", "organization_name": org},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _tool_id(pg_client, auth, name):
    r = await pg_client.get("/api/v1/tools", headers=auth_headers(auth))
    return next(t["id"] for t in r.json()["items"] if t["name"] == name)


async def _active_agent_with_knowledge(pg_client, auth, *, attach_kb: str | None):
    a = await pg_client.post("/api/v1/agents", headers=auth_headers(auth), json={"name": "RAG Bot"})
    agent_id = a.json()["id"]
    tid = await _tool_id(pg_client, auth, "search_knowledge")
    await pg_client.post(
        f"/api/v1/agents/{agent_id}/tools", headers=auth_headers(auth), json={"tool_id": tid}
    )
    if attach_kb:
        r = await pg_client.post(
            f"/api/v1/agents/{agent_id}/knowledge-bases",
            headers=auth_headers(auth),
            json={"knowledge_base_id": attach_kb},
        )
        assert r.status_code == 201, r.text
    ver = await pg_client.post(
        f"/api/v1/agents/{agent_id}/versions", headers=auth_headers(auth), json={}
    )
    await pg_client.post(
        f"/api/v1/agents/{agent_id}/versions/{ver.json()['id']}/activate",
        headers=auth_headers(auth),
    )
    return agent_id


async def _ingest(pg_client, auth, kb_id, name, content):
    r = await pg_client.post(
        f"/api/v1/knowledge-bases/{kb_id}/documents/text",
        headers=auth_headers(auth),
        json={"name": name, "content": content},
    )
    assert r.status_code == 201, r.text


def _directive(query: str) -> str:
    return f"please look this up [[tool:search_knowledge|{json.dumps({'query': query})}]]"


async def _tool_messages(pg_client, auth, conversation_id):
    r = await pg_client.get(
        f"/api/v1/conversations/{conversation_id}/messages", headers=auth_headers(auth)
    )
    return [m for m in r.json() if m["role"] == "tool"]


@pytest.mark.asyncio
async def test_agent_retrieves_from_authorized_kb(pg_client):
    auth = await _register(pg_client, "rag@acme.example.com")
    kb = await pg_client.post(
        "/api/v1/knowledge-bases", headers=auth_headers(auth), json={"name": "Policies"}
    )
    kb_id = kb.json()["id"]
    await _ingest(pg_client, auth, kb_id, "Refund", _POLICY)
    agent_id = await _active_agent_with_knowledge(pg_client, auth, attach_kb=kb_id)

    resp = await pg_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        headers=auth_headers(auth),
        json={"message": _directive(_POLICY)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"

    tool_msgs = await _tool_messages(pg_client, auth, body["conversation_id"])
    assert len(tool_msgs) == 1
    payload = json.loads(tool_msgs[0]["content"])
    assert payload["count"] >= 1
    assert any(_POLICY in r["content"] for r in payload["results"])
    assert payload["citations"][0]["document_name"] == "Refund"
    assert "notice" in payload  # untrusted-content labelling for prompt-injection defense


@pytest.mark.asyncio
async def test_agent_without_knowledge_source_gets_nothing(pg_client):
    auth = await _register(pg_client, "norag@acme.example.com")
    kb = await pg_client.post(
        "/api/v1/knowledge-bases", headers=auth_headers(auth), json={"name": "Policies"}
    )
    kb_id = kb.json()["id"]
    await _ingest(pg_client, auth, kb_id, "Refund", _POLICY)
    # Agent has the tool but NO knowledge base attached → not authorized to search it.
    agent_id = await _active_agent_with_knowledge(pg_client, auth, attach_kb=None)

    resp = await pg_client.post(
        f"/api/v1/agents/{agent_id}/execute",
        headers=auth_headers(auth),
        json={"message": _directive(_POLICY)},
    )
    assert resp.status_code == 200, resp.text
    tool_msgs = await _tool_messages(pg_client, auth, resp.json()["conversation_id"])
    payload = json.loads(tool_msgs[0]["content"])
    assert payload["count"] == 0
    assert "no authorized knowledge bases" in payload.get("message", "").lower()
