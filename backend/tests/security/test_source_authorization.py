# ruff: noqa: F811  (pytest fixtures imported from the M8.2 test module)
"""Milestone 8.7: read-time source authorization beyond the M8.2 contract tests.

Covers the reference types and read paths the contract tests do not exercise:
integrations, knowledge tables, conversations, mixed source sets, empty,
malformed and cyclic provenance, followed run references and their depth limit,
superusers, list endpoints, the operations overview and the approval execution
result. Every check is made through the API
with the viewer's permissions at read time.
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import update

from app.models.conversation import ConversationParticipant
from app.models.integration import IntegrationConnection
from app.models.knowledge import KnowledgeDocument, KnowledgeTable
from app.models.run import AgentRun
from app.models.user import User
from tests.agents.test_controlled_autonomy import calls, text
from tests.conftest import register_org
from tests.security.test_provenance import (  # noqa: F401 (fixtures)
    _canary,
    _doc_ref,
    _document,
    _escalated_run,
    _grant,
    _h,
    _join,
    _memory,
    _org,
    _read_run,
    _set_provenance,
    _visible,
    env,
    provider,
    runtime,
)


async def _connection(session_factory, org_id: str) -> str:
    async with session_factory() as s:
        conn = IntegrationConnection(
            organization_id=uuid.UUID(org_id), provider="webhook", name=f"Hook {uuid.uuid4().hex}"
        )
        s.add(conn)
        await s.commit()
        return str(conn.id)


async def _table(session_factory, document_id: str) -> str:
    async with session_factory() as s:
        doc = await s.get(KnowledgeDocument, uuid.UUID(document_id))
        assert doc is not None
        table = KnowledgeTable(
            organization_id=doc.organization_id,
            document_id=doc.id,
            knowledge_base_id=doc.knowledge_base_id,
            name="Sheet1",
        )
        s.add(table)
        await s.commit()
        return str(table.id)


async def test_integration_sources_follow_integration_use(client, env, provider, session_factory):
    owner, alice, viewer, agent_id = await _org(client, session_factory, "integ", "VIEWER")
    member = await _join(
        client, session_factory, owner["organization_id"], "m-integ@acme.example.com", "MEMBER"
    )
    connection = await _connection(session_factory, owner["organization_id"])
    reason = _canary("reason")
    run_id = await _escalated_run(client, provider, alice, agent_id, reason)
    await _set_provenance(session_factory, run_id, [{"type": "integration", "id": connection}])

    assert _visible(await _read_run(client, member, run_id), reason) is True
    denied = await _read_run(client, viewer, run_id)  # VIEWER lacks integration:use
    assert _visible(denied, reason) is False
    assert denied["content_withheld_reason"] == "restricted_sources"


async def test_a_knowledge_table_follows_its_document(client, env, provider, session_factory):
    owner, alice, other, agent_id = await _org(client, session_factory, "table", "MEMBER")
    doc = await _document(session_factory, owner["organization_id"], "Budget", restricted=True)
    await _grant(client, owner, doc, [alice["user"]["id"]])
    table = await _table(session_factory, doc)
    reason = _canary("reason")
    run_id = await _escalated_run(client, provider, alice, agent_id, reason)
    await _set_provenance(session_factory, run_id, [{"type": "knowledge_table", "id": table}])

    hidden = await _read_run(client, other, run_id)
    assert _visible(hidden, reason) is False
    assert hidden["content_withheld_reason"] == "restricted_sources"
    await _grant(client, owner, doc, [alice["user"]["id"], other["user"]["id"]])
    assert _visible(await _read_run(client, other, run_id), reason) is True


async def test_mixed_sources_need_every_one(client, env, provider, session_factory):
    owner, alice, other, agent_id = await _org(client, session_factory, "mixed", "MEMBER")
    org = owner["organization_id"]
    open_doc = await _document(session_factory, org, "Handbook", restricted=False)
    shared = await _memory(client, owner, "Office opens at 8", scope="ORGANIZATION")
    private = await _memory(client, alice, _canary("memory"))
    connection = await _connection(session_factory, org)
    readable = [
        _doc_ref(open_doc),
        {"type": "memory", "id": shared, "scope": "ORGANIZATION"},
        {"type": "integration", "id": connection},
        {"type": "external_input"},
    ]
    reason = _canary("reason")
    run_id = await _escalated_run(client, provider, alice, agent_id, reason)

    await _set_provenance(session_factory, run_id, readable)
    assert _visible(await _read_run(client, other, run_id), reason) is True

    await _set_provenance(
        session_factory, run_id, [*readable, {"type": "memory", "id": private, "scope": "USER"}]
    )
    restricted = await _read_run(client, other, run_id)
    assert _visible(restricted, reason) is False
    assert restricted["content_withheld_reason"] == "restricted_sources"

    # A source that cannot be resolved makes the provenance unknown.
    await _set_provenance(session_factory, run_id, [*readable, _doc_ref(str(uuid.uuid4()))])
    unknown = await _read_run(client, other, run_id)
    assert _visible(unknown, reason) is False
    assert unknown["content_withheld_reason"] == "unknown_provenance"


async def test_malformed_references_fail_closed(client, env, provider, session_factory):
    owner, alice, other, agent_id = await _org(client, session_factory, "bad", "MEMBER")
    open_doc = await _document(session_factory, owner["organization_id"], "Open", restricted=False)
    reason = _canary("reason")
    run_id = await _escalated_run(client, provider, alice, agent_id, reason)
    for bad in (
        {"type": "knowledge_document", "id": "not-a-uuid"},
        {"type": "made_up", "id": open_doc},
        {"type": "memory", "id": str(uuid.uuid4())},  # no scope
        {"type": "knowledge_document", "id": open_doc, "text": "copied content"},
    ):
        await _set_provenance(session_factory, run_id, [_doc_ref(open_doc), bad])
        body = await _read_run(client, other, run_id)
        assert _visible(body, reason) is False, bad
        assert body["content_withheld_reason"] == "unknown_provenance"


async def test_run_references_are_followed_and_cycles_fail_closed(
    client, env, provider, session_factory
):
    owner, alice, other, agent_id = await _org(client, session_factory, "nest", "MEMBER")
    org = owner["organization_id"]
    open_doc = await _document(session_factory, org, "Open", restricted=False)
    secret_doc = await _document(session_factory, org, "Secret", restricted=True)
    reason = _canary("reason")
    run_id = await _escalated_run(client, provider, alice, agent_id, reason)
    inner = await _escalated_run(client, provider, alice, agent_id, _canary("inner"))
    inner_ref = {"type": "agent_run", "id": inner}

    # The referenced run's own sources decide.
    await _set_provenance(session_factory, inner, [_doc_ref(open_doc)])
    await _set_provenance(session_factory, run_id, [inner_ref])
    assert _visible(await _read_run(client, other, run_id), reason) is True
    await _set_provenance(session_factory, inner, [_doc_ref(secret_doc)])
    assert (await _read_run(client, other, run_id))["content_withheld_reason"] == (
        "restricted_sources"
    )
    await _set_provenance(session_factory, inner, None)
    assert (await _read_run(client, other, run_id))["content_withheld_reason"] == (
        "unknown_provenance"
    )

    # A cycle can never be proven readable.
    await _set_provenance(session_factory, inner, [{"type": "agent_run", "id": run_id}])
    cyclic = await _read_run(client, other, run_id)
    assert _visible(cyclic, reason) is False
    assert cyclic["content_withheld_reason"] == "unknown_provenance"

    # A run of another organization is unknown, even when its sources are empty.
    stranger = await register_org(client, "stranger-nest@other.example.com", "Other Nest")
    theirs = await client.post(
        "/api/v1/agent-templates/executive-ai/instantiate", headers=_h(stranger)
    )
    foreign = await _escalated_run(client, provider, stranger, theirs.json()["id"], "x")
    await _set_provenance(session_factory, foreign, [])
    await _set_provenance(session_factory, run_id, [{"type": "agent_run", "id": foreign}])
    assert (await _read_run(client, other, run_id))["content_withheld_reason"] == (
        "unknown_provenance"
    )


async def test_superusers_get_knowledge_read_all_only(client, env, provider, session_factory):
    owner, alice, root, agent_id = await _org(client, session_factory, "root", "MEMBER")
    async with session_factory() as s:
        await s.execute(
            update(User).where(User.id == uuid.UUID(root["user"]["id"])).values(is_superuser=True)
        )
        await s.commit()
    restricted = await _document(
        session_factory, owner["organization_id"], "Salaries", restricted=True
    )
    await _grant(client, owner, restricted, [alice["user"]["id"]])
    reason = _canary("reason")
    run_id = await _escalated_run(client, provider, alice, agent_id, reason)

    await _set_provenance(session_factory, run_id, [_doc_ref(restricted)])
    assert _visible(await _read_run(client, root, run_id), reason) is True

    private = await _memory(client, alice, _canary("memory"))
    await _set_provenance(
        session_factory, run_id, [{"type": "memory", "id": private, "scope": "USER"}]
    )
    blocked = await _read_run(client, root, run_id)
    assert _visible(blocked, reason) is False
    assert blocked["content_withheld_reason"] == "restricted_sources"


async def test_list_endpoints_and_overview_apply_the_same_rule(
    client, env, provider, session_factory
):
    owner, alice, other, agent_id = await _org(client, session_factory, "lists", "MANAGER")
    org = owner["organization_id"]
    open_doc = await _document(session_factory, org, "Open", restricted=False)
    secret_doc = await _document(session_factory, org, "Secret", restricted=True)
    shown, hidden = _canary("shown"), _canary("hidden")
    run_shown = await _escalated_run(client, provider, alice, agent_id, shown)
    run_hidden = await _escalated_run(client, provider, alice, agent_id, hidden)
    await _set_provenance(session_factory, run_shown, [_doc_ref(open_doc)])
    await _set_provenance(session_factory, run_hidden, [_doc_ref(secret_doc)])

    listed = await client.get("/api/v1/runs", headers=_h(other))
    by_id = {item["id"]: item for item in listed.json()["items"]}
    assert by_id[run_shown]["escalation_reason"] == shown
    assert by_id[run_shown]["content_withheld_reason"] is None
    assert by_id[run_hidden]["escalation_reason"] is None
    assert by_id[run_hidden]["content_withheld_reason"] == "restricted_sources"

    overview = await client.get("/api/v1/operations/overview", headers=_h(other))
    assert overview.status_code == 200, overview.text
    reasons = {e["run_id"]: e["reason"] for e in overview.json()["recent_escalations"]}
    assert reasons[run_shown] == shown
    assert reasons[run_hidden] is None


async def test_approval_execution_result_follows_the_run_rule(
    client, env, provider, session_factory
):
    owner, alice, manager, agent_id = await _org(client, session_factory, "appr", "MANAGER")
    provider.queue(
        calls(("notify_member", {"recipient_email": "alice-appr@acme.example.com", "title": "Hi"}))
    )
    paused = await client.post(
        f"/api/v1/agents/{agent_id}/execute", headers=_h(alice), json={"message": "remind me"}
    )
    assert paused.json()["status"] == "awaiting_approval", paused.text
    answer = _canary("answer")
    provider.queue(text(answer))
    decided = await client.post(
        f"/api/v1/approvals/{paused.json()['approval_id']}/approve", headers=_h(manager)
    )
    assert decided.status_code == 200, decided.text
    # The run read Alice's conversation, which the approver takes no part in.
    execution = decided.json()["execution"]
    assert execution["content_withheld"] is True
    assert execution["content_withheld_reason"] == "restricted_sources"
    assert answer not in decided.text


# --------------------------------------------------------------------------- #
# Empty provenance, conversations and nesting depth
# --------------------------------------------------------------------------- #
async def test_empty_provenance_fails_closed(client, env, provider, session_factory):
    owner, alice, other, agent_id = await _org(client, session_factory, "empty", "ADMIN")
    reason = _canary("reason")
    run_id = await _escalated_run(client, provider, alice, agent_id, reason)
    await _set_provenance(session_factory, run_id, [])
    body = await _read_run(client, other, run_id)
    assert _visible(body, reason) is False
    assert body["content_withheld_reason"] == "unknown_provenance"
    assert _visible(await _read_run(client, alice, run_id), reason) is True


async def test_a_direct_run_is_readable_by_its_conversation_participants_only(
    client, env, provider, session_factory
):
    owner, alice, other, agent_id = await _org(client, session_factory, "conv", "MEMBER")
    reason = _canary("reason")
    run_id = await _escalated_run(client, provider, alice, agent_id, reason)
    async with session_factory() as s:
        run = await s.get(AgentRun, uuid.UUID(run_id))
    assert run is not None and run.conversation_id is not None
    # The run read Alice's conversation: recorded by reference, never its text.
    assert run.sources == [{"type": "conversation", "id": str(run.conversation_id)}]
    assert reason not in json.dumps(run.sources)

    hidden = await _read_run(client, other, run_id)
    assert _visible(hidden, reason) is False
    assert hidden["content_withheld_reason"] == "restricted_sources"
    # Joining the conversation (the conversation API's own rule) is what grants it.
    async with session_factory() as s:
        s.add(
            ConversationParticipant(
                organization_id=run.organization_id,
                conversation_id=run.conversation_id,
                user_id=uuid.UUID(other["user"]["id"]),
                role="member",
            )
        )
        await s.commit()
    assert _visible(await _read_run(client, other, run_id), reason) is True


async def test_run_references_deeper_than_the_limit_fail_closed(
    client, env, provider, session_factory
):
    from app.rbac.visibility import MAX_PROVENANCE_DEPTH

    owner, alice, other, agent_id = await _org(client, session_factory, "deep", "MEMBER")
    open_doc = await _document(session_factory, owner["organization_id"], "Open", restricted=False)
    reason = _canary("reason")
    top = await _escalated_run(client, provider, alice, agent_id, reason)
    chain = [await _escalated_run(client, provider, alice, agent_id, "x") for _ in range(6)]

    async def link(length: int) -> None:
        """top -> chain[0] -> ... -> chain[length - 1] -> open document."""
        for run_id, nxt in zip(chain[: length - 1], chain[1:length], strict=True):
            await _set_provenance(session_factory, run_id, [{"type": "agent_run", "id": nxt}])
        await _set_provenance(session_factory, chain[length - 1], [_doc_ref(open_doc)])
        await _set_provenance(session_factory, top, [{"type": "agent_run", "id": chain[0]}])

    await link(MAX_PROVENANCE_DEPTH)
    assert _visible(await _read_run(client, other, top), reason) is True
    await link(MAX_PROVENANCE_DEPTH + 1)
    deep = await _read_run(client, other, top)
    assert _visible(deep, reason) is False
    assert deep["content_withheld_reason"] == "unknown_provenance"
