"""Noblen AI 3.0 (M5): the workflow engine end to end.

Deterministic: the model is scripted; API, service, engine, worker, agent runtime
and tools are real. Runs are executed by the same worker functions production uses.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select, update

from app.agents.runtime import AgentRuntime, get_agent_runtime
from app.agents.tools.seed import seed_builtin_tools
from app.ai.errors import AIProviderUnavailableError
from app.ai.gateway import AIGateway
from app.core.config import settings
from app.main import app
from app.models.membership import OrganizationMember
from app.models.work import Notification, Task
from app.models.workflow import Workflow, WorkflowRun
from app.workflows.worker import tick
from tests.agents.test_controlled_autonomy import ScriptedProvider, calls, text
from tests.conftest import auth_headers, register_org


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
async def wf(client, session_factory, runtime):
    async with session_factory() as session:
        await seed_builtin_tools(session)
        await session.commit()
    app.dependency_overrides[get_agent_runtime] = lambda: runtime
    yield
    app.dependency_overrides.pop(get_agent_runtime, None)


async def _member(client, session_factory, org_id: str, email: str, role: str) -> dict:
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
    headers = auth_headers(auth)
    if auth.get("_org"):
        headers["X-Organization-Id"] = auth["_org"]
    return headers


async def _workflow(client, auth, steps, trigger=None, activate=True) -> dict:
    definition = {"steps": steps, **({"trigger": trigger} if trigger else {})}
    resp = await client.post(
        "/api/v1/workflows", headers=_h(auth), json={"name": "Flow", "definition": definition}
    )
    assert resp.status_code == 201, resp.text
    workflow = resp.json()
    if activate:
        resp = await client.post(f"/api/v1/workflows/{workflow['id']}/activate", headers=_h(auth))
        assert resp.status_code == 200, resp.text
        workflow = resp.json()
    return workflow


async def _start(client, auth, workflow_id, input=None, **extra) -> dict:
    resp = await client.post(
        f"/api/v1/workflows/{workflow_id}/runs",
        headers=_h(auth),
        json={"input": input or {}, **extra},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


async def _drain(session_factory, runtime, limit: int = 50) -> int:
    """Run the worker until there is nothing left to do."""
    done = 0
    while done < limit and await tick(session_factory, runtime) is not None:
        done += 1
    return done


async def _run(client, auth, run_id) -> dict:
    resp = await client.get(f"/api/v1/workflow-runs/{run_id}", headers=_h(auth))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _agent(client, auth, **fields) -> str:
    resp = await client.post(
        "/api/v1/agents",
        headers=_h(auth),
        json={"name": "Writer", "system_instructions": "Write clearly.", **fields},
    )
    assert resp.status_code == 201, resp.text
    agent_id = resp.json()["id"]
    version = await client.post(f"/api/v1/agents/{agent_id}/versions", headers=_h(auth), json={})
    assert version.status_code == 201, version.text
    activated = await client.post(
        f"/api/v1/agents/{agent_id}/versions/{version.json()['id']}/activate", headers=_h(auth)
    )
    assert activated.status_code == 200, activated.text
    return agent_id


async def _tasks(session_factory) -> list[Task]:
    async with session_factory() as s:
        return list((await s.execute(select(Task).order_by(Task.created_at))).scalars())


# --------------------------------------------------------------------------- #
# Definitions and permissions
# --------------------------------------------------------------------------- #
async def test_definitions_are_validated_against_the_organization(client, wf, session_factory):
    admin = await register_org(client, "wf@acme.example.com", "Acme WF")
    other = await register_org(client, "o@other.example.com", "Other")
    foreign_agent = await _agent(client, other)

    async def create(steps):
        return await client.post(
            "/api/v1/workflows",
            headers=_h(admin),
            json={"name": "x", "definition": {"steps": steps}},
        )

    bad = [
        [{"id": "a", "type": "tool", "tool": "no_such_tool"}],
        # Needs an agent's knowledge scope: only valid inside an agent step.
        [{"id": "a", "type": "tool", "tool": "search_knowledge", "arguments": {"query": "x"}}],
        [{"id": "a", "type": "agent", "agent_id": foreign_agent, "input": "hi"}],
        [{"id": "a", "type": "tool", "tool": "list_tasks", "next": "b"}],
    ]
    for steps in bad:
        resp = await create(steps)
        assert resp.status_code == 422, resp.text

    member = await _member(
        client, session_factory, admin["organization_id"], "m@acme.example.com", "MEMBER"
    )
    resp = await client.post(
        "/api/v1/workflows",
        headers=_h(member),
        json={
            "name": "x",
            "definition": {"steps": [{"id": "a", "type": "tool", "tool": "list_tasks"}]},
        },
    )
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# Tools, conditions and templating
# --------------------------------------------------------------------------- #
async def test_tool_condition_and_templated_follow_up(client, wf, runtime, session_factory):
    admin = await register_org(client, "ops@acme.example.com", "Acme Ops")
    await client.post("/api/v1/tasks", headers=_h(admin), json={"title": "Chase invoice 42"})
    workflow = await _workflow(
        client,
        admin,
        [
            {"id": "open", "type": "tool", "tool": "list_tasks", "arguments": {"open_only": True}},
            {
                "id": "any",
                "type": "condition",
                "left": "{{ steps.open.output.total }}",
                "op": "gt",
                "right": 0,
                "then": "log",
                "else": "end",
            },
            {
                "id": "log",
                "type": "tool",
                "tool": "create_task",
                "arguments": {
                    "title": "Review {{ steps.open.output.total }} open item(s)"
                    " for {{ input.team }}",
                    "priority": "HIGH",
                },
            },
        ],
    )
    run = await _start(client, admin, workflow["id"], {"team": "Finance"})
    assert run["status"] == "QUEUED"
    await _drain(session_factory, runtime)

    detail = await _run(client, admin, run["id"])
    assert detail["status"] == "COMPLETED", detail
    assert [s["step_id"] for s in detail["steps"]] == ["open", "any", "log"]
    assert detail["context"]["steps"]["any"]["output"] == {"result": True}
    titles = [t.title for t in await _tasks(session_factory)]
    assert "Review 1 open item(s) for Finance" in titles


async def test_condition_can_end_the_run_early(client, wf, runtime, session_factory):
    admin = await register_org(client, "quiet@acme.example.com", "Acme Quiet")
    workflow = await _workflow(
        client,
        admin,
        [
            {"id": "open", "type": "tool", "tool": "list_tasks", "arguments": {"open_only": True}},
            {
                "id": "any",
                "type": "condition",
                "left": "{{ steps.open.output.total }}",
                "op": "gt",
                "right": 0,
                "then": "log",
                "else": "end",
            },
            {"id": "log", "type": "tool", "tool": "create_task", "arguments": {"title": "x"}},
        ],
    )
    run = await _start(client, admin, workflow["id"])
    await _drain(session_factory, runtime)
    detail = await _run(client, admin, run["id"])
    assert detail["status"] == "COMPLETED"
    assert [s["step_id"] for s in detail["steps"]] == ["open", "any"]
    assert await _tasks(session_factory) == []


# --------------------------------------------------------------------------- #
# Approvals
# --------------------------------------------------------------------------- #
_GATED = [
    {"id": "ok", "type": "approval", "title": "Create the task for {{ input.who }}?"},
    {
        "id": "log",
        "type": "tool",
        "tool": "create_task",
        "arguments": {"title": "For {{ input.who }}"},
    },
]


async def test_approval_step_waits_notifies_and_resumes(client, wf, runtime, session_factory):
    admin = await register_org(client, "appr@acme.example.com", "Acme Appr")
    org = admin["organization_id"]
    operator = await _member(client, session_factory, org, "op@acme.example.com", "OPERATOR")
    workflow = await _workflow(client, admin, _GATED)
    run = await _start(client, admin, workflow["id"], {"who": "Bola"})
    await _drain(session_factory, runtime)

    detail = await _run(client, admin, run["id"])
    assert detail["status"] == "WAITING"
    assert detail["steps"][0]["output"]["title"] == "Create the task for Bola?"
    assert await _tasks(session_factory) == []
    async with session_factory() as s:
        kinds = (
            (
                await s.execute(
                    select(Notification.kind).where(
                        Notification.recipient_id == uuid.UUID(operator["user"]["id"])
                    )
                )
            )
            .scalars()
            .all()
        )
    assert "workflow_approval_requested" in kinds

    member = await _member(client, session_factory, org, "mm@acme.example.com", "MEMBER")
    denied = await client.post(f"/api/v1/workflow-runs/{run['id']}/approve", headers=_h(member))
    assert denied.status_code == 403
    resp = await client.post(
        f"/api/v1/workflow-runs/{run['id']}/approve", headers=_h(operator), json={"note": "fine"}
    )
    assert resp.status_code == 200, resp.text
    again = await client.post(f"/api/v1/workflow-runs/{run['id']}/approve", headers=_h(operator))
    assert again.status_code == 409  # already decided
    await _drain(session_factory, runtime)

    detail = await _run(client, admin, run["id"])
    assert detail["status"] == "COMPLETED"
    assert detail["steps"][0]["decision"] == "approved"
    assert detail["steps"][0]["decision_note"] == "fine"
    assert [t.title for t in await _tasks(session_factory)] == ["For Bola"]


async def test_rejection_cancels_or_branches(client, wf, runtime, session_factory):
    admin = await register_org(client, "rej@acme.example.com", "Acme Rej")
    plain = await _workflow(client, admin, _GATED)
    run = await _start(client, admin, plain["id"], {"who": "Chi"})
    await _drain(session_factory, runtime)
    await client.post(f"/api/v1/workflow-runs/{run['id']}/reject", headers=_h(admin))
    await _drain(session_factory, runtime)
    detail = await _run(client, admin, run["id"])
    assert (detail["status"], detail["error_code"]) == ("CANCELLED", "approval_rejected")
    assert await _tasks(session_factory) == []

    branching = await _workflow(
        client,
        admin,
        [
            {"id": "ok", "type": "approval", "title": "Proceed?", "on_reject": "note"},
            {
                "id": "go",
                "type": "tool",
                "tool": "create_task",
                "arguments": {"title": "Go"},
                "next": "end",
            },
            {
                "id": "note",
                "type": "tool",
                "tool": "create_task",
                "arguments": {"title": "Declined"},
            },
        ],
    )
    run = await _start(client, admin, branching["id"])
    await _drain(session_factory, runtime)
    await client.post(f"/api/v1/workflow-runs/{run['id']}/reject", headers=_h(admin))
    await _drain(session_factory, runtime)
    assert (await _run(client, admin, run["id"]))["status"] == "COMPLETED"
    assert [t.title for t in await _tasks(session_factory)] == ["Declined"]


async def test_tool_steps_can_require_approval(client, wf, runtime, session_factory):
    admin = await register_org(client, "gate@acme.example.com", "Acme Gate")
    workflow = await _workflow(
        client,
        admin,
        [
            {
                "id": "log",
                "type": "tool",
                "tool": "create_task",
                "arguments": {"title": "Gated"},
                "require_approval": True,
            }
        ],
    )
    run = await _start(client, admin, workflow["id"])
    await _drain(session_factory, runtime)
    detail = await _run(client, admin, run["id"])
    assert detail["status"] == "WAITING"
    assert detail["steps"][0]["output"]["details"] == {"arguments": {"title": "Gated"}}
    await client.post(f"/api/v1/workflow-runs/{run['id']}/approve", headers=_h(admin))
    await _drain(session_factory, runtime)
    assert (await _run(client, admin, run["id"]))["status"] == "COMPLETED"
    assert [t.title for t in await _tasks(session_factory)] == ["Gated"]


async def test_independent_approval_applies_to_workflows(client, wf, runtime, session_factory):
    admin = await register_org(client, "sod@acme.example.com", "Acme SoD")
    org = admin["organization_id"]
    peer = await _member(client, session_factory, org, "peer@acme.example.com", "MANAGER")
    await client.patch(
        "/api/v1/organizations/current",
        headers=_h(admin),
        json={"require_independent_approval": True},
    )
    workflow = await _workflow(client, admin, _GATED)
    run = await _start(client, admin, workflow["id"], {"who": "Dayo"})
    await _drain(session_factory, runtime)
    own = await client.post(f"/api/v1/workflow-runs/{run['id']}/approve", headers=_h(admin))
    assert own.status_code == 403
    ok = await client.post(f"/api/v1/workflow-runs/{run['id']}/approve", headers=_h(peer))
    assert ok.status_code == 200


# --------------------------------------------------------------------------- #
# Agent steps
# --------------------------------------------------------------------------- #
async def test_agent_step_output_feeds_the_next_step(
    client, wf, provider, runtime, session_factory
):
    admin = await register_org(client, "agent@acme.example.com", "Acme Agent")
    agent_id = await _agent(client, admin)
    workflow = await _workflow(
        client,
        admin,
        [
            {
                "id": "draft",
                "type": "agent",
                "agent_id": agent_id,
                "input": "Summarise: {{ input.note }}",
            },
            {
                "id": "log",
                "type": "tool",
                "tool": "create_task",
                "arguments": {"title": "Follow up", "description": "{{ steps.draft.output.text }}"},
            },
        ],
    )
    provider.queue(text("Customer wants a refund by Friday."))
    run = await _start(client, admin, workflow["id"], {"note": "long complaint email"})
    await _drain(session_factory, runtime)

    detail = await _run(client, admin, run["id"])
    assert detail["status"] == "COMPLETED", detail
    assert "long complaint email" in provider.requests[0].messages[-1].content
    [task] = await _tasks(session_factory)
    assert task.description == "Customer wants a refund by Friday."
    agent_run_id = detail["steps"][0]["agent_run_id"]
    trace = await client.get(f"/api/v1/runs/{agent_run_id}", headers=_h(admin))
    assert trace.status_code == 200 and trace.json()["status"] == "COMPLETED"


async def test_workflow_waits_for_the_agents_own_approval(
    client, wf, provider, runtime, session_factory
):
    admin = await register_org(client, "exec@acme.example.com", "Acme Exec")
    agent = await client.post("/api/v1/agent-templates/executive-ai/instantiate", headers=_h(admin))
    agent_id = agent.json()["id"]
    workflow = await _workflow(
        client,
        admin,
        [{"id": "remind", "type": "agent", "agent_id": agent_id, "input": "Remind me"}],
    )
    provider.queue(
        calls(("notify_member", {"recipient_email": "exec@acme.example.com", "title": "Reminder"}))
    )
    run = await _start(client, admin, workflow["id"])
    await _drain(session_factory, runtime)
    detail = await _run(client, admin, run["id"])
    assert detail["status"] == "WAITING"
    agent_run = (
        await client.get(f"/api/v1/runs/{detail['steps'][0]['agent_run_id']}", headers=_h(admin))
    ).json()
    assert agent_run["status"] == "AWAITING_APPROVAL"

    approvals = (await client.get("/api/v1/approvals", headers=_h(admin))).json()
    approval_id = approvals["items"][0]["id"]
    provider.queue(text("Reminder sent."))
    resp = await client.post(f"/api/v1/approvals/{approval_id}/approve", headers=_h(admin))
    assert resp.status_code == 200, resp.text

    await _drain(session_factory, runtime)
    detail = await _run(client, admin, run["id"])
    assert detail["status"] == "COMPLETED", detail
    assert detail["context"]["steps"]["remind"]["output"]["text"] == "Reminder sent."


# --------------------------------------------------------------------------- #
# Failure handling
# --------------------------------------------------------------------------- #
async def test_retries_back_off_then_succeed(client, wf, provider, runtime, session_factory):
    admin = await register_org(client, "retry@acme.example.com", "Acme Retry")
    agent_id = await _agent(client, admin)
    workflow = await _workflow(
        client,
        admin,
        [
            {
                "id": "draft",
                "type": "agent",
                "agent_id": agent_id,
                "input": "hi",
                "retry": {"max_attempts": 2, "backoff_seconds": 60},
            }
        ],
    )
    provider.queue(AIProviderUnavailableError("down"), text("Recovered."))
    run = await _start(client, admin, workflow["id"])
    await _drain(session_factory, runtime)

    detail = await _run(client, admin, run["id"])
    assert detail["status"] == "QUEUED" and detail["current_attempt"] == 2
    assert detail["next_attempt_at"] is not None  # backing off: the worker leaves it alone
    assert detail["steps"][0]["status"] == "FAILED"

    async with session_factory() as s:
        await s.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == uuid.UUID(run["id"]))
            .values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await s.commit()
    await _drain(session_factory, runtime)
    detail = await _run(client, admin, run["id"])
    assert detail["status"] == "COMPLETED"
    assert [(s["attempt"], s["status"]) for s in detail["steps"]] == [
        (1, "FAILED"),
        (2, "SUCCEEDED"),
    ]


async def test_failure_policies_continue_and_escalate(client, wf, runtime, session_factory):
    admin = await register_org(client, "fail@acme.example.com", "Acme Fail")
    org = admin["organization_id"]
    operator = await _member(client, session_factory, org, "op2@acme.example.com", "OPERATOR")
    missing = str(uuid.uuid4())
    bad_update = {
        "id": "upd",
        "type": "tool",
        "tool": "update_task",
        "arguments": {"task_id": missing, "status": "DONE"},
    }

    cont = await _workflow(
        client,
        admin,
        [
            {**bad_update, "on_failure": "continue"},
            {
                "id": "log",
                "type": "tool",
                "tool": "create_task",
                "arguments": {"title": "After failure"},
            },
        ],
    )
    run = await _start(client, admin, cont["id"])
    await _drain(session_factory, runtime)
    detail = await _run(client, admin, run["id"])
    assert detail["status"] == "COMPLETED"
    assert detail["context"]["steps"]["upd"]["status"] == "failed"

    esc = await _workflow(client, admin, [{**bad_update, "on_failure": "escalate"}])
    run = await _start(client, admin, esc["id"])
    await _drain(session_factory, runtime)
    detail = await _run(client, admin, run["id"])
    assert (detail["status"], detail["error_code"]) == ("ESCALATED", "step_failed")
    async with session_factory() as s:
        kinds = (
            (
                await s.execute(
                    select(Notification.kind).where(
                        Notification.recipient_id == uuid.UUID(operator["user"]["id"])
                    )
                )
            )
            .scalars()
            .all()
        )
    assert "workflow_escalated" in kinds

    fail = await _workflow(client, admin, [bad_update])
    run = await _start(client, admin, fail["id"])
    await _drain(session_factory, runtime)
    assert (await _run(client, admin, run["id"]))["status"] == "FAILED"


async def test_loops_are_bounded_by_the_step_budget(
    client, wf, runtime, session_factory, monkeypatch
):
    monkeypatch.setattr(settings, "WORKFLOW_MAX_STEPS_PER_RUN", 5)
    admin = await register_org(client, "loop@acme.example.com", "Acme Loop")
    workflow = await _workflow(
        client,
        admin,
        [
            {
                "id": "spin",
                "type": "condition",
                "left": 1,
                "op": "eq",
                "right": 1,
                "then": "spin",
                "else": "end",
            }
        ],
    )
    run = await _start(client, admin, workflow["id"])
    await _drain(session_factory, runtime)
    detail = await _run(client, admin, run["id"])
    assert (detail["status"], detail["error_code"]) == ("FAILED", "step_budget_exceeded")
    assert detail["steps_executed"] == 5


# --------------------------------------------------------------------------- #
# Authority and kill switch
# --------------------------------------------------------------------------- #
async def test_runs_stop_when_their_person_loses_authority(client, wf, runtime, session_factory):
    admin = await register_org(client, "own@acme.example.com", "Acme Own")
    org = admin["organization_id"]
    manager = await _member(client, session_factory, org, "mgr@acme.example.com", "MANAGER")
    workflow = await _workflow(
        client,
        manager,
        [{"id": "log", "type": "tool", "tool": "create_task", "arguments": {"title": "x"}}],
    )
    run = await _start(client, manager, workflow["id"])
    async with session_factory() as s:
        await s.execute(
            update(OrganizationMember)
            .where(OrganizationMember.user_id == uuid.UUID(manager["user"]["id"]))
            .values(role_name="VIEWER")
        )
        await s.commit()
    await _drain(session_factory, runtime)
    detail = await _run(client, admin, run["id"])
    assert (detail["status"], detail["error_code"]) == ("FAILED", "not_authorized")
    assert await _tasks(session_factory) == []


async def test_pausing_cancels_pending_runs_and_blocks_new_ones(
    client, wf, runtime, session_factory
):
    admin = await register_org(client, "pause@acme.example.com", "Acme Pause")
    workflow = await _workflow(client, admin, _GATED)
    queued = await _start(client, admin, workflow["id"], {"who": "Efe"})
    resp = await client.post(f"/api/v1/workflows/{workflow['id']}/pause", headers=_h(admin))
    assert resp.status_code == 200 and resp.json()["status"] == "PAUSED"
    await _drain(session_factory, runtime)
    detail = await _run(client, admin, queued["id"])
    assert (detail["status"], detail["error_code"]) == ("CANCELLED", "workflow_inactive")

    blocked = await client.post(
        f"/api/v1/workflows/{workflow['id']}/runs", headers=_h(admin), json={}
    )
    assert blocked.status_code == 409


async def test_draft_versions_can_be_test_run_by_managers_only(
    client, wf, runtime, session_factory
):
    admin = await register_org(client, "draft@acme.example.com", "Acme Draft")
    member = await _member(
        client, session_factory, admin["organization_id"], "md@acme.example.com", "MEMBER"
    )
    workflow = await _workflow(
        client,
        admin,
        [{"id": "log", "type": "tool", "tool": "create_task", "arguments": {"title": "Draft ran"}}],
        activate=False,
    )
    versions = (
        await client.get(f"/api/v1/workflows/{workflow['id']}/versions", headers=_h(admin))
    ).json()
    version_id = versions[0]["id"]
    assert (
        await client.post(
            f"/api/v1/workflows/{workflow['id']}/runs",
            headers=_h(member),
            json={"version_id": version_id},
        )
    ).status_code == 403
    run = await _start(client, admin, workflow["id"], version_id=version_id)
    await _drain(session_factory, runtime)
    detail = await _run(client, admin, run["id"])
    assert detail["status"] == "COMPLETED" and detail["trigger_detail"] == {"test": True}


# --------------------------------------------------------------------------- #
# Triggers
# --------------------------------------------------------------------------- #
async def test_scheduled_workflows_run_when_due(client, wf, runtime, session_factory):
    admin = await register_org(client, "sched@acme.example.com", "Acme Sched")
    workflow = await _workflow(
        client,
        admin,
        [
            {
                "id": "log",
                "type": "tool",
                "tool": "create_task",
                "arguments": {"title": "Daily check"},
            }
        ],
        trigger={"type": "schedule", "daily_at": "08:00"},
    )
    assert workflow["trigger_type"] == "SCHEDULE" and workflow["next_run_at"] is not None
    assert await _drain(session_factory, runtime) == 0  # not due yet

    async with session_factory() as s:
        await s.execute(
            update(Workflow)
            .where(Workflow.id == uuid.UUID(workflow["id"]))
            .values(next_run_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await s.commit()
    await _drain(session_factory, runtime)
    runs = (
        await client.get(f"/api/v1/workflow-runs?workflow_id={workflow['id']}", headers=_h(admin))
    ).json()
    assert runs["total"] == 1 and runs["items"][0]["trigger_type"] == "SCHEDULE"
    assert runs["items"][0]["status"] == "COMPLETED"
    assert runs["items"][0]["initiated_by"] == admin["user"]["id"]  # the activator
    refreshed = (await client.get(f"/api/v1/workflows/{workflow['id']}", headers=_h(admin))).json()
    assert datetime.fromisoformat(refreshed["next_run_at"]).replace(tzinfo=UTC) > datetime.now(UTC)


async def test_task_events_trigger_workflows_with_a_depth_limit(
    client, wf, runtime, session_factory
):
    admin = await register_org(client, "event@acme.example.com", "Acme Event")
    # Every new task creates another task: a loop the depth guard must stop.
    workflow = await _workflow(
        client,
        admin,
        [
            {
                "id": "echo",
                "type": "tool",
                "tool": "create_task",
                "arguments": {"title": "Re: {{ input.title }}"},
            }
        ],
        trigger={"type": "event", "event": "task.created"},
    )
    resp = await client.post("/api/v1/tasks", headers=_h(admin), json={"title": "Seed"})
    assert resp.status_code == 201
    await _drain(session_factory, runtime)

    runs = (
        await client.get(f"/api/v1/workflow-runs?workflow_id={workflow['id']}", headers=_h(admin))
    ).json()
    depths = sorted(r["depth"] for r in runs["items"])
    assert depths == list(range(settings.WORKFLOW_MAX_EVENT_DEPTH + 1))
    assert all(r["status"] == "COMPLETED" for r in runs["items"])
    first = min(runs["items"], key=lambda r: r["depth"])
    assert first["input"]["title"] == "Seed" and first["trigger_detail"] == {
        "event": "task.created"
    }


async def test_task_completion_event(client, wf, runtime, session_factory):
    admin = await register_org(client, "done@acme.example.com", "Acme Done")
    await _workflow(
        client,
        admin,
        [
            {
                "id": "thanks",
                "type": "tool",
                "tool": "create_task",
                "arguments": {"title": "Close out {{ input.title }}"},
            }
        ],
        trigger={"type": "event", "event": "task.completed"},
    )
    task = (await client.post("/api/v1/tasks", headers=_h(admin), json={"title": "Ship"})).json()
    await client.patch(f"/api/v1/tasks/{task['id']}", headers=_h(admin), json={"status": "DONE"})
    await client.patch(f"/api/v1/tasks/{task['id']}", headers=_h(admin), json={"title": "Ship v2"})
    await _drain(session_factory, runtime)
    # Completing fires once; later edits of a done task do not fire again.
    assert [t.title for t in await _tasks(session_factory)] == ["Ship v2", "Close out Ship"]


async def test_workflows_are_tenant_isolated(client, wf, runtime, session_factory):
    a = await register_org(client, "ta@acme.example.com", "Tenant A")
    b = await register_org(client, "tb@other.example.com", "Tenant B")
    workflow = await _workflow(client, a, _GATED)
    run = await _start(client, a, workflow["id"], {"who": "x"})
    for path in (f"/api/v1/workflows/{workflow['id']}", f"/api/v1/workflow-runs/{run['id']}"):
        assert (await client.get(path, headers=_h(b))).status_code == 404
    assert (await client.get("/api/v1/workflows", headers=_h(b))).json()["total"] == 0
    await _drain(session_factory, runtime)
    assert (
        await client.post(f"/api/v1/workflow-runs/{run['id']}/approve", headers=_h(b))
    ).status_code == 404
    # Events in one organization never start another organization's workflows.
    await _workflow(
        client,
        a,
        [{"id": "t", "type": "tool", "tool": "list_tasks"}],
        trigger={"type": "event", "event": "task.created"},
    )
    await client.post("/api/v1/tasks", headers=_h(b), json={"title": "B's task"})
    async with session_factory() as s:
        count = len((await s.execute(select(WorkflowRun))).scalars().all())
    assert count == 1


async def test_cancel_a_waiting_run(client, wf, runtime, session_factory):
    admin = await register_org(client, "cxl@acme.example.com", "Acme Cancel")
    workflow = await _workflow(client, admin, _GATED)
    run = await _start(client, admin, workflow["id"], {"who": "Gbenga"})
    await _drain(session_factory, runtime)
    resp = await client.post(f"/api/v1/workflow-runs/{run['id']}/cancel", headers=_h(admin))
    assert resp.status_code == 200 and resp.json()["status"] == "CANCELLED"
    late = await client.post(f"/api/v1/workflow-runs/{run['id']}/approve", headers=_h(admin))
    assert late.status_code == 409
