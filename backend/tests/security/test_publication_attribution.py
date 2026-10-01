# ruff: noqa: F811  (pytest fixtures imported from the M8.2 test module)
"""Milestone 8.8: publication attribution in the audit trail.

When a run publishes output (a non-low-risk tool call), the existing audit events
`agent.tool_executed` and `workflow.tool_executed` record what it derives from:
reference counts by type, whether provenance was truncated, the acting role, and
`restricted`. Content is never copied into the audit.
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import select, update

from app.models.audit import AuditLog
from app.models.run import AgentRun
from app.models.workflow import WorkflowStepRun
from tests.agents.test_controlled_autonomy import calls, text
from tests.security.test_provenance import (  # noqa: F401 (fixtures)
    _canary,
    _drain,
    _h,
    _memory,
    _org,
    env,
    provider,
    runtime,
)


async def _audits(session_factory, action: str, target_id: str) -> list[AuditLog]:
    async with session_factory() as s:
        rows = await s.execute(
            select(AuditLog).where(AuditLog.action == action, AuditLog.target_id == target_id)
        )
        return list(rows.scalars())


async def _workflow(client, owner, steps) -> str:
    created = await client.post(
        "/api/v1/workflows",
        headers=_h(owner),
        json={"name": "Publish", "definition": {"steps": steps}},
    )
    assert created.status_code == 201, created.text
    workflow_id = created.json()["id"]
    await client.post(f"/api/v1/workflows/{workflow_id}/activate", headers=_h(owner))
    return workflow_id


async def test_publishing_from_organization_sources_is_not_restricted(
    client, env, provider, runtime, session_factory
):
    owner, alice, _, agent_id = await _org(client, session_factory, "pub-org", "MEMBER")
    await _memory(client, owner, "Office opens at 8", scope="ORGANIZATION")
    workflow_id = await _workflow(
        client,
        owner,
        [
            {
                "id": "digest",
                "type": "agent",
                "agent_id": agent_id,
                "input": "Week {{ input.week }}",
            },
            {
                "id": "log",
                "type": "tool",
                "tool": "create_task",
                "arguments": {"title": "Digest", "description": "{{ steps.digest.output.text }}"},
            },
        ],
    )
    marker = _canary("digest")
    provider.queue(calls(("recall_memories", {})), text(marker))
    started = await client.post(
        f"/api/v1/workflows/{workflow_id}/runs", headers=_h(alice), json={"input": {"week": 40}}
    )
    run_id = started.json()["id"]
    await _drain(session_factory, runtime)

    [audit] = await _audits(session_factory, "workflow.tool_executed", run_id)
    meta = audit.metadata_json
    assert meta["step"] == "log" and meta["tool"] == "create_task"
    assert meta["restricted"] is False
    assert meta["source_counts"] == {"external_input": 1, "memory": 1}
    assert meta["sources_truncated"] is False
    assert meta["acting_role"] == "MEMBER"
    assert str(audit.user_id) == alice["user"]["id"] and audit.created_at is not None
    assert marker not in json.dumps(meta)


async def test_publishing_from_unknown_provenance_is_restricted(
    client, env, runtime, session_factory
):
    owner, alice, _, _ = await _org(client, session_factory, "pub-unknown", "MEMBER")
    workflow_id = await _workflow(
        client,
        owner,
        [
            {"id": "check", "type": "approval", "title": "Go ahead?"},
            {
                "id": "log",
                "type": "tool",
                "tool": "create_task",
                "arguments": {"title": "Decision: {{ steps.check.output.decision }}"},
            },
        ],
    )
    started = await client.post(
        f"/api/v1/workflows/{workflow_id}/runs", headers=_h(alice), json={"input": {}}
    )
    run_id = started.json()["id"]
    await _drain(session_factory, runtime)
    async with session_factory() as s:
        await s.execute(
            update(WorkflowStepRun)
            .where(WorkflowStepRun.run_id == uuid.UUID(run_id))
            .values(sources=None)
        )
        await s.commit()
    decided = await client.post(f"/api/v1/workflow-runs/{run_id}/approve", headers=_h(owner))
    assert decided.status_code == 200, decided.text
    await _drain(session_factory, runtime)

    [audit] = await _audits(session_factory, "workflow.tool_executed", run_id)
    assert audit.metadata_json["restricted"] is True
    assert audit.metadata_json["sources_truncated"] is True


async def test_an_agent_publication_records_the_conversation_it_read(
    client, env, provider, session_factory
):
    owner, alice, _, agent_id = await _org(client, session_factory, "pub-agent", "MEMBER")
    secret = _canary("title")
    provider.queue(calls(("create_task", {"title": secret})), text("Done."))
    resp = await client.post(
        f"/api/v1/agents/{agent_id}/execute", headers=_h(alice), json={"message": "task"}
    )
    run_id = resp.json()["run_id"]

    [audit] = await _audits(session_factory, "agent.tool_executed", run_id)
    meta = audit.metadata_json
    assert meta["tool"] == "create_task" and meta["ok"] is True
    # Alice's conversation is private to its participants.
    assert meta["source_counts"] == {"conversation": 1}
    assert meta["restricted"] is True
    assert meta["acting_role"] == "MEMBER"
    assert secret not in json.dumps(meta)


async def test_a_workflow_agent_run_starts_from_the_input_its_step_consumed(
    client, env, provider, runtime, session_factory
):
    owner, alice, _, agent_id = await _org(client, session_factory, "pub-seed", "MEMBER")
    workflow_id = await _workflow(
        client,
        owner,
        [{"id": "plan", "type": "agent", "agent_id": agent_id, "input": "Plan {{ input.topic }}"}],
    )
    provider.queue(calls(("create_task", {"title": "Plan the offsite"})), text("Planned."))
    started = await client.post(
        f"/api/v1/workflows/{workflow_id}/runs",
        headers=_h(alice),
        json={"input": {"topic": "offsite"}},
    )
    run_id = started.json()["id"]
    await _drain(session_factory, runtime)

    async with session_factory() as s:
        [step] = (
            await s.execute(
                select(WorkflowStepRun).where(WorkflowStepRun.run_id == uuid.UUID(run_id))
            )
        ).scalars()
        agent_run = await s.get(AgentRun, step.agent_run_id)
    assert agent_run is not None
    # The agent's publication happened mid-run: it already carries the step input.
    [audit] = await _audits(session_factory, "agent.tool_executed", str(agent_run.id))
    assert audit.metadata_json["source_counts"] == {"external_input": 1}
    assert audit.metadata_json["restricted"] is False
    assert agent_run.sources == [{"type": "external_input"}]
