"""Recovery of runs a crashed process left RUNNING (hardening).

Recovery must never repeat a side effect: interrupted agent runs and workflows
interrupted mid-tool/agent are escalated to a person; workflows interrupted at a
safe point are re-queued and finish.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select, update

from app.agents.runtime import AgentRuntime, get_agent_runtime
from app.agents.tools.seed import seed_builtin_tools
from app.agents.worker import recover_stale_runs
from app.ai.gateway import AIGateway
from app.main import app
from app.models.audit import AuditLog
from app.models.run import AgentRun, AgentRunStep
from app.models.work import Notification, Task
from app.models.workflow import WorkflowRun, WorkflowStepRun
from app.workflows.worker import tick
from tests.agents.test_controlled_autonomy import ScriptedProvider, text
from tests.conftest import auth_headers, register_org

LONG_AGO = datetime.now(UTC) - timedelta(hours=1)


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


async def _agent_run(client, auth, provider) -> str:
    agent = await client.post(
        "/api/v1/agent-templates/executive-ai/instantiate", headers=auth_headers(auth)
    )
    provider.queue(text("done"))
    result = await client.post(
        f"/api/v1/agents/{agent.json()['id']}/execute",
        headers=auth_headers(auth),
        json={"message": "hi"},
    )
    return result.json()["run_id"]


async def test_interrupted_agent_runs_are_escalated_not_retried(
    client, env, provider, session_factory
):
    admin = await register_org(client, "rec@acme.example.com", "Acme Recovery")
    stuck = await _agent_run(client, admin, provider)
    healthy = await _agent_run(client, admin, provider)
    async with session_factory() as s:
        # A crashed worker: RUNNING, untouched for an hour.
        await s.execute(
            update(AgentRun)
            .where(AgentRun.id == uuid.UUID(stuck))
            .values(status="RUNNING", updated_at=LONG_AGO)
        )
        # Still being worked on: RUNNING but recently updated.
        await s.execute(
            update(AgentRun).where(AgentRun.id == uuid.UUID(healthy)).values(status="RUNNING")
        )
        await s.commit()
    calls_before = len(provider.requests)

    result = await recover_stale_runs(session_factory)
    assert result["agent_runs_escalated"] == 1
    assert len(provider.requests) == calls_before  # nothing was re-executed

    trace = (await client.get(f"/api/v1/runs/{stuck}", headers=auth_headers(admin))).json()
    assert (trace["status"], trace["error_code"]) == ("ESCALATED", "interrupted")
    assert "not retried" in trace["escalation_reason"]
    assert trace["steps"][-1]["step_type"] == "ESCALATION"
    live = (await client.get(f"/api/v1/runs/{healthy}", headers=auth_headers(admin))).json()
    assert live["status"] == "RUNNING"

    async with session_factory() as s:
        kinds = (
            (
                await s.execute(
                    select(Notification.kind).where(
                        Notification.recipient_id == uuid.UUID(admin["user"]["id"])
                    )
                )
            )
            .scalars()
            .all()
        )
        audits = (
            (await s.execute(select(AuditLog.action).where(AuditLog.target_id == stuck)))
            .scalars()
            .all()
        )
    assert "run_escalated" in kinds and "agent.run_recovered" in audits

    # Idempotent: a second pass finds nothing.
    again = await recover_stale_runs(session_factory)
    assert again.get("agent_runs_escalated") == 0


async def _workflow_run(client, auth, steps) -> tuple[str, str]:
    workflow = (
        await client.post(
            "/api/v1/workflows",
            headers=auth_headers(auth),
            json={"name": "Flow", "definition": {"steps": steps}},
        )
    ).json()
    await client.post(f"/api/v1/workflows/{workflow['id']}/activate", headers=auth_headers(auth))
    run = (
        await client.post(
            f"/api/v1/workflows/{workflow['id']}/runs", headers=auth_headers(auth), json={}
        )
    ).json()
    return workflow["id"], run["id"]


async def _crash(session_factory, run_id: str, in_flight: tuple[str, str] | None) -> None:
    """Leave the run RUNNING and stale, optionally with a step attempt in flight."""
    async with session_factory() as s:
        run = await s.get(WorkflowRun, uuid.UUID(run_id))
        assert run is not None
        if in_flight:
            s.add(
                WorkflowStepRun(
                    organization_id=run.organization_id,
                    run_id=run.id,
                    sequence=1,
                    step_id=in_flight[0],
                    step_type=in_flight[1],
                    attempt=1,
                    status="RUNNING",
                    started_at=LONG_AGO,
                )
            )
            run.steps_executed = 1
        await s.flush()
        await s.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == run.id)
            .values(status="RUNNING", updated_at=LONG_AGO)
        )
        await s.commit()


_LOG = {"id": "log", "type": "tool", "tool": "create_task", "arguments": {"title": "Once"}}


async def test_workflow_interrupted_between_steps_is_requeued_and_finishes(
    client, env, runtime, session_factory
):
    admin = await register_org(client, "wfr@acme.example.com", "Acme WF Recovery")
    _, run_id = await _workflow_run(client, admin, [_LOG])
    await _crash(session_factory, run_id, in_flight=None)

    result = await recover_stale_runs(session_factory)
    assert result["workflow_runs_requeued"] == 1
    while await tick(session_factory, runtime):
        pass
    detail = (
        await client.get(f"/api/v1/workflow-runs/{run_id}", headers=auth_headers(admin))
    ).json()
    assert detail["status"] == "COMPLETED"
    async with session_factory() as s:
        assert [t.title for t in (await s.execute(select(Task))).scalars()] == ["Once"]


async def test_workflow_interrupted_in_a_safe_step_retries_it(
    client, env, runtime, session_factory
):
    admin = await register_org(client, "wfs@acme.example.com", "Acme WF Safe")
    check = {
        "id": "check",
        "type": "condition",
        "left": 1,
        "op": "eq",
        "right": 1,
        "then": "log",
        "else": "end",
    }
    _, run_id = await _workflow_run(client, admin, [check, _LOG])
    await _crash(session_factory, run_id, in_flight=("check", "condition"))

    assert (await recover_stale_runs(session_factory))["workflow_runs_requeued"] == 1
    while await tick(session_factory, runtime):
        pass
    detail = (
        await client.get(f"/api/v1/workflow-runs/{run_id}", headers=auth_headers(admin))
    ).json()
    assert detail["status"] == "COMPLETED"
    first = detail["steps"][0]
    assert (first["step_id"], first["status"]) == ("check", "FAILED")
    assert "Interrupted" in first["error"]


async def test_workflow_interrupted_mid_tool_is_escalated_never_repeated(
    client, env, runtime, session_factory
):
    admin = await register_org(client, "wft@acme.example.com", "Acme WF Tool")
    _, run_id = await _workflow_run(client, admin, [_LOG])
    await _crash(session_factory, run_id, in_flight=("log", "tool"))

    assert (await recover_stale_runs(session_factory))["workflow_runs_escalated"] == 1
    while await tick(session_factory, runtime):
        pass
    detail = (
        await client.get(f"/api/v1/workflow-runs/{run_id}", headers=auth_headers(admin))
    ).json()
    assert (detail["status"], detail["error_code"]) == ("ESCALATED", "interrupted")
    assert "may already have acted" in detail["error"]
    async with session_factory() as s:
        # The tool was not run again.
        assert (await s.execute(select(Task))).first() is None
        kinds = (
            (
                await s.execute(
                    select(Notification.kind).where(
                        Notification.recipient_id == uuid.UUID(admin["user"]["id"])
                    )
                )
            )
            .scalars()
            .all()
        )
    assert "workflow_escalated" in kinds


async def test_waiting_and_queued_runs_are_never_touched(client, env, runtime, session_factory):
    admin = await register_org(client, "wfw@acme.example.com", "Acme WF Waiting")
    gate = {"id": "ok", "type": "approval", "title": "Go?"}
    _, waiting = await _workflow_run(client, admin, [gate, _LOG])
    while await tick(session_factory, runtime):
        pass
    _, queued = await _workflow_run(client, admin, [_LOG])
    async with session_factory() as s:
        await s.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id.in_([uuid.UUID(waiting), uuid.UUID(queued)]))
            .values(updated_at=LONG_AGO)
        )
        await s.commit()
    result = await recover_stale_runs(session_factory)
    assert result == {
        "agent_runs_escalated": 0,
        "workflow_runs_requeued": 0,
        "workflow_runs_escalated": 0,
    }
    async with session_factory() as s:
        statuses = {str(r.id): r.status for r in (await s.execute(select(WorkflowRun))).scalars()}
        steps = (await s.execute(select(AgentRunStep))).scalars().all()
    assert statuses[waiting] == "WAITING" and statuses[queued] == "QUEUED"
    assert steps == []
