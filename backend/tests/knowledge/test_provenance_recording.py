"""Milestone 8 on PostgreSQL + pgvector: retrieval records exactly what it returned.

Documents filtered out by the initiator's ACL are never recorded, and chunk text
never enters provenance (docs/architecture/milestone-8-provenance.md §5, I2).
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import select

from app.models.run import AgentRun
from tests.knowledge.test_access_control import (
    _HANDBOOK,
    _SALARIES,
    _agent,
    _doc,
    _h,
    _join,
    _kb,
    _register,
)


async def _search_run(pg_client, pg_sessions, auth, agent_id) -> AgentRun:
    directive = 'go [[tool:search_knowledge|{"query": "salary"}]]'
    r = await pg_client.post(
        f"/api/v1/agents/{agent_id}/execute", headers=_h(auth), json={"message": directive}
    )
    assert r.status_code == 200, r.text
    async with pg_sessions() as s:
        run = (
            await s.execute(select(AgentRun).where(AgentRun.id == uuid.UUID(r.json()["run_id"])))
        ).scalar_one()
    return run


async def test_retrieval_records_returned_documents_only(pg_client, pg_sessions):
    admin = await _register(pg_client, "prov-admin@acl.example.com")
    member = await _join(pg_client, pg_sessions, admin, "prov-m@acl.example.com", "MEMBER")
    kb = await _kb(pg_client, admin, "Company")
    handbook = await _doc(pg_client, admin, kb, "Handbook", _HANDBOOK)
    salaries = await _doc(pg_client, admin, kb, "Salaries", _SALARIES)
    await pg_client.put(
        f"/api/v1/documents/{salaries['id']}/access",
        headers=_h(admin),
        json={"visibility": "RESTRICTED", "grants": []},
    )
    agent_id = await _agent(pg_client, admin, kb)

    as_member = await _search_run(pg_client, pg_sessions, member, agent_id)
    as_admin = await _search_run(pg_client, pg_sessions, admin, agent_id)
    refs = lambda run: {(r["type"], r["id"]) for r in run.sources}  # noqa: E731
    # The member's search filtered Salaries out, so it is not a source of their run.
    # Each run also read its person's conversation (M8.7).
    assert refs(as_member) == {
        ("knowledge_document", handbook["id"]),
        ("conversation", str(as_member.conversation_id)),
    }
    assert refs(as_admin) == {
        ("knowledge_document", handbook["id"]),
        ("knowledge_document", salaries["id"]),
        ("conversation", str(as_admin.conversation_id)),
    }
    assert as_member.acting_role == "MEMBER" and as_admin.acting_role == "ADMIN"
    for run in (as_member, as_admin):
        assert run.sources_truncated is False
        assert "120 million" not in json.dumps(run.sources)
