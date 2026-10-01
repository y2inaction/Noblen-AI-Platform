# ruff: noqa: F811  (pytest fixtures imported from the engine test module)
"""Milestone 8.6: transitive workflow provenance.

A step inherits the provenance of the upstream values its templates consume
(`steps.<id>.*`, `input.*`), an agent step passes that on to its agent run, and
the workflow run is the union of its input and its steps. Unknown or truncated
upstream provenance truncates every consumer, and is never reset to empty.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select, update

from app.models.run import AgentRun
from app.models.workflow import WorkflowRun, WorkflowStepRun
from tests.agents.test_controlled_autonomy import calls, text
from tests.conftest import register_org
from tests.workflows.test_workflow_engine import (  # noqa: F401 (fixtures)
    _drain,
    _h,
    _start,
    _workflow,
    provider,
    runtime,
    wf,
)


def _refs(sources) -> set[tuple[str, str | None]]:
    return {(s["type"], s.get("id")) for s in sources or []}


async def _steps(session_factory, run_id: str) -> dict[str, WorkflowStepRun]:
    async with session_factory() as s:
        rows = await s.execute(
            select(WorkflowStepRun).where(WorkflowStepRun.run_id == uuid.UUID(run_id))
        )
        return {row.step_id: row for row in rows.scalars()}


async def test_only_consumed_upstream_provenance_propagates(
    client,
    wf,
    provider,
    runtime,
    session_factory,
):
    admin = await register_org(client, "prov-wf@acme.example.com", "Acme Prov WF")
    agent = await client.post("/api/v1/agent-templates/executive-ai/instantiate", headers=_h(admin))
    memory = await client.post(
        "/api/v1/memories", headers=_h(admin), json={"content": "Prefers morning calls"}
    )
    memory_id = memory.json()["id"]
    workflow = await _workflow(
        client,
        admin,
        [
            {
                "id": "brief",
                "type": "agent",
                "agent_id": agent.json()["id"],
                "input": "Brief {{ input.topic }}",
            },
            {
                "id": "log",
                "type": "tool",
                "tool": "create_task",
                "arguments": {"title": "Follow up", "description": "{{ steps.brief.output.text }}"},
            },
            {"id": "count", "type": "tool", "tool": "list_tasks"},
        ],
    )
    provider.queue(calls(("recall_memories", {})), text("Call in the morning."))
    run = await _start(client, admin, workflow["id"], {"topic": "supplier"})
    await _drain(session_factory, runtime)

    steps = await _steps(session_factory, run["id"])
    memory_ref = ("memory", memory_id)
    external = ("external_input", None)
    # The agent step observed the memory and consumed the run input.
    assert _refs(steps["brief"].sources) == {memory_ref, external}
    # The templated tool step consumed the agent step's output, so it inherits it.
    assert _refs(steps["log"].sources) == {memory_ref, external}
    # A step that consumes nothing upstream inherits nothing.
    assert steps["count"].sources == []
    async with session_factory() as s:
        agent_run = await s.get(AgentRun, steps["brief"].agent_run_id)
        workflow_run = await s.get(WorkflowRun, uuid.UUID(run["id"]))
    # The agent run's content was produced from the rendered input: it carries it too.
    assert agent_run is not None and _refs(agent_run.sources) == {memory_ref, external}
    assert workflow_run is not None and _refs(workflow_run.sources) == {memory_ref, external}
    assert not any(row.sources_truncated for row in (*steps.values(), agent_run, workflow_run))


async def test_unknown_upstream_provenance_truncates_consumers(
    client,
    wf,
    runtime,
    session_factory,
):
    admin = await register_org(client, "prov-unknown@acme.example.com", "Acme Prov Unknown")
    workflow = await _workflow(
        client,
        admin,
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
    run = await _start(client, admin, workflow["id"])
    await _drain(session_factory, runtime)
    # The waiting step's provenance becomes unknown (as for a run from before M8).
    async with session_factory() as s:
        await s.execute(
            update(WorkflowStepRun)
            .where(WorkflowStepRun.run_id == uuid.UUID(run["id"]))
            .values(sources=None)
        )
        await s.commit()
    decided = await client.post(f"/api/v1/workflow-runs/{run['id']}/approve", headers=_h(admin))
    assert decided.status_code == 200, decided.text
    await _drain(session_factory, runtime)

    steps = await _steps(session_factory, run["id"])
    async with session_factory() as s:
        workflow_run = await s.get(WorkflowRun, uuid.UUID(run["id"]))
    assert workflow_run is not None and workflow_run.status == "COMPLETED"
    # Unknown upstream provenance is never turned into an apparently safe [].
    assert steps["check"].sources_truncated is True
    assert steps["log"].sources_truncated is True
    assert workflow_run.sources_truncated is True
