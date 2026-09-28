"""Milestone 8: permission propagation and provenance (ADR-0037).

Contract: docs/architecture/milestone-8-provenance.md. These tests are written
before the implementation and fail until it lands.

- **Recording.** Real agent and workflow executions record *references* to the
  protected sources their content derives from, never the content itself.
- **Read rule.** A real run is executed, its provenance is set to a chosen list of
  references, and reads go through the API. Access to a source is granted and
  revoked through the real knowledge access API, so authorization is always
  decided at read time (invariant I8).

Knowledge sources are seeded as rows: the read rule needs documents and their
ACLs, not embeddings, so these tests run on SQLite. Retrieval-driven recording
is covered on PostgreSQL in tests/knowledge/test_provenance_recording.py.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, event, select, update

from app.agents.runtime import AgentRuntime, get_agent_runtime
from app.agents.tools.seed import seed_builtin_tools
from app.ai.gateway import AIGateway
from app.main import app
from app.models.audit import AuditLog
from app.models.knowledge import KnowledgeBase, KnowledgeDocument
from app.models.membership import OrganizationMember
from app.models.run import AgentRun
from app.models.workflow import WorkflowRun, WorkflowStepRun
from app.workflows.worker import tick
from tests.agents.test_controlled_autonomy import ScriptedProvider, calls, text
from tests.conftest import auth_headers, register_org

ROLES = ["VIEWER", "MEMBER", "MANAGER", "ADMIN"]


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #
@pytest.fixture
def provider() -> ScriptedProvider:
    return ScriptedProvider()


@pytest.fixture
def runtime(provider) -> AgentRuntime:
    gateway = AIGateway(
        providers={"scripted": provider},
        default_provider="scripted",
        default_model="scripted-1",
        max_retries=0,
    )
    return AgentRuntime(gateway=gateway)


@pytest_asyncio.fixture
async def env(client, session_factory, runtime):
    async with session_factory() as session:
        await seed_builtin_tools(session)
        await session.commit()
    app.dependency_overrides[get_agent_runtime] = lambda: runtime
    yield
    app.dependency_overrides.pop(get_agent_runtime, None)


def _canary(label: str) -> str:
    return f"CANARY-{label}-{uuid.uuid4().hex}"


async def _join(client, session_factory, org_id: str, email: str, role: str) -> dict:
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
    auth["_org"] = org_id
    return auth


def _h(auth: dict) -> dict[str, str]:
    return {**auth_headers(auth), "X-Organization-Id": auth.get("_org") or auth["organization_id"]}


async def _org(client, session_factory, tag: str, role: str):
    """Owner (ADMIN), Alice (MEMBER, the run's person), another member in `role`,
    and an Executive AI agent (it can recall memories)."""
    owner = await register_org(client, f"owner-{tag}@acme.example.com", f"Acme {tag}")
    org = owner["organization_id"]
    alice = await _join(client, session_factory, org, f"alice-{tag}@acme.example.com", "MEMBER")
    other = await _join(client, session_factory, org, f"other-{tag}@acme.example.com", role)
    agent = await client.post("/api/v1/agent-templates/executive-ai/instantiate", headers=_h(owner))
    assert agent.status_code in (200, 201), agent.text
    return owner, alice, other, agent.json()["id"]


async def _drain(session_factory, runtime) -> None:
    for _ in range(50):
        if await tick(session_factory, runtime) is None:
            return


async def _memory(client, auth, content: str, scope: str = "USER") -> str:
    resp = await client.post(
        "/api/v1/memories", headers=_h(auth), json={"content": content, "scope": scope}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _escalated_run(client, provider, alice, agent_id, reason: str) -> str:
    """A real agent run by Alice whose content (escalation_reason) holds `reason`."""
    provider.queue(calls(("escalate_to_human", {"reason": reason})))
    resp = await client.post(
        f"/api/v1/agents/{agent_id}/execute", headers=_h(alice), json={"message": "help"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["run_id"]


async def _set_provenance(session_factory, run_id: str, sources, truncated: bool = False):
    """Give a finished run a chosen provenance (M8 columns)."""
    async with session_factory() as s:
        await s.execute(
            update(AgentRun)
            .where(AgentRun.id == uuid.UUID(run_id))
            .values(sources=sources, sources_truncated=truncated)
        )
        await s.commit()


async def _document(session_factory, org_id: str, name: str, *, restricted: bool) -> str:
    """A knowledge document row in an organization-visible base (no ingestion needed:
    the read rule checks the ACL, not the text). Created by nobody, so no member
    reads it as its creator."""
    async with session_factory() as s:
        kb = (
            await s.execute(
                select(KnowledgeBase).where(
                    KnowledgeBase.organization_id == uuid.UUID(org_id),
                    KnowledgeBase.slug == "provenance",
                )
            )
        ).scalar_one_or_none()
        if kb is None:
            kb = KnowledgeBase(
                organization_id=uuid.UUID(org_id), name="Provenance", slug="provenance"
            )
            s.add(kb)
            await s.flush()
        doc = KnowledgeDocument(
            organization_id=uuid.UUID(org_id),
            knowledge_base_id=kb.id,
            name=name,
            checksum=hashlib.sha256(f"{name}-{uuid.uuid4()}".encode()).hexdigest(),
            status="READY",
            visibility="RESTRICTED" if restricted else "INHERIT",
        )
        s.add(doc)
        await s.commit()
        return str(doc.id)


async def _documents(session_factory, org_id: str, n: int) -> list[str]:
    """Many organization-visible documents in one insert (for the cost test)."""
    first = await _document(session_factory, org_id, "Doc 0", restricted=False)
    async with session_factory() as s:
        kb_id = (await s.get(KnowledgeDocument, uuid.UUID(first))).knowledge_base_id
        docs = [
            KnowledgeDocument(
                organization_id=uuid.UUID(org_id),
                knowledge_base_id=kb_id,
                name=f"Doc {i}",
                checksum=hashlib.sha256(f"doc-{i}-{uuid.uuid4()}".encode()).hexdigest(),
                status="READY",
            )
            for i in range(1, n)
        ]
        s.add_all(docs)
        await s.commit()
        return [first, *(str(d.id) for d in docs)]


async def _grant(client, owner, document_id: str, user_ids: list[str]) -> None:
    resp = await client.put(
        f"/api/v1/documents/{document_id}/access",
        headers=_h(owner),
        json={
            "visibility": "RESTRICTED",
            "grants": [{"principal_type": "USER", "principal": u} for u in user_ids],
        },
    )
    assert resp.status_code == 200, resp.text


def _doc_ref(document_id: str) -> dict:
    return {"type": "knowledge_document", "id": document_id}


def _refs(sources) -> set[tuple[str, str | None]]:
    return {(s["type"], s.get("id")) for s in sources or []}


async def _read_run(client, auth, run_id: str) -> dict:
    resp = await client.get(f"/api/v1/runs/{run_id}", headers=_h(auth))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _visible(body: dict, canary: str) -> bool:
    shown = canary in json.dumps(body)
    assert shown == (body["content_withheld"] is False), body
    return shown


# --------------------------------------------------------------------------- #
# Collector contract (I2, I4 at the source, bounds)
# --------------------------------------------------------------------------- #
def test_collector_keeps_references_only_and_is_bounded():
    from app.agents.provenance import MAX_SOURCES, ProvenanceCollector

    assert MAX_SOURCES == 500
    c = ProvenanceCollector()
    doc = str(uuid.uuid4())
    c.add({"type": "knowledge_document", "id": doc})
    c.add({"type": "knowledge_document", "id": doc})  # duplicates collapse
    assert c.sources == [{"type": "knowledge_document", "id": doc}]
    assert c.truncated is False

    # A reference carries a type and an id (plus a memory scope), never text.
    with pytest.raises(ValueError):
        c.add({"type": "knowledge_document", "id": doc, "content": "secret text"})
    with pytest.raises(ValueError):
        c.add({"type": "made_up", "id": doc})

    for _ in range(MAX_SOURCES):
        c.add({"type": "knowledge_document", "id": str(uuid.uuid4())})
    assert len(c.sources) == MAX_SOURCES
    assert c.truncated is True


# --------------------------------------------------------------------------- #
# Recording: real executions
# --------------------------------------------------------------------------- #
async def test_agent_run_records_recalled_memories_by_reference(
    client, env, provider, session_factory
):
    owner, alice, _, agent_id = await _org(client, session_factory, "rec", "MEMBER")
    secret = _canary("memory")
    mine = await _memory(client, alice, secret)
    bob = await _join(
        client, session_factory, owner["organization_id"], "bob-rec@acme.example.com", "MEMBER"
    )
    await _memory(client, bob, _canary("bob"))  # never returned to Alice, never recorded

    provider.queue(calls(("recall_memories", {})), text("Noted."))
    resp = await client.post(
        f"/api/v1/agents/{agent_id}/execute", headers=_h(alice), json={"message": "hi"}
    )
    run_id = uuid.UUID(resp.json()["run_id"])
    async with session_factory() as s:
        run = await s.get(AgentRun, run_id)
    assert run is not None
    assert _refs(run.sources) == {("memory", mine)}
    [ref] = run.sources
    assert ref["scope"] == "USER"
    assert run.sources_truncated is False
    assert run.acting_role == "MEMBER"
    # Every provenance field of the run (I2: no content text anywhere in sources).
    assert str(run.organization_id) == owner["organization_id"]
    assert str(run.initiated_by) == alice["user"]["id"]
    assert str(run.agent_id) == agent_id and run.agent_version_id is not None
    assert run.created_at is not None and run.completed_at is not None
    assert secret not in json.dumps(run.sources)


async def test_persistent_memory_injection_is_recorded(client, env, provider, session_factory):
    owner, alice, _, _ = await _org(client, session_factory, "pers", "MEMBER")
    created = await client.post(
        "/api/v1/agents",
        headers=_h(owner),
        json={"name": "Keeper", "system_instructions": "Help.", "memory_mode": "PERSISTENT"},
    )
    agent_id = created.json()["id"]
    version = await client.post(f"/api/v1/agents/{agent_id}/versions", headers=_h(owner), json={})
    await client.post(
        f"/api/v1/agents/{agent_id}/versions/{version.json()['id']}/activate", headers=_h(owner)
    )
    secret = _canary("memory")
    mine = await _memory(client, alice, secret)
    provider.queue(text("Hello."))
    resp = await client.post(
        f"/api/v1/agents/{agent_id}/execute", headers=_h(alice), json={"message": "hi"}
    )
    # The memory reached the model (system prompt), so it is a source of this run.
    assert secret in provider.requests[0].model_dump_json()
    async with session_factory() as s:
        run = await s.get(AgentRun, uuid.UUID(resp.json()["run_id"]))
    assert run is not None and ("memory", mine) in _refs(run.sources)
    assert secret not in json.dumps(run.sources)


async def test_workflow_records_transitive_provenance_and_attributes_publication(
    client, env, provider, runtime, session_factory
):
    owner, alice, _, agent_id = await _org(client, session_factory, "wfrec", "MEMBER")
    secret = _canary("memory")
    mine = await _memory(client, alice, secret)
    workflow = await client.post(
        "/api/v1/workflows",
        headers=_h(owner),
        json={
            "name": "Brief",
            "definition": {
                "steps": [
                    {
                        "id": "brief",
                        "type": "agent",
                        "agent_id": agent_id,
                        "input": "Brief {{ input.ref }}",
                    },
                    {
                        "id": "log",
                        "type": "tool",
                        "tool": "create_task",
                        "arguments": {
                            "title": "Follow up",
                            "description": "{{ steps.brief.output.text }}",
                        },
                    },
                ]
            },
        },
    )
    workflow_id = workflow.json()["id"]
    await client.post(f"/api/v1/workflows/{workflow_id}/activate", headers=_h(owner))
    provider.queue(calls(("recall_memories", {})), text(f"Your note: {secret}"))
    started = await client.post(
        f"/api/v1/workflows/{workflow_id}/runs", headers=_h(alice), json={"input": {"ref": "R-1"}}
    )
    run_id = uuid.UUID(started.json()["id"])
    await _drain(session_factory, runtime)

    async with session_factory() as s:
        run = await s.get(WorkflowRun, run_id)
        steps = {
            st.step_id: st
            for st in (
                await s.execute(select(WorkflowStepRun).where(WorkflowStepRun.run_id == run_id))
            ).scalars()
        }
        audit = (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.action == "workflow.tool_executed",
                    AuditLog.target_id == str(run_id),
                )
            )
        ).scalar_one()
    assert run is not None and run.status == "COMPLETED"
    # The agent step carries its agent run's sources; the templated tool step
    # inherits them (it read steps.brief); the workflow run accumulates everything.
    assert ("memory", mine) in _refs(steps["brief"].sources)
    assert ("memory", mine) in _refs(steps["log"].sources)
    assert {("memory", mine), ("external_input", None)} <= _refs(run.sources)
    assert (steps["brief"].sources_truncated, steps["log"].sources_truncated) == (False, False)
    assert run.sources_truncated is False
    assert run.acting_role == "MEMBER"
    assert str(run.initiated_by) == alice["user"]["id"]
    assert run.workflow_id is not None and run.version_id is not None
    for row in (run, steps["brief"], steps["log"]):
        assert secret not in json.dumps(row.sources)
    # Publication attribution: the audit event says a restricted-derived tool ran.
    assert audit.metadata_json["restricted"] is True
    assert audit.metadata_json["source_counts"]["memory"] >= 1
    assert secret not in json.dumps(audit.metadata_json)


async def test_truncation_propagates_to_steps_and_the_workflow_run(
    client, env, provider, runtime, session_factory, monkeypatch
):
    import app.agents.provenance as provenance

    monkeypatch.setattr(provenance, "MAX_SOURCES", 1)
    owner, alice, other, agent_id = await _org(client, session_factory, "trunc", "MEMBER")
    # Two organization memories: readable by everyone, but more than the limit.
    await _memory(client, owner, "Office opens at 8", scope="ORGANIZATION")
    await _memory(client, owner, "Payroll runs on the 25th", scope="ORGANIZATION")
    workflow = await client.post(
        "/api/v1/workflows",
        headers=_h(owner),
        json={
            "name": "Digest",
            "definition": {
                "steps": [
                    {"id": "digest", "type": "agent", "agent_id": agent_id, "input": "Digest"}
                ]
            },
        },
    )
    workflow_id = workflow.json()["id"]
    await client.post(f"/api/v1/workflows/{workflow_id}/activate", headers=_h(owner))
    marker = _canary("digest")
    provider.queue(calls(("recall_memories", {})), text(marker))
    started = await client.post(
        f"/api/v1/workflows/{workflow_id}/runs", headers=_h(alice), json={"input": {}}
    )
    run_id = started.json()["id"]
    await _drain(session_factory, runtime)

    async with session_factory() as s:
        run = await s.get(WorkflowRun, uuid.UUID(run_id))
        [step] = (
            await s.execute(
                select(WorkflowStepRun).where(WorkflowStepRun.run_id == uuid.UUID(run_id))
            )
        ).scalars()
        agent_run = await s.get(AgentRun, step.agent_run_id)
    assert agent_run is not None and agent_run.sources_truncated is True
    assert step.sources_truncated is True
    assert run is not None and run.sources_truncated is True

    # Fail closed (I3): truncated provenance hides content from everyone but Alice.
    theirs = (await client.get(f"/api/v1/workflow-runs/{run_id}", headers=_h(other))).json()
    assert theirs["content_withheld"] is True
    assert theirs["content_withheld_reason"] == "unknown_provenance"
    assert marker not in json.dumps(theirs)
    mine = (await client.get(f"/api/v1/workflow-runs/{run_id}", headers=_h(alice))).json()
    assert mine["content_withheld"] is False and marker in json.dumps(mine)


# --------------------------------------------------------------------------- #
# Read rule through workflow runs (widening, restricted, pre-M8)
# --------------------------------------------------------------------------- #
async def _org_memory_workflow(client, provider, runtime, session_factory, owner, alice, agent_id):
    await _memory(client, owner, "Office opens at 8", scope="ORGANIZATION")
    workflow = await client.post(
        "/api/v1/workflows",
        headers=_h(owner),
        json={
            "name": "Digest",
            "definition": {
                "steps": [
                    {"id": "digest", "type": "agent", "agent_id": agent_id, "input": "Digest"}
                ]
            },
        },
    )
    workflow_id = workflow.json()["id"]
    await client.post(f"/api/v1/workflows/{workflow_id}/activate", headers=_h(owner))
    marker = _canary("digest")
    provider.queue(calls(("recall_memories", {})), text(marker))
    started = await client.post(
        f"/api/v1/workflows/{workflow_id}/runs", headers=_h(alice), json={"input": {"week": 39}}
    )
    await _drain(session_factory, runtime)
    return started.json()["id"], marker


@pytest.mark.parametrize("role", ROLES)
async def test_content_from_organization_sources_is_visible_to_the_organization(
    client, env, provider, runtime, session_factory, role
):
    owner, alice, other, agent_id = await _org(client, session_factory, f"wide-{role}", role)
    run_id, marker = await _org_memory_workflow(
        client, provider, runtime, session_factory, owner, alice, agent_id
    )
    mine = (await client.get(f"/api/v1/workflow-runs/{run_id}", headers=_h(alice))).json()
    assert mine["status"] == "COMPLETED" and marker in json.dumps(mine)
    body = (await client.get(f"/api/v1/workflow-runs/{run_id}", headers=_h(other))).json()
    assert body["content_withheld"] is False, body
    assert body["content_withheld_reason"] is None
    assert marker in json.dumps(body)
    assert body["input"] == {"week": 39}


@pytest.mark.parametrize("role", ROLES)
async def test_content_from_a_private_memory_stays_with_its_person(
    client, env, provider, runtime, session_factory, role
):
    owner, alice, other, agent_id = await _org(client, session_factory, f"priv-{role}", role)
    secret = _canary("memory")
    await _memory(client, alice, secret)
    workflow = await client.post(
        "/api/v1/workflows",
        headers=_h(owner),
        json={
            "name": "Brief",
            "definition": {
                "steps": [{"id": "brief", "type": "agent", "agent_id": agent_id, "input": "Hi"}]
            },
        },
    )
    workflow_id = workflow.json()["id"]
    await client.post(f"/api/v1/workflows/{workflow_id}/activate", headers=_h(owner))
    provider.queue(calls(("recall_memories", {})), text(f"Your note: {secret}"))
    started = await client.post(
        f"/api/v1/workflows/{workflow_id}/runs", headers=_h(alice), json={"input": {}}
    )
    run_id = started.json()["id"]
    await _drain(session_factory, runtime)

    body = (await client.get(f"/api/v1/workflow-runs/{run_id}", headers=_h(other))).json()
    assert body["content_withheld"] is True
    assert body["content_withheld_reason"] == "restricted_sources"
    assert secret not in json.dumps(body)
    mine = (await client.get(f"/api/v1/workflow-runs/{run_id}", headers=_h(alice))).json()
    assert secret in json.dumps(mine)


async def test_runs_without_provenance_fail_closed(client, env, provider, runtime, session_factory):
    owner, alice, other, agent_id = await _org(client, session_factory, "prem8", "MEMBER")
    run_id, marker = await _org_memory_workflow(
        client, provider, runtime, session_factory, owner, alice, agent_id
    )
    # A run recorded before M8 has no provenance at all.
    async with session_factory() as s:
        await s.execute(
            update(WorkflowRun).where(WorkflowRun.id == uuid.UUID(run_id)).values(sources=None)
        )
        await s.commit()
    body = (await client.get(f"/api/v1/workflow-runs/{run_id}", headers=_h(other))).json()
    assert body["content_withheld"] is True
    assert body["content_withheld_reason"] == "unknown_provenance"
    assert marker not in json.dumps(body)
    mine = (await client.get(f"/api/v1/workflow-runs/{run_id}", headers=_h(alice))).json()
    assert marker in json.dumps(mine)


# --------------------------------------------------------------------------- #
# Read rule through agent runs: current-time authorization and fail-closed cases
# --------------------------------------------------------------------------- #
async def test_revoking_access_to_a_source_hides_content_seen_before(
    client, env, provider, session_factory
):
    owner, alice, other, agent_id = await _org(client, session_factory, "revoke", "MEMBER")
    doc = await _document(
        session_factory, owner["organization_id"], "Board minutes", restricted=True
    )
    await _grant(client, owner, doc, [alice["user"]["id"], other["user"]["id"]])
    reason = _canary("reason")
    run_id = await _escalated_run(client, provider, alice, agent_id, reason)
    await _set_provenance(session_factory, run_id, [_doc_ref(doc)])

    assert _visible(await _read_run(client, other, run_id), reason) is True
    await _grant(client, owner, doc, [alice["user"]["id"]])  # revoke the other member
    after = await _read_run(client, other, run_id)
    assert _visible(after, reason) is False
    assert after["content_withheld_reason"] == "restricted_sources"
    assert _visible(await _read_run(client, alice, run_id), reason) is True


async def test_granting_every_source_later_reveals_content_hidden_before(
    client, env, provider, session_factory
):
    owner, alice, other, agent_id = await _org(client, session_factory, "grant", "MEMBER")
    doc_a = await _document(session_factory, owner["organization_id"], "Plan A", restricted=True)
    doc_b = await _document(session_factory, owner["organization_id"], "Plan B", restricted=True)
    await _grant(client, owner, doc_a, [alice["user"]["id"]])
    await _grant(client, owner, doc_b, [alice["user"]["id"]])
    reason = _canary("reason")
    run_id = await _escalated_run(client, provider, alice, agent_id, reason)
    await _set_provenance(session_factory, run_id, [_doc_ref(doc_a), _doc_ref(doc_b)])

    assert _visible(await _read_run(client, other, run_id), reason) is False
    await _grant(client, owner, doc_a, [alice["user"]["id"], other["user"]["id"]])
    # One of two sources is not enough.
    assert _visible(await _read_run(client, other, run_id), reason) is False
    await _grant(client, owner, doc_b, [alice["user"]["id"], other["user"]["id"]])
    assert _visible(await _read_run(client, other, run_id), reason) is True


async def test_deleted_and_foreign_sources_fail_closed(client, env, provider, session_factory):
    owner, alice, other, agent_id = await _org(client, session_factory, "gone", "ADMIN")
    open_doc = await _document(
        session_factory, owner["organization_id"], "Handbook", restricted=False
    )
    reason = _canary("reason")
    run_id = await _escalated_run(client, provider, alice, agent_id, reason)
    await _set_provenance(session_factory, run_id, [_doc_ref(open_doc)])
    assert _visible(await _read_run(client, other, run_id), reason) is True

    # A deleted source can no longer be checked (I3).
    async with session_factory() as s:
        await s.execute(
            delete(KnowledgeDocument).where(KnowledgeDocument.id == uuid.UUID(open_doc))
        )
        await s.commit()
    gone = await _read_run(client, other, run_id)
    assert _visible(gone, reason) is False
    assert gone["content_withheld_reason"] == "unknown_provenance"

    # A reference into another organization never satisfies the check (I6), even
    # for an admin with knowledge:read_all.
    stranger = await register_org(client, "stranger@other.example.com", "Other Org")
    foreign = await _document(
        session_factory, stranger["organization_id"], "Theirs", restricted=False
    )
    await _set_provenance(session_factory, run_id, [_doc_ref(foreign)])
    crossed = await _read_run(client, other, run_id)
    assert _visible(crossed, reason) is False
    assert crossed["content_withheld_reason"] == "unknown_provenance"
    assert _visible(await _read_run(client, alice, run_id), reason) is True


async def test_truncated_or_missing_provenance_fails_closed_for_agent_runs(
    client, env, provider, session_factory
):
    owner, alice, other, agent_id = await _org(client, session_factory, "trag", "MEMBER")
    open_doc = await _document(
        session_factory, owner["organization_id"], "Handbook", restricted=False
    )
    reason = _canary("reason")
    run_id = await _escalated_run(client, provider, alice, agent_id, reason)

    await _set_provenance(session_factory, run_id, [_doc_ref(open_doc)], truncated=True)
    truncated = await _read_run(client, other, run_id)
    assert _visible(truncated, reason) is False
    assert truncated["content_withheld_reason"] == "unknown_provenance"

    await _set_provenance(session_factory, run_id, None)
    missing = await _read_run(client, other, run_id)
    assert _visible(missing, reason) is False
    assert missing["content_withheld_reason"] == "unknown_provenance"


async def test_admins_get_no_bypass_for_private_sources(client, env, provider, session_factory):
    owner, alice, admin, agent_id = await _org(client, session_factory, "adm", "ADMIN")
    member = await _join(
        client, session_factory, owner["organization_id"], "mem-adm@acme.example.com", "MEMBER"
    )
    restricted = await _document(
        session_factory, owner["organization_id"], "Salaries", restricted=True
    )
    await _grant(client, owner, restricted, [alice["user"]["id"]])
    reason = _canary("reason")
    run_id = await _escalated_run(client, provider, alice, agent_id, reason)

    # knowledge:read_all is the one documented exception, for knowledge sources.
    await _set_provenance(session_factory, run_id, [_doc_ref(restricted)])
    assert _visible(await _read_run(client, admin, run_id), reason) is True
    assert _visible(await _read_run(client, member, run_id), reason) is False

    # A private memory has no such exception (I5). The reference's scope label is
    # not trusted: the memory row decides.
    private = await _memory(client, alice, _canary("memory"))
    await _set_provenance(
        session_factory, run_id, [{"type": "memory", "id": private, "scope": "ORGANIZATION"}]
    )
    blocked = await _read_run(client, admin, run_id)
    assert _visible(blocked, reason) is False
    assert blocked["content_withheld_reason"] == "restricted_sources"


async def test_read_cost_does_not_grow_with_the_number_of_sources(
    client, env, provider, session_factory, db_engine
):
    owner, alice, other, agent_id = await _org(client, session_factory, "cost", "MEMBER")
    org = owner["organization_id"]
    few = [await _document(session_factory, org, "Only", restricted=False)]
    many = await _documents(session_factory, org, 500)
    run_few = await _escalated_run(client, provider, alice, agent_id, _canary("few"))
    run_many = await _escalated_run(client, provider, alice, agent_id, _canary("many"))
    await _set_provenance(session_factory, run_few, [_doc_ref(d) for d in few])
    await _set_provenance(session_factory, run_many, [_doc_ref(d) for d in many])

    counts: list[int] = []

    async def statements(run_id: str) -> int:
        seen: list[str] = []

        def record(conn, cursor, statement, *args):
            seen.append(statement)

        event.listen(db_engine.sync_engine, "before_cursor_execute", record)
        try:
            body = await _read_run(client, other, run_id)
        finally:
            event.remove(db_engine.sync_engine, "before_cursor_execute", record)
        assert body["content_withheld"] is False
        return len(seen)

    counts = [await statements(run_few), await statements(run_many)]
    assert counts[0] == counts[1], counts


# --------------------------------------------------------------------------- #
# Guards that already hold and must keep holding (I4, attribution)
# --------------------------------------------------------------------------- #
async def test_provenance_cannot_be_supplied_through_the_api(client, env, session_factory):
    owner, alice, _, agent_id = await _org(client, session_factory, "i4", "MEMBER")
    injected = [{"type": "knowledge_document", "id": str(uuid.uuid4())}]
    execute = await client.post(
        f"/api/v1/agents/{agent_id}/execute",
        headers=_h(alice),
        json={"message": "hi", "sources": injected},
    )
    assert execute.status_code == 422
    workflow = await client.post(
        "/api/v1/workflows",
        headers=_h(owner),
        json={
            "name": "Guard",
            "definition": {"steps": [{"id": "t", "type": "tool", "tool": "list_tasks"}]},
        },
    )
    workflow_id = workflow.json()["id"]
    await client.post(f"/api/v1/workflows/{workflow_id}/activate", headers=_h(owner))
    for extra in ({"sources": injected}, {"sources_truncated": False}):
        start = await client.post(
            f"/api/v1/workflows/{workflow_id}/runs",
            headers=_h(alice),
            json={"input": {}, **extra},
        )
        assert start.status_code == 422, start.text


async def test_agent_publications_keep_their_source_run(client, env, provider, session_factory):
    owner, alice, _, agent_id = await _org(client, session_factory, "pub", "MEMBER")
    provider.queue(
        calls(("create_task", {"title": "Call the supplier"})),
        text("Done."),
    )
    resp = await client.post(
        f"/api/v1/agents/{agent_id}/execute", headers=_h(alice), json={"message": "task"}
    )
    run_id = resp.json()["run_id"]
    from app.models.work import Task

    async with session_factory() as s:
        task = (await s.execute(select(Task).where(Task.title == "Call the supplier"))).scalar_one()
    assert str(task.source_run_id) == run_id
