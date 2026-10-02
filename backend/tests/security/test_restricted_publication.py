# ruff: noqa: F811  (pytest fixtures imported from the M8.2 test module)
"""Milestone 9: approval before publishing restricted-derived content (ADR-0038).

Contract: docs/architecture/milestone-9-restricted-publication.md. These tests are
written before the implementation and fail until it lands (M9.3–M9.7). The
"setting off" tests (P1) pass today: they guard that nothing changes for
organizations that leave the setting off.

Restriction is decided from Milestone 8 provenance:
- **Agent runs started through the API** record the conversation they read. A
  conversation is private to its participants, so such publications are restricted.
  An approver becomes eligible for one by joining the conversation, which is the
  conversation API's own rule.
- **Workflow tool steps** inherit what they consume. The tests give an upstream
  approval step a chosen provenance, as the M8 tests do. A restricted knowledge
  document and its grants then decide eligibility, through the real access API.

Every read and decision goes through the API.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, select, update

from app.models.approval import Approval
from app.models.audit import AuditLog
from app.models.conversation import ConversationParticipant
from app.models.knowledge import KnowledgeDocument
from app.models.membership import OrganizationMember
from app.models.work import Task
from app.models.workflow import WorkflowStepRun
from tests.agents.test_controlled_autonomy import calls, text
from tests.conftest import register_org
from tests.security.test_provenance import (  # noqa: F401 (fixtures)
    _canary,
    _doc_ref,
    _document,
    _drain,
    _escalated_run,
    _grant,
    _h,
    _join,
    _memory,
    _set_provenance,
    env,
    provider,
    runtime,
)

SETTING = "require_approval_to_publish_restricted"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
async def _team(client, session_factory, tag: str) -> dict:
    """Owner (ADMIN), Alice (MEMBER, the run's person), Mia and Max (MANAGER:
    approvers), Vic (VIEWER), and an Executive AI agent."""
    owner = await register_org(client, f"owner-{tag}@acme.example.com", f"Acme {tag}")
    org = owner["organization_id"]
    team = {"owner": owner, "org": org}
    for name, role in (
        ("alice", "MEMBER"),
        ("mia", "MANAGER"),
        ("max", "MANAGER"),
        ("vic", "VIEWER"),
    ):
        team[name] = await _join(
            client, session_factory, org, f"{name}-{tag}@acme.example.com", role
        )
    agent = await client.post("/api/v1/agent-templates/executive-ai/instantiate", headers=_h(owner))
    assert agent.status_code in (200, 201), agent.text
    team["agent_id"] = agent.json()["id"]
    return team


async def _enable(client, owner, on: bool = True) -> None:
    """Turn the M9 organization setting on (or off) through the settings API."""
    resp = await client.patch(
        "/api/v1/organizations/current", headers=_h(owner), json={SETTING: on}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json().get(SETTING) is on, f"M9: the organization setting {SETTING} is missing"


def _uid(auth: dict) -> uuid.UUID:
    return uuid.UUID(auth["user"]["id"])


async def _execute(client, auth, agent_id: str, message: str = "go") -> dict:
    resp = await client.post(
        f"/api/v1/agents/{agent_id}/execute", headers=_h(auth), json={"message": message}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _participant(session_factory, conversation_id: str, auth: dict, *, add: bool) -> None:
    """Join or leave a conversation: the conversation API's participant rule."""
    async with session_factory() as s:
        if add:
            s.add(
                ConversationParticipant(
                    organization_id=uuid.UUID(auth["_org"]),
                    conversation_id=uuid.UUID(conversation_id),
                    user_id=_uid(auth),
                    role="member",
                )
            )
        else:
            await s.execute(
                delete(ConversationParticipant).where(
                    ConversationParticipant.conversation_id == uuid.UUID(conversation_id),
                    ConversationParticipant.user_id == _uid(auth),
                )
            )
        await s.commit()


async def _approvals(session_factory, run_id: str) -> list[Approval]:
    async with session_factory() as s:
        rows = await s.execute(select(Approval).where(Approval.run_id == uuid.UUID(run_id)))
        return list(rows.scalars())


async def _tasks(session_factory, title: str) -> list[Task]:
    async with session_factory() as s:
        return list((await s.execute(select(Task).where(Task.title == title))).scalars())


async def _audit(session_factory, action: str, target_id: str) -> list[AuditLog]:
    async with session_factory() as s:
        rows = await s.execute(
            select(AuditLog).where(AuditLog.action == action, AuditLog.target_id == target_id)
        )
        return list(rows.scalars())


async def _agent_publication(client, provider, team, person: str, title: str) -> dict:
    """A direct agent run by `person` that calls create_task(title)."""
    provider.queue(calls(("create_task", {"title": title})), text("Done."))
    return await _execute(client, team[person], team["agent_id"])


def _gated(result: dict) -> str:
    """The approval a restricted agent publication must wait for (M9)."""
    assert result["status"] == "awaiting_approval" and result.get("approval_id"), (
        f"M9: a restricted publication must wait for approval, got {result['status']}"
    )
    return result["approval_id"]


async def _approve(client, auth, approval_id: str):
    return await client.post(f"/api/v1/approvals/{approval_id}/approve", headers=_h(auth))


async def _reject(client, auth, approval_id: str):
    return await client.post(f"/api/v1/approvals/{approval_id}/reject", headers=_h(auth))


# Workflows: an approval step `check` whose provenance a test sets, then a
# publication step `log` that consumes it and the run's input.
async def _workflow(
    client,
    owner,
    *,
    tool: str = "create_task",
    require_approval: bool = False,
    recipient: str | None = None,
) -> str:
    if tool == "notify_member":
        arguments = {
            "recipient_email": recipient,
            "title": "{{ input.secret }}",
            "body": "After {{ steps.check.output.decision }}",
        }
    else:
        arguments = {
            "title": "{{ input.secret }}",
            "description": "After {{ steps.check.output.decision }}",
        }
    log: dict[str, Any] = {"id": "log", "type": "tool", "tool": tool, "arguments": arguments}
    if require_approval:
        log["require_approval"] = True
    created = await client.post(
        "/api/v1/workflows",
        headers=_h(owner),
        json={
            "name": f"Publish {uuid.uuid4().hex[:6]}",
            "definition": {
                "steps": [{"id": "check", "type": "approval", "title": "Prepare?"}, log]
            },
        },
    )
    assert created.status_code == 201, created.text
    workflow_id = created.json()["id"]
    activated = await client.post(f"/api/v1/workflows/{workflow_id}/activate", headers=_h(owner))
    assert activated.status_code == 200, activated.text
    return workflow_id


async def _to_publication(
    client,
    session_factory,
    runtime,
    team,
    workflow_id: str,
    secret: str,
    sources,
    truncated: bool = False,
    person: str = "alice",
) -> str:
    """`person` (Alice by default) starts the run; `check` gets the chosen
    provenance and is approved by the owner; the run then reaches the `log`
    publication step."""
    started = await client.post(
        f"/api/v1/workflows/{workflow_id}/runs",
        headers=_h(team[person]),
        json={"input": {"secret": secret}},
    )
    assert started.status_code == 202, started.text
    run_id = started.json()["id"]
    await _drain(session_factory, runtime)
    async with session_factory() as s:
        await s.execute(
            update(WorkflowStepRun)
            .where(WorkflowStepRun.run_id == uuid.UUID(run_id), WorkflowStepRun.step_id == "check")
            .values(sources=sources, sources_truncated=truncated)
        )
        await s.commit()
    decided = await client.post(
        f"/api/v1/workflow-runs/{run_id}/approve", headers=_h(team["owner"])
    )
    assert decided.status_code == 200, decided.text
    await _drain(session_factory, runtime)
    return run_id


async def _run(client, auth, run_id: str) -> dict:
    resp = await client.get(f"/api/v1/workflow-runs/{run_id}", headers=_h(auth))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _waiting(client, team, run_id: str) -> dict:
    """A workflow run held at its restricted publication step (M9)."""
    body = await _run(client, team["alice"], run_id)
    assert body["status"] == "WAITING", (
        f"M9: a restricted workflow publication must wait for approval, got {body['status']}"
    )
    return body


def _step(body: dict, step_id: str) -> dict:
    return [s for s in body["steps"] if s["step_id"] == step_id][-1]


async def _restricted_doc(session_factory, client, team, *, readers: list[str]) -> str:
    doc = await _document(session_factory, team["org"], "Board minutes", restricted=True)
    await _grant(client, team["owner"], doc, [team[r]["user"]["id"] for r in readers])
    return doc


# --------------------------------------------------------------------------- #
# P1: the setting defaults to off, and off changes nothing
# --------------------------------------------------------------------------- #
async def test_the_setting_exists_and_defaults_to_off(client, env, session_factory):
    team = await _team(client, session_factory, "default")
    body = (await client.get("/api/v1/organizations/current", headers=_h(team["owner"]))).json()
    assert body.get(SETTING) is False, f"M9: {SETTING} must exist and default to false"


async def test_setting_off_agent_publication_is_unchanged(client, env, provider, session_factory):
    team = await _team(client, session_factory, "off-agent")
    title = _canary("task")
    result = await _agent_publication(client, provider, team, "alice", title)
    assert result["status"] == "completed", result
    assert len(await _tasks(session_factory, title)) == 1
    assert await _approvals(session_factory, result["run_id"]) == []


async def test_setting_off_workflow_publication_is_unchanged(client, env, runtime, session_factory):
    team = await _team(client, session_factory, "off-wf")
    doc = await _restricted_doc(session_factory, client, team, readers=["alice"])
    workflow_id = await _workflow(client, team["owner"])
    secret = _canary("secret")
    run_id = await _to_publication(
        client, session_factory, runtime, team, workflow_id, secret, [_doc_ref(doc)]
    )
    body = await _run(client, team["alice"], run_id)
    assert body["status"] == "COMPLETED", body
    assert len(await _tasks(session_factory, secret)) == 1


# --------------------------------------------------------------------------- #
# P2: with the setting on, a restricted publication waits for approval
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tool", ["create_task", "notify_member", "save_agent_memory"])
async def test_restricted_agent_publication_waits_for_approval(
    client, env, provider, session_factory, tool
):
    team = await _team(client, session_factory, f"gate-{tool}")
    await _enable(client, team["owner"])
    secret = _canary("secret")
    args = {
        "create_task": {"title": secret},
        "notify_member": {"recipient_email": f"max-gate-{tool}@acme.example.com", "title": secret},
        "save_agent_memory": {"content": secret},
    }[tool]
    provider.queue(calls((tool, args)), text("Done."))
    result = await _execute(client, team["alice"], team["agent_id"])
    # The run read Alice's conversation (private), so the publication is restricted.
    assert result["status"] == "awaiting_approval", result
    [approval] = await _approvals(session_factory, result["run_id"])
    assert approval.tool_name == tool and approval.status == "PENDING"
    assert approval.restricted_publication is True
    assert await _tasks(session_factory, secret) == []


@pytest.mark.parametrize("tool", ["create_task", "notify_member"])
async def test_restricted_workflow_publication_waits_for_approval(
    client, env, runtime, session_factory, tool
):
    team = await _team(client, session_factory, f"gate-wf-{tool}")
    await _enable(client, team["owner"])
    doc = await _restricted_doc(session_factory, client, team, readers=["alice"])
    workflow_id = await _workflow(
        client, team["owner"], tool=tool, recipient=f"max-gate-wf-{tool}@acme.example.com"
    )
    secret = _canary("secret")
    run_id = await _to_publication(
        client, session_factory, runtime, team, workflow_id, secret, [_doc_ref(doc)]
    )
    body = await _run(client, team["alice"], run_id)
    assert body["status"] == "WAITING", body
    log = _step(body, "log")
    assert log["status"] == "WAITING" and log["restricted_publication"] is True
    assert await _tasks(session_factory, secret) == []


# --------------------------------------------------------------------------- #
# P3: organization-readable provenance is not gated
# --------------------------------------------------------------------------- #
async def test_organization_readable_workflow_publication_is_not_gated(
    client, env, runtime, session_factory
):
    team = await _team(client, session_factory, "open-wf")
    await _enable(client, team["owner"])
    handbook = await _document(session_factory, team["org"], "Handbook", restricted=False)
    workflow_id = await _workflow(client, team["owner"])
    secret = _canary("secret")
    run_id = await _to_publication(
        client, session_factory, runtime, team, workflow_id, secret, [_doc_ref(handbook)]
    )
    body = await _run(client, team["alice"], run_id)
    assert body["status"] == "COMPLETED", body
    assert _step(body, "log").get("restricted_publication") is False
    assert len(await _tasks(session_factory, secret)) == 1


async def test_organization_readable_agent_publication_is_not_gated(
    client, env, provider, runtime, session_factory
):
    """A workflow agent step reads only organization sources (its input and an
    organization memory); its own create_task call is not restricted."""
    team = await _team(client, session_factory, "open-agent")
    await _enable(client, team["owner"])
    await _memory(client, team["owner"], "Office opens at 8", scope="ORGANIZATION")
    created = await client.post(
        "/api/v1/workflows",
        headers=_h(team["owner"]),
        json={
            "name": "Digest",
            "definition": {
                "steps": [
                    {
                        "id": "digest",
                        "type": "agent",
                        "agent_id": team["agent_id"],
                        "input": "Digest week {{ input.week }}",
                    }
                ]
            },
        },
    )
    workflow_id = created.json()["id"]
    await client.post(f"/api/v1/workflows/{workflow_id}/activate", headers=_h(team["owner"]))
    title = _canary("digest")
    provider.queue(
        calls(("recall_memories", {})), calls(("create_task", {"title": title})), text("ok")
    )
    started = await client.post(
        f"/api/v1/workflows/{workflow_id}/runs",
        headers=_h(team["alice"]),
        json={"input": {"week": 40}},
    )
    await _drain(session_factory, runtime)
    body = await _run(client, team["alice"], started.json()["id"])
    assert body["status"] == "COMPLETED", body
    assert len(await _tasks(session_factory, title)) == 1
    assert await _approvals(session_factory, _step(body, "digest")["agent_run_id"]) == []


# --------------------------------------------------------------------------- #
# P4: unknown provenance fails closed
# --------------------------------------------------------------------------- #
async def _unknown_sources(variant: str, client, provider, session_factory, team):
    """(sources, truncated) for one kind of unknown provenance."""
    org = team["org"]
    if variant == "null":
        return None, False
    if variant == "truncated":
        doc = await _document(session_factory, org, "Handbook", restricted=False)
        return [_doc_ref(doc)], True
    if variant == "malformed":
        return [{"type": "knowledge_document", "id": "not-a-uuid"}], False
    if variant == "missing":
        return [_doc_ref(str(uuid.uuid4()))], False
    if variant == "foreign":
        email = f"stranger-{uuid.uuid4().hex[:6]}@other.example.com"
        stranger = await register_org(client, email, "Other Org")
        doc = await _document(
            session_factory, stranger["organization_id"], "Theirs", restricted=False
        )
        return [_doc_ref(doc)], False
    if variant == "cyclic":
        run_id = await _escalated_run(client, provider, team["alice"], team["agent_id"], "x")
        await _set_provenance(session_factory, run_id, [{"type": "agent_run", "id": run_id}])
        return [{"type": "agent_run", "id": run_id}], False
    if variant == "over_depth":
        from app.rbac.visibility import MAX_PROVENANCE_DEPTH

        doc = await _document(session_factory, org, "Handbook", restricted=False)
        chain = [
            await _escalated_run(client, provider, team["alice"], team["agent_id"], "x")
            for _ in range(MAX_PROVENANCE_DEPTH + 1)
        ]
        for run_id, nxt in zip(chain, chain[1:], strict=False):
            await _set_provenance(session_factory, run_id, [{"type": "agent_run", "id": nxt}])
        await _set_provenance(session_factory, chain[-1], [_doc_ref(doc)])
        return [{"type": "agent_run", "id": chain[0]}], False
    raise AssertionError(variant)


@pytest.mark.parametrize(
    "variant", ["null", "truncated", "malformed", "missing", "foreign", "cyclic", "over_depth"]
)
async def test_unknown_provenance_is_gated_and_no_one_can_approve(
    client, env, provider, runtime, session_factory, variant
):
    team = await _team(client, session_factory, f"unknown-{variant}")
    await _enable(client, team["owner"])
    sources, truncated = await _unknown_sources(variant, client, provider, session_factory, team)
    workflow_id = await _workflow(client, team["owner"])
    secret = _canary("secret")
    run_id = await _to_publication(
        client, session_factory, runtime, team, workflow_id, secret, sources, truncated
    )
    body = await _run(client, team["alice"], run_id)
    assert body["status"] == "WAITING", body
    assert _step(body, "log")["restricted_publication"] is True
    # Unknown provenance can be read by no one: even the owner (knowledge:read_all)
    # is not an eligible approver.
    refused = await client.post(
        f"/api/v1/workflow-runs/{run_id}/approve", headers=_h(team["owner"])
    )
    assert refused.status_code == 403, refused.text
    assert await _tasks(session_factory, secret) == []


async def test_empty_provenance_is_gated(client, env, runtime, session_factory):
    """A tool step that consumes nothing has empty provenance, which proves nothing."""
    team = await _team(client, session_factory, "empty")
    await _enable(client, team["owner"])
    title = _canary("static")
    created = await client.post(
        "/api/v1/workflows",
        headers=_h(team["owner"]),
        json={
            "name": "Static",
            "definition": {
                "steps": [
                    {
                        "id": "log",
                        "type": "tool",
                        "tool": "create_task",
                        "arguments": {"title": title},
                    }
                ]
            },
        },
    )
    workflow_id = created.json()["id"]
    await client.post(f"/api/v1/workflows/{workflow_id}/activate", headers=_h(team["owner"]))
    started = await client.post(
        f"/api/v1/workflows/{workflow_id}/runs", headers=_h(team["alice"]), json={"input": {}}
    )
    await _drain(session_factory, runtime)
    body = await _run(client, team["alice"], started.json()["id"])
    assert body["status"] == "WAITING", body
    assert _step(body, "log")["restricted_publication"] is True
    assert await _tasks(session_factory, title) == []


async def test_unknown_upstream_gates_a_workflow_agent_publication(
    client, env, provider, runtime, session_factory
):
    """An agent step whose input comes from unknown provenance starts truncated
    (M8.8), so its own publication is gated through the agent approval path."""
    team = await _team(client, session_factory, "unknown-agent")
    await _enable(client, team["owner"])
    created = await client.post(
        "/api/v1/workflows",
        headers=_h(team["owner"]),
        json={
            "name": "Plan",
            "definition": {
                "steps": [
                    {"id": "check", "type": "approval", "title": "Prepare?"},
                    {
                        "id": "plan",
                        "type": "agent",
                        "agent_id": team["agent_id"],
                        "input": "Plan after {{ steps.check.output.decision }}",
                    },
                ]
            },
        },
    )
    workflow_id = created.json()["id"]
    await client.post(f"/api/v1/workflows/{workflow_id}/activate", headers=_h(team["owner"]))
    title = _canary("plan")
    provider.queue(calls(("create_task", {"title": title})), text("Planned."))
    run_id = await _to_publication(
        client, session_factory, runtime, team, workflow_id, "unused", None
    )
    body = await _run(client, team["alice"], run_id)
    agent_run_id = _step(body, "plan")["agent_run_id"]
    approvals = await _approvals(session_factory, agent_run_id)
    assert len(approvals) == 1, "M9: the agent publication must wait for approval"
    approval = approvals[0]
    assert approval.restricted_publication is True
    refused = await _approve(client, team["owner"], str(approval.id))
    assert refused.status_code == 403, refused.text
    assert await _tasks(session_factory, title) == []


# --------------------------------------------------------------------------- #
# P5: an ineligible approver neither sees the payload nor decides
# --------------------------------------------------------------------------- #
async def test_agent_request_payload_is_hidden_from_ineligible_approvers(
    client, env, provider, session_factory
):
    team = await _team(client, session_factory, "hide-agent")
    await _enable(client, team["owner"])
    secret = _canary("secret")
    result = await _agent_publication(client, provider, team, "alice", secret)
    approval_id = _gated(result)
    # Max holds agent:approve_actions but cannot read Alice's conversation.
    listed = await client.get("/api/v1/approvals", headers=_h(team["max"]))
    assert listed.status_code == 200, listed.text
    [item] = [a for a in listed.json()["items"] if a["id"] == approval_id]
    assert item["restricted_publication"] is True
    assert secret not in listed.text
    single = await client.get(f"/api/v1/approvals/{approval_id}", headers=_h(team["max"]))
    assert single.status_code == 200 and secret not in single.text
    # Without agent:approve_actions there is no access at all (existing rule).
    viewer = await client.get("/api/v1/approvals", headers=_h(team["vic"]))
    assert viewer.status_code == 403
    # Max cannot decide either way; the request is unchanged and nothing is published.
    for decide in (_approve, _reject):
        refused = await decide(client, team["max"], approval_id)
        assert refused.status_code == 403, refused.text
        assert secret not in refused.text
    [approval] = await _approvals(session_factory, result["run_id"])
    assert approval.status == "PENDING"
    assert await _tasks(session_factory, secret) == []
    assert secret not in (await client.get("/api/v1/notifications", headers=_h(team["max"]))).text
    refusals = await _audit(session_factory, "agent.approval_decision_refused", approval_id)
    assert refusals, "M9: a refused decision is audited"
    refusal = refusals[0]
    assert refusal.metadata_json.get("reason") == "not_eligible"
    assert secret not in json.dumps(refusal.metadata_json)


async def test_workflow_request_payload_is_hidden_from_ineligible_approvers(
    client, env, runtime, session_factory
):
    team = await _team(client, session_factory, "hide-wf")
    await _enable(client, team["owner"])
    doc = await _restricted_doc(session_factory, client, team, readers=["alice", "mia"])
    workflow_id = await _workflow(client, team["owner"])
    secret = _canary("secret")
    run_id = await _to_publication(
        client, session_factory, runtime, team, workflow_id, secret, [_doc_ref(doc)]
    )
    await _waiting(client, team, run_id)
    # Max is an approver without the document: the request exists, its payload does not.
    theirs = await client.get(f"/api/v1/workflow-runs/{run_id}", headers=_h(team["max"]))
    assert theirs.status_code == 200
    assert secret not in theirs.text
    assert _step(theirs.json(), "log")["restricted_publication"] is True
    for path in ("approve", "reject"):
        refused = await client.post(
            f"/api/v1/workflow-runs/{run_id}/{path}", headers=_h(team["max"])
        )
        assert refused.status_code == 403, refused.text
    assert (await _run(client, team["alice"], run_id))["status"] == "WAITING"
    # Mia (granted) sees what she is asked to decide.
    assert (
        secret
        in (await client.get(f"/api/v1/workflow-runs/{run_id}", headers=_h(team["mia"]))).text
    )
    assert await _tasks(session_factory, secret) == []


# --------------------------------------------------------------------------- #
# P6: decisions are secure and idempotent
# --------------------------------------------------------------------------- #
async def test_eligible_agent_approval_publishes_exactly_once(
    client, env, provider, session_factory
):
    team = await _team(client, session_factory, "once-agent")
    await _enable(client, team["owner"])
    secret = _canary("secret")
    result = await _agent_publication(client, provider, team, "alice", secret)
    approval_id = _gated(result)
    await _participant(session_factory, result["conversation_id"], team["mia"], add=True)
    single = await client.get(f"/api/v1/approvals/{result['approval_id']}", headers=_h(team["mia"]))
    assert secret in single.text
    provider.queue(text("Created."))
    approved = await _approve(client, team["mia"], approval_id)
    assert approved.status_code == 200, approved.text
    again = await _approve(client, team["mia"], approval_id)
    assert again.status_code == 409, again.text
    assert len(await _tasks(session_factory, secret)) == 1


async def test_rejected_agent_publication_publishes_nothing(client, env, provider, session_factory):
    team = await _team(client, session_factory, "reject-agent")
    await _enable(client, team["owner"])
    secret = _canary("secret")
    result = await _agent_publication(client, provider, team, "alice", secret)
    approval_id = _gated(result)
    await _participant(session_factory, result["conversation_id"], team["mia"], add=True)
    provider.queue(text("Understood."))
    rejected = await _reject(client, team["mia"], approval_id)
    assert rejected.status_code == 200, rejected.text
    [approval] = await _approvals(session_factory, result["run_id"])
    assert approval.status == "REJECTED"
    assert await _tasks(session_factory, secret) == []


async def test_expired_agent_approval_fails_closed(client, env, provider, session_factory):
    team = await _team(client, session_factory, "expire-agent")
    await _enable(client, team["owner"])
    secret = _canary("secret")
    result = await _agent_publication(client, provider, team, "alice", secret)
    approval_id = _gated(result)
    await _participant(session_factory, result["conversation_id"], team["mia"], add=True)
    async with session_factory() as s:
        await s.execute(
            update(Approval)
            .where(Approval.id == uuid.UUID(approval_id))
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await s.commit()
    expired = await _approve(client, team["mia"], approval_id)
    assert expired.status_code == 409, expired.text
    assert await _tasks(session_factory, secret) == []


async def test_workflow_decisions_publish_once_or_not_at_all(client, env, runtime, session_factory):
    team = await _team(client, session_factory, "once-wf")
    await _enable(client, team["owner"])
    doc = await _restricted_doc(session_factory, client, team, readers=["alice", "mia"])
    workflow_id = await _workflow(client, team["owner"])

    approved_secret = _canary("approved")
    run_id = await _to_publication(
        client, session_factory, runtime, team, workflow_id, approved_secret, [_doc_ref(doc)]
    )
    await _waiting(client, team, run_id)
    ok = await client.post(f"/api/v1/workflow-runs/{run_id}/approve", headers=_h(team["mia"]))
    assert ok.status_code == 200, ok.text
    again = await client.post(f"/api/v1/workflow-runs/{run_id}/approve", headers=_h(team["mia"]))
    assert again.status_code == 409, again.text
    await _drain(session_factory, runtime)
    assert len(await _tasks(session_factory, approved_secret)) == 1

    rejected_secret = _canary("rejected")
    run_id = await _to_publication(
        client, session_factory, runtime, team, workflow_id, rejected_secret, [_doc_ref(doc)]
    )
    await _waiting(client, team, run_id)
    no = await client.post(f"/api/v1/workflow-runs/{run_id}/reject", headers=_h(team["mia"]))
    assert no.status_code == 200, no.text
    await _drain(session_factory, runtime)
    assert _step(await _run(client, team["alice"], run_id), "log")["status"] == "REJECTED"
    assert await _tasks(session_factory, rejected_secret) == []


# --------------------------------------------------------------------------- #
# P7: decision-time authorization is authoritative
# --------------------------------------------------------------------------- #
async def test_agent_approver_revoked_before_deciding_cannot_decide(
    client, env, provider, session_factory
):
    team = await _team(client, session_factory, "revoke-agent")
    await _enable(client, team["owner"])
    secret = _canary("secret")
    result = await _agent_publication(client, provider, team, "alice", secret)
    approval_id = _gated(result)
    await _participant(session_factory, result["conversation_id"], team["mia"], add=True)
    seen = await client.get(f"/api/v1/approvals/{result['approval_id']}", headers=_h(team["mia"]))
    assert secret in seen.text
    await _participant(session_factory, result["conversation_id"], team["mia"], add=False)
    hidden = await client.get(f"/api/v1/approvals/{result['approval_id']}", headers=_h(team["mia"]))
    assert secret not in hidden.text
    refused = await _approve(client, team["mia"], approval_id)
    assert refused.status_code == 403, refused.text
    assert await _tasks(session_factory, secret) == []


async def test_agent_approver_granted_before_deciding_becomes_eligible(
    client, env, provider, session_factory
):
    team = await _team(client, session_factory, "grant-agent")
    await _enable(client, team["owner"])
    secret = _canary("secret")
    result = await _agent_publication(client, provider, team, "alice", secret)
    approval_id = _gated(result)
    refused = await _approve(client, team["mia"], approval_id)
    assert refused.status_code == 403, refused.text
    await _participant(session_factory, result["conversation_id"], team["mia"], add=True)
    provider.queue(text("Created."))
    approved = await _approve(client, team["mia"], approval_id)
    assert approved.status_code == 200, approved.text
    assert len(await _tasks(session_factory, secret)) == 1


async def test_workflow_eligibility_follows_current_document_access(
    client, env, runtime, session_factory
):
    team = await _team(client, session_factory, "revoke-wf")
    await _enable(client, team["owner"])
    doc = await _restricted_doc(session_factory, client, team, readers=["alice", "mia"])
    workflow_id = await _workflow(client, team["owner"])
    secret = _canary("secret")
    run_id = await _to_publication(
        client, session_factory, runtime, team, workflow_id, secret, [_doc_ref(doc)]
    )
    await _waiting(client, team, run_id)
    await _grant(client, team["owner"], doc, [team["alice"]["user"]["id"]])  # revoke Mia
    refused = await client.post(f"/api/v1/workflow-runs/{run_id}/approve", headers=_h(team["mia"]))
    assert refused.status_code == 403, refused.text
    assert (
        secret
        not in (await client.get(f"/api/v1/workflow-runs/{run_id}", headers=_h(team["mia"]))).text
    )
    await _grant(
        client, team["owner"], doc, [team["alice"]["user"]["id"], team["mia"]["user"]["id"]]
    )
    approved = await client.post(f"/api/v1/workflow-runs/{run_id}/approve", headers=_h(team["mia"]))
    assert approved.status_code == 200, approved.text
    await _drain(session_factory, runtime)
    assert len(await _tasks(session_factory, secret)) == 1


async def test_a_publication_no_longer_restricted_at_decision_time_follows_m8(
    client, env, runtime, session_factory
):
    """Restriction is re-read from the M8 provenance at decision time: once the
    document is organization-readable, any approver may decide."""
    team = await _team(client, session_factory, "derestrict")
    await _enable(client, team["owner"])
    doc = await _restricted_doc(session_factory, client, team, readers=["alice"])
    workflow_id = await _workflow(client, team["owner"])
    secret = _canary("secret")
    run_id = await _to_publication(
        client, session_factory, runtime, team, workflow_id, secret, [_doc_ref(doc)]
    )
    await _waiting(client, team, run_id)
    refused = await client.post(f"/api/v1/workflow-runs/{run_id}/approve", headers=_h(team["max"]))
    assert refused.status_code == 403, refused.text
    opened = await client.put(
        f"/api/v1/documents/{doc}/access",
        headers=_h(team["owner"]),
        json={"visibility": "INHERIT", "grants": []},
    )
    assert opened.status_code == 200, opened.text
    approved = await client.post(f"/api/v1/workflow-runs/{run_id}/approve", headers=_h(team["max"]))
    assert approved.status_code == 200, approved.text
    await _drain(session_factory, runtime)
    assert len(await _tasks(session_factory, secret)) == 1


# --------------------------------------------------------------------------- #
# P8: composition with existing approvals and separation of duties
# --------------------------------------------------------------------------- #
async def test_an_existing_agent_approval_is_reused_not_duplicated(
    client, env, provider, session_factory
):
    """notify_member already requires approval for Executive AI: one request, marked."""
    team = await _team(client, session_factory, "reuse-agent")
    await _enable(client, team["owner"])
    secret = _canary("secret")
    provider.queue(
        calls(
            (
                "notify_member",
                {"recipient_email": "max-reuse-agent@acme.example.com", "title": secret},
            )
        )
    )
    result = await _execute(client, team["alice"], team["agent_id"])
    assert result["status"] == "awaiting_approval", result
    [approval] = await _approvals(session_factory, result["run_id"])
    assert approval.restricted_publication is True
    assert secret not in (await client.get("/api/v1/approvals", headers=_h(team["max"]))).text
    refused = await _approve(client, team["max"], str(approval.id))
    assert refused.status_code == 403, refused.text


async def test_an_existing_workflow_approval_is_reused_not_duplicated(
    client, env, runtime, session_factory
):
    team = await _team(client, session_factory, "reuse-wf")
    await _enable(client, team["owner"])
    doc = await _restricted_doc(session_factory, client, team, readers=["alice"])
    workflow_id = await _workflow(client, team["owner"], require_approval=True)
    secret = _canary("secret")
    run_id = await _to_publication(
        client, session_factory, runtime, team, workflow_id, secret, [_doc_ref(doc)]
    )
    body = await _run(client, team["alice"], run_id)
    waiting = [s for s in body["steps"] if s["step_id"] == "log" and s["status"] == "WAITING"]
    assert len(waiting) == 1
    assert waiting[0]["restricted_publication"] is True
    assert (
        secret
        not in (await client.get(f"/api/v1/workflow-runs/{run_id}", headers=_h(team["max"]))).text
    )
    refused = await client.post(f"/api/v1/workflow-runs/{run_id}/approve", headers=_h(team["max"]))
    assert refused.status_code == 403, refused.text


async def test_the_runs_person_may_approve_when_eligible(client, env, provider, session_factory):
    team = await _team(client, session_factory, "self-ok")
    await _enable(client, team["owner"])
    secret = _canary("secret")
    # Mia (MANAGER) runs the agent herself: she can read her own conversation.
    result = await _agent_publication(client, provider, team, "mia", secret)
    approval_id = _gated(result)
    provider.queue(text("Created."))
    approved = await _approve(client, team["mia"], approval_id)
    assert approved.status_code == 200, approved.text
    assert len(await _tasks(session_factory, secret)) == 1


async def test_independent_approval_still_blocks_the_runs_person(
    client, env, provider, session_factory
):
    team = await _team(client, session_factory, "self-sod")
    await _enable(client, team["owner"])
    sod = await client.patch(
        "/api/v1/organizations/current",
        headers=_h(team["owner"]),
        json={"require_independent_approval": True},
    )
    assert sod.status_code == 200, sod.text
    secret = _canary("secret")
    result = await _agent_publication(client, provider, team, "mia", secret)
    approval_id = _gated(result)
    own = await _approve(client, team["mia"], approval_id)
    assert own.status_code == 403
    assert own.json()["error"]["code"] == "independent_approval_required"
    await _participant(session_factory, result["conversation_id"], team["max"], add=True)
    provider.queue(text("Created."))
    peer = await _approve(client, team["max"], approval_id)
    assert peer.status_code == 200, peer.text
    assert len(await _tasks(session_factory, secret)) == 1


# --------------------------------------------------------------------------- #
# Excluded sinks, no retraction, audit
# --------------------------------------------------------------------------- #
async def test_reads_and_personal_memory_are_never_gated(client, env, provider, session_factory):
    team = await _team(client, session_factory, "excluded")
    await _enable(client, team["owner"])
    memory_id = await _memory(client, team["alice"], "Old preference")
    provider.queue(
        calls(("save_user_memory", {"content": "Prefers mornings"})),
        calls(("recall_memories", {})),
        calls(("list_tasks", {})),
        calls(("forget_user_memory", {"memory_id": memory_id})),
        text("Done."),
    )
    result = await _execute(client, team["alice"], team["agent_id"])
    assert result["status"] == "completed", result
    assert await _approvals(session_factory, result["run_id"]) == []


async def test_published_output_is_not_retracted_when_access_changes(
    client, env, runtime, session_factory
):
    team = await _team(client, session_factory, "keep")
    await _enable(client, team["owner"])
    doc = await _restricted_doc(session_factory, client, team, readers=["alice", "mia"])
    workflow_id = await _workflow(client, team["owner"])
    secret = _canary("secret")
    run_id = await _to_publication(
        client, session_factory, runtime, team, workflow_id, secret, [_doc_ref(doc)]
    )
    await _waiting(client, team, run_id)
    approved = await client.post(f"/api/v1/workflow-runs/{run_id}/approve", headers=_h(team["mia"]))
    assert approved.status_code == 200, approved.text
    await _drain(session_factory, runtime)
    await _grant(client, team["owner"], doc, [team["alice"]["user"]["id"]])  # revoke Mia
    assert len(await _tasks(session_factory, secret)) == 1
    tasks = await client.get("/api/v1/tasks", headers=_h(team["vic"]))
    assert secret in tasks.text


async def test_requests_and_decisions_are_audited_without_content(
    client, env, provider, runtime, session_factory
):
    team = await _team(client, session_factory, "audit")
    await _enable(client, team["owner"])
    secret = _canary("secret")

    # Agent path.
    result = await _agent_publication(client, provider, team, "alice", secret)
    approval_id = _gated(result)
    [requested] = await _audit(session_factory, "agent.approval_requested", approval_id)
    assert requested.metadata_json["restricted_publication"] is True
    assert requested.metadata_json["source_counts"] == {"conversation": 1}
    await _participant(session_factory, result["conversation_id"], team["mia"], add=True)
    provider.queue(text("Created."))
    assert (await _approve(client, team["mia"], approval_id)).status_code == 200
    [decided] = await _audit(session_factory, "agent.approval_decided", approval_id)
    assert decided.metadata_json["restricted_publication"] is True
    for row in (requested, decided):
        assert secret not in json.dumps(row.metadata_json)

    # Workflow path.
    doc = await _restricted_doc(session_factory, client, team, readers=["alice"])
    workflow_id = await _workflow(client, team["owner"])
    wf_secret = _canary("wf")
    run_id = await _to_publication(
        client, session_factory, runtime, team, workflow_id, wf_secret, [_doc_ref(doc)]
    )
    await _waiting(client, team, run_id)
    [wf_requested] = await _audit(session_factory, "workflow.approval_requested", run_id)
    meta = wf_requested.metadata_json
    assert (meta["step"], meta["tool"], meta["restricted_publication"]) == (
        "log",
        "create_task",
        True,
    )
    assert meta["source_counts"] == {"external_input": 1, "knowledge_document": 1}
    assert wf_secret not in json.dumps(meta)


# --------------------------------------------------------------------------- #
# M9.4: the agent path on its own (gate, marker, single request, resume)
# --------------------------------------------------------------------------- #
def test_publication_sinks_are_the_approved_list():
    from app.agents.publication import PUBLICATION_SINKS, is_publication_sink
    from app.agents.tools.registry import tool_registry
    from app.integrations.tools import McpToolHandler

    sinks = {h.handler_identifier for h in tool_registry.all() if is_publication_sink(h)}
    assert (
        sinks
        == set(PUBLICATION_SINKS)
        == {
            "create_task",
            "update_task",
            "notify_member",
            "save_agent_memory",
            "send_email",
            "call_webhook",
            "create_calendar_event",
            "upsert_crm_contact",
            "add_crm_note",
        }
    )
    assert is_publication_sink(McpToolHandler(uuid.uuid4(), "mcp_x_y", "LOW"))
    for name in ("recall_memories", "list_tasks", "save_user_memory", "forget_user_memory"):
        assert not is_publication_sink(tool_registry.get_by_identifier(name))


async def _agent_step_after(client, session_factory, runtime, provider, team, sources, truncated):
    """A workflow agent step whose input derives from an upstream step with the
    given provenance; the agent's own create_task call is the publication."""
    created = await client.post(
        "/api/v1/workflows",
        headers=_h(team["owner"]),
        json={
            "name": f"Plan {uuid.uuid4().hex[:6]}",
            "definition": {
                "steps": [
                    {"id": "check", "type": "approval", "title": "Prepare?"},
                    {
                        "id": "plan",
                        "type": "agent",
                        "agent_id": team["agent_id"],
                        "input": "Plan after {{ steps.check.output.decision }}",
                    },
                ]
            },
        },
    )
    workflow_id = created.json()["id"]
    await client.post(f"/api/v1/workflows/{workflow_id}/activate", headers=_h(team["owner"]))
    title = _canary("plan")
    provider.queue(calls(("create_task", {"title": title})), text("Planned."))
    run_id = await _to_publication(
        client, session_factory, runtime, team, workflow_id, "unused", sources, truncated
    )
    body = await _run(client, team["alice"], run_id)
    return _step(body, "plan")["agent_run_id"], title


@pytest.mark.parametrize("variant", ["null", "empty", "truncated", "malformed", "missing"])
async def test_unknown_provenance_gates_an_agent_publication(
    client, env, provider, runtime, session_factory, variant
):
    team = await _team(client, session_factory, f"agent-unknown-{variant}")
    await _enable(client, team["owner"])
    if variant == "empty":
        sources, truncated = [], False
    else:
        sources, truncated = await _unknown_sources(
            variant, client, provider, session_factory, team
        )
    agent_run_id, title = await _agent_step_after(
        client, session_factory, runtime, provider, team, sources, truncated
    )
    [approval] = await _approvals(session_factory, agent_run_id)
    assert approval.tool_name == "create_task" and approval.status == "PENDING"
    assert approval.restricted_publication is True
    assert await _tasks(session_factory, title) == []


async def test_organization_readable_upstream_does_not_gate_an_agent_publication(
    client, env, provider, runtime, session_factory
):
    team = await _team(client, session_factory, "agent-open")
    await _enable(client, team["owner"])
    handbook = await _document(session_factory, team["org"], "Handbook", restricted=False)
    agent_run_id, title = await _agent_step_after(
        client, session_factory, runtime, provider, team, [_doc_ref(handbook)], False
    )
    assert await _approvals(session_factory, agent_run_id) == []
    assert len(await _tasks(session_factory, title)) == 1


async def test_setting_off_leaves_an_existing_approval_unmarked(
    client, env, provider, session_factory
):
    team = await _team(client, session_factory, "off-marker")
    args = {"recipient_email": "max-off-marker@acme.example.com", "title": "x"}
    provider.queue(calls(("notify_member", args)))
    result = await _execute(client, team["alice"], team["agent_id"])
    assert result["status"] == "awaiting_approval", result
    [approval] = await _approvals(session_factory, result["run_id"])
    assert approval.restricted_publication is False


async def test_a_reused_approval_resumes_and_publishes_once(client, env, provider, session_factory):
    """notify_member already requires approval: one request, marked, and approving
    it delivers the notification exactly once."""
    team = await _team(client, session_factory, "reuse-once")
    await _enable(client, team["owner"])
    secret = _canary("secret")
    provider.queue(
        calls(
            (
                "notify_member",
                {"recipient_email": "max-reuse-once@acme.example.com", "title": secret},
            )
        )
    )
    result = await _execute(client, team["mia"], team["agent_id"])
    approval_id = _gated(result)
    [approval] = await _approvals(session_factory, result["run_id"])
    assert approval.restricted_publication is True
    listed = await client.get(f"/api/v1/approvals/{approval_id}", headers=_h(team["mia"]))
    assert listed.json()["restricted_publication"] is True
    provider.queue(text("Sent."))
    approved = await _approve(client, team["mia"], approval_id)
    assert approved.status_code == 200, approved.text
    assert (await _approve(client, team["mia"], approval_id)).status_code == 409
    assert len(await _approvals(session_factory, result["run_id"])) == 1
    inbox = await client.get("/api/v1/notifications", headers=_h(team["max"]))
    assert inbox.text.count(secret) == 1


# --------------------------------------------------------------------------- #
# M9.5: the workflow path on its own (gate, marker, single request, resume)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "variant",
    ["null", "truncated", "malformed", "missing", "foreign", "cyclic", "over_depth"],
)
async def test_unknown_provenance_gates_a_workflow_publication(
    client, env, provider, runtime, session_factory, variant
):
    """Empty provenance is test_empty_provenance_is_gated: here the step also
    consumes the run's input, so its own provenance is never empty."""
    team = await _team(client, session_factory, f"wf-unknown-{variant}")
    await _enable(client, team["owner"])
    sources, truncated = await _unknown_sources(variant, client, provider, session_factory, team)
    workflow_id = await _workflow(client, team["owner"])
    secret = _canary("secret")
    run_id = await _to_publication(
        client, session_factory, runtime, team, workflow_id, secret, sources, truncated
    )
    body = await _waiting(client, team, run_id)
    log = _step(body, "log")
    assert log["status"] == "WAITING" and log["restricted_publication"] is True
    assert await _tasks(session_factory, secret) == []


async def test_workflow_reads_are_never_gated(client, env, runtime, session_factory):
    """Read steps that consume nothing have empty provenance, which is restricted,
    yet reads are not publications."""
    team = await _team(client, session_factory, "wf-read")
    await _enable(client, team["owner"])
    created = await client.post(
        "/api/v1/workflows",
        headers=_h(team["owner"]),
        json={
            "name": "Read",
            "definition": {
                "steps": [
                    {"id": "time", "type": "tool", "tool": "get_current_time", "arguments": {}},
                    {"id": "tasks", "type": "tool", "tool": "list_tasks", "arguments": {}},
                ]
            },
        },
    )
    assert created.status_code == 201, created.text
    workflow_id = created.json()["id"]
    await client.post(f"/api/v1/workflows/{workflow_id}/activate", headers=_h(team["owner"]))
    started = await client.post(
        f"/api/v1/workflows/{workflow_id}/runs", headers=_h(team["alice"]), json={"input": {}}
    )
    await _drain(session_factory, runtime)
    body = await _run(client, team["alice"], started.json()["id"])
    assert body["status"] == "COMPLETED", body
    assert [s["restricted_publication"] for s in body["steps"]] == [False, False]


async def test_a_reused_workflow_approval_publishes_once(client, env, runtime, session_factory):
    """`require_approval` already makes the step wait: one decision, marked, and
    approving it publishes exactly once."""
    team = await _team(client, session_factory, "wf-reuse-once")
    await _enable(client, team["owner"])
    doc = await _restricted_doc(session_factory, client, team, readers=["alice"])
    workflow_id = await _workflow(client, team["owner"], require_approval=True)
    secret = _canary("secret")
    run_id = await _to_publication(
        client, session_factory, runtime, team, workflow_id, secret, [_doc_ref(doc)]
    )
    body = await _waiting(client, team, run_id)
    waiting = [s for s in body["steps"] if s["status"] == "WAITING"]
    assert [(s["step_id"], s["restricted_publication"]) for s in waiting] == [("log", True)]
    ok = await client.post(f"/api/v1/workflow-runs/{run_id}/approve", headers=_h(team["owner"]))
    assert ok.status_code == 200, ok.text
    again = await client.post(f"/api/v1/workflow-runs/{run_id}/approve", headers=_h(team["owner"]))
    assert again.status_code == 409, again.text
    await _drain(session_factory, runtime)
    body = await _run(client, team["alice"], run_id)
    assert body["status"] == "COMPLETED", body
    assert len([s for s in body["steps"] if s["step_id"] == "log"]) == 1
    assert len(await _tasks(session_factory, secret)) == 1


async def test_independent_approval_applies_to_a_restricted_workflow_publication(
    client, env, runtime, session_factory
):
    team = await _team(client, session_factory, "wf-sod")
    await _enable(client, team["owner"])
    sod = await client.patch(
        "/api/v1/organizations/current",
        headers=_h(team["owner"]),
        json={"require_independent_approval": True},
    )
    assert sod.status_code == 200, sod.text
    doc = await _restricted_doc(session_factory, client, team, readers=["mia", "max"])
    workflow_id = await _workflow(client, team["owner"])
    secret = _canary("secret")
    run_id = await _to_publication(
        client, session_factory, runtime, team, workflow_id, secret, [_doc_ref(doc)], person="mia"
    )
    await _waiting(client, team, run_id)
    # The existing workflow rule: the run's initiator cannot decide.
    own = await client.post(f"/api/v1/workflow-runs/{run_id}/approve", headers=_h(team["mia"]))
    assert own.status_code == 403, own.text
    assert (await _run(client, team["mia"], run_id))["status"] == "WAITING"
    peer = await client.post(f"/api/v1/workflow-runs/{run_id}/approve", headers=_h(team["max"]))
    assert peer.status_code == 200, peer.text
    await _drain(session_factory, runtime)
    assert len(await _tasks(session_factory, secret)) == 1


# --------------------------------------------------------------------------- #
# M9.6: approver eligibility and payload protection, end to end
# --------------------------------------------------------------------------- #
async def _reads(client, auth, paths: list[str]) -> str:
    """Every response body an approver can fetch, joined, with their statuses."""
    bodies = []
    for path in paths:
        resp = await client.get(path, headers=_h(auth))
        bodies.append(f"{path} {resp.status_code} {resp.text}")
    return "\n".join(bodies)


async def test_an_ineligible_agent_approver_gets_no_payload_from_any_endpoint(
    client, env, provider, session_factory
):
    team = await _team(client, session_factory, "m96-agent")
    await _enable(client, team["owner"])
    secret = _canary("secret")
    result = await _agent_publication(client, provider, team, "alice", secret)
    approval_id = _gated(result)

    # Max holds agent:approve_actions; that alone is not enough.
    single = (await client.get(f"/api/v1/approvals/{approval_id}", headers=_h(team["max"]))).json()
    assert single["status"] == "PENDING" and single["tool_name"] == "create_task"
    assert single["restricted_publication"] is True and single["payload_withheld"] is True
    assert single["request_payload"] == {} and single["modified_payload"] is None
    seen = await _reads(
        client,
        team["max"],
        [
            "/api/v1/approvals",
            f"/api/v1/approvals/{approval_id}",
            "/api/v1/runs",
            f"/api/v1/runs/{result['run_id']}",
            f"/api/v1/conversations/{result['conversation_id']}",
            f"/api/v1/conversations/{result['conversation_id']}/messages",
            "/api/v1/notifications",
            "/api/v1/operations/overview",
        ],
    )
    assert secret not in seen

    # Every way to decide is refused, without content; nothing changes.
    for path, body in (
        ("approve", None),
        ("reject", None),
        ("modify", {"arguments": {"title": "edited"}}),
    ):
        refused = await client.post(
            f"/api/v1/approvals/{approval_id}/{path}", headers=_h(team["max"]), json=body
        )
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"]["code"] == "not_eligible"
        assert secret not in refused.text
    [approval] = await _approvals(session_factory, result["run_id"])
    assert approval.status == "PENDING" and approval.approved_by is None
    assert await _tasks(session_factory, secret) == []

    # An eligible approver sees the payload and decides; her note stays with
    # the content, so Max sees neither.
    await _participant(session_factory, result["conversation_id"], team["mia"], add=True)
    mine = (await client.get(f"/api/v1/approvals/{approval_id}", headers=_h(team["mia"]))).json()
    assert mine["payload_withheld"] is False and mine["request_payload"] == {"title": secret}
    note = _canary("note")
    provider.queue(text("Created."))
    approved = await client.post(
        f"/api/v1/approvals/{approval_id}/approve", headers=_h(team["mia"]), json={"note": note}
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval"]["decision_note"] == note
    after = await client.get(f"/api/v1/approvals/{approval_id}", headers=_h(team["max"]))
    assert after.json()["status"] == "APPROVED"
    assert secret not in after.text and note not in after.text
    assert len(await _tasks(session_factory, secret)) == 1


async def test_an_ineligible_workflow_approver_gets_no_payload_from_any_endpoint(
    client, env, runtime, session_factory
):
    team = await _team(client, session_factory, "m96-wf")
    await _enable(client, team["owner"])
    doc = await _restricted_doc(session_factory, client, team, readers=["alice", "mia"])
    workflow_id = await _workflow(client, team["owner"])
    secret = _canary("secret")
    run_id = await _to_publication(
        client, session_factory, runtime, team, workflow_id, secret, [_doc_ref(doc)]
    )
    await _waiting(client, team, run_id)

    theirs = (await client.get(f"/api/v1/workflow-runs/{run_id}", headers=_h(team["max"]))).json()
    log = _step(theirs, "log")
    assert log["status"] == "WAITING" and log["restricted_publication"] is True
    assert log["output"] is None
    seen = await _reads(
        client,
        team["max"],
        [
            "/api/v1/workflow-runs",
            f"/api/v1/workflow-runs/{run_id}",
            "/api/v1/approvals",
            "/api/v1/notifications",
            "/api/v1/operations/overview",
        ],
    )
    assert secret not in seen
    for path in ("approve", "reject"):
        refused = await client.post(
            f"/api/v1/workflow-runs/{run_id}/{path}", headers=_h(team["max"])
        )
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"]["code"] == "not_eligible"
        assert secret not in refused.text
    assert (await _run(client, team["alice"], run_id))["status"] == "WAITING"

    # Mia (granted) receives the arguments she is asked to decide.
    hers = await _run(client, team["mia"], run_id)
    assert _step(hers, "log")["output"]["details"]["arguments"]["title"] == secret
    assert await _tasks(session_factory, secret) == []


async def test_a_source_deleted_before_the_decision_fails_closed(
    client, env, runtime, session_factory
):
    team = await _team(client, session_factory, "m96-deleted")
    await _enable(client, team["owner"])
    doc = await _restricted_doc(session_factory, client, team, readers=["alice", "mia"])
    workflow_id = await _workflow(client, team["owner"])
    secret = _canary("secret")
    run_id = await _to_publication(
        client, session_factory, runtime, team, workflow_id, secret, [_doc_ref(doc)]
    )
    await _waiting(client, team, run_id)
    async with session_factory() as s:
        await s.execute(delete(KnowledgeDocument).where(KnowledgeDocument.id == uuid.UUID(doc)))
        await s.commit()
    for person in ("mia", "owner"):
        refused = await client.post(
            f"/api/v1/workflow-runs/{run_id}/approve", headers=_h(team[person])
        )
        assert refused.status_code == 403, refused.text
        assert secret not in refused.text
        assert (
            secret
            not in (
                await client.get(f"/api/v1/workflow-runs/{run_id}", headers=_h(team[person]))
            ).text
        )
    assert (await _run(client, team["alice"], run_id))["status"] == "WAITING"
    assert await _tasks(session_factory, secret) == []


async def test_an_approver_who_loses_the_permission_cannot_decide(
    client, env, provider, session_factory
):
    team = await _team(client, session_factory, "m96-demoted")
    await _enable(client, team["owner"])
    secret = _canary("secret")
    result = await _agent_publication(client, provider, team, "alice", secret)
    approval_id = _gated(result)
    await _participant(session_factory, result["conversation_id"], team["mia"], add=True)
    async with session_factory() as s:
        await s.execute(
            update(OrganizationMember)
            .where(
                OrganizationMember.user_id == _uid(team["mia"]),
                OrganizationMember.organization_id == uuid.UUID(team["org"]),
            )
            .values(role_name="MEMBER")
        )
        await s.commit()
    refused = await _approve(client, team["mia"], approval_id)
    assert refused.status_code == 403, refused.text
    assert secret not in refused.text
    assert await _tasks(session_factory, secret) == []


# --------------------------------------------------------------------------- #
# M9.7: audit attribution and refusals, never content
# --------------------------------------------------------------------------- #
async def _org_audit(session_factory, org: str) -> list[AuditLog]:
    async with session_factory() as s:
        rows = await s.execute(select(AuditLog).where(AuditLog.organization_id == uuid.UUID(org)))
        return list(rows.scalars())


def _no_content(rows: list[AuditLog], *values: str) -> None:
    dumped = json.dumps([[r.action, r.metadata_json] for r in rows], default=str)
    for value in values:
        assert value not in dumped


async def test_a_refused_agent_decision_is_audited_and_a_later_one_still_decides(
    client, env, provider, session_factory
):
    team = await _team(client, session_factory, "m97-agent")
    await _enable(client, team["owner"])
    secret = _canary("secret")
    result = await _agent_publication(client, provider, team, "alice", secret)
    approval_id = _gated(result)
    [requested] = await _audit(session_factory, "agent.approval_requested", approval_id)
    assert requested.metadata_json["restricted_publication"] is True
    assert requested.metadata_json["sources_truncated"] is False

    refused = await _approve(client, team["mia"], approval_id)
    assert refused.status_code == 403, refused.text
    [refusal] = await _audit(session_factory, "agent.approval_decision_refused", approval_id)
    assert refusal.user_id == _uid(team["mia"])
    assert refusal.metadata_json == {
        "reason": "not_eligible",
        "tool": "create_task",
        "run_id": result["run_id"],
        "restricted_publication": True,
    }
    [approval] = await _approvals(session_factory, result["run_id"])
    assert approval.status == "PENDING"
    assert await _audit(session_factory, "agent.approval_decided", approval_id) == []

    # Granted later, the same approver decides; nothing records a second decision.
    await _participant(session_factory, result["conversation_id"], team["mia"], add=True)
    note = _canary("note")
    provider.queue(text("Created."))
    approved = await client.post(
        f"/api/v1/approvals/{approval_id}/approve", headers=_h(team["mia"]), json={"note": note}
    )
    assert approved.status_code == 200, approved.text
    [decided] = await _audit(session_factory, "agent.approval_decided", approval_id)
    assert decided.metadata_json["restricted_publication"] is True
    assert decided.metadata_json["restricted"] is True
    assert len(await _audit(session_factory, "agent.approval_decision_refused", approval_id)) == 1
    assert len(await _tasks(session_factory, secret)) == 1
    _no_content(await _org_audit(session_factory, team["org"]), secret, note)


async def test_a_refused_workflow_decision_is_audited_and_a_later_one_still_decides(
    client, env, runtime, session_factory
):
    team = await _team(client, session_factory, "m97-wf")
    await _enable(client, team["owner"])
    doc = await _restricted_doc(session_factory, client, team, readers=["alice"])
    workflow_id = await _workflow(client, team["owner"])
    secret = _canary("secret")
    run_id = await _to_publication(
        client, session_factory, runtime, team, workflow_id, secret, [_doc_ref(doc)]
    )
    await _waiting(client, team, run_id)

    refused = await client.post(f"/api/v1/workflow-runs/{run_id}/approve", headers=_h(team["max"]))
    assert refused.status_code == 403, refused.text
    [refusal] = await _audit(session_factory, "workflow.approval_decision_refused", run_id)
    assert refusal.user_id == _uid(team["max"])
    assert refusal.metadata_json == {
        "step": "log",
        "reason": "not_eligible",
        "restricted_publication": True,
    }
    assert (await _run(client, team["alice"], run_id))["status"] == "WAITING"

    readers = [team["alice"]["user"]["id"], team["max"]["user"]["id"]]
    await _grant(client, team["owner"], doc, readers)
    approved = await client.post(f"/api/v1/workflow-runs/{run_id}/approve", headers=_h(team["max"]))
    assert approved.status_code == 200, approved.text
    await _drain(session_factory, runtime)
    decided = [
        r
        for r in await _audit(session_factory, "workflow.approval_decided", run_id)
        if r.metadata_json.get("step") == "log"
    ]
    assert len(decided) == 1
    assert decided[0].metadata_json["restricted_publication"] is True
    assert decided[0].metadata_json["restricted"] is True
    assert len(await _tasks(session_factory, secret)) == 1
    _no_content(await _org_audit(session_factory, team["org"]), secret)


async def test_unrestricted_requests_are_attributed_as_such(
    client, env, provider, runtime, session_factory
):
    # Agent, setting off: an approval the tool always required, not a restricted
    # publication; its sources are still counted.
    team = await _team(client, session_factory, "m97-open")
    args = {"recipient_email": "max-m97-open@acme.example.com", "title": "x"}
    provider.queue(calls(("notify_member", args)))
    result = await _execute(client, team["alice"], team["agent_id"])
    approval_id = _gated(result)
    [requested] = await _audit(session_factory, "agent.approval_requested", approval_id)
    assert requested.metadata_json["restricted_publication"] is False
    assert requested.metadata_json["source_counts"] == {"conversation": 1}

    # Workflow, setting on: a step that requires approval and consumes only
    # organization-readable sources is not a restricted publication.
    await _enable(client, team["owner"])
    handbook = await _document(session_factory, team["org"], "Handbook", restricted=False)
    workflow_id = await _workflow(client, team["owner"], require_approval=True)
    secret = _canary("secret")
    run_id = await _to_publication(
        client, session_factory, runtime, team, workflow_id, secret, [_doc_ref(handbook)]
    )
    body = await _waiting(client, team, run_id)
    assert _step(body, "log")["restricted_publication"] is False
    assert await _audit(session_factory, "workflow.approval_requested", run_id) == []
    ok = await client.post(f"/api/v1/workflow-runs/{run_id}/approve", headers=_h(team["max"]))
    assert ok.status_code == 200, ok.text
    [decided] = [
        r
        for r in await _audit(session_factory, "workflow.approval_decided", run_id)
        if r.metadata_json.get("step") == "log"
    ]
    assert decided.metadata_json["restricted_publication"] is False
    assert decided.metadata_json["restricted"] is False
