"""Run content is visible to the person the run acts for, not the organization (ADR-0035).

A run reads with its person's authority: their private memories, knowledge
restricted to them. What it was given, retrieved and produced must therefore not
become readable by other members through the run: not by viewers, not by members,
not by managers, not by admins. Metadata (status, ids, timings, error codes, cost)
stays organization-visible under the existing `:view` permissions.

Each test plants a unique canary in the private source and searches every
response body for it, so a field that leaks under a new name is still caught.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from app.agents.runtime import AgentRuntime, get_agent_runtime
from app.agents.tools.seed import seed_builtin_tools
from app.ai.gateway import AIGateway
from app.main import app
from app.models.membership import OrganizationMember
from app.workflows.worker import tick
from tests.agents.test_controlled_autonomy import ScriptedProvider, calls, text
from tests.conftest import auth_headers, register_org

ROLES = ["VIEWER", "MEMBER", "MANAGER", "ADMIN"]


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


async def _drain(session_factory, runtime) -> None:
    for _ in range(50):
        if await tick(session_factory, runtime) is None:
            return


async def _org(client, session_factory, tag: str, role: str) -> tuple[dict, dict, dict, str]:
    """An organization with its owner, Alice (MEMBER) and another member in `role`."""
    owner = await register_org(client, f"owner-{tag}@acme.example.com", f"Acme {tag}")
    org = owner["organization_id"]
    alice = await _join(client, session_factory, org, f"alice-{tag}@acme.example.com", "MEMBER")
    other = await _join(client, session_factory, org, f"other-{tag}@acme.example.com", role)
    agent = await client.post("/api/v1/agent-templates/executive-ai/instantiate", headers=_h(owner))
    assert agent.status_code in (200, 201), agent.text
    return owner, alice, other, agent.json()["id"]


async def _workflow(client, auth, steps) -> str:
    resp = await client.post(
        "/api/v1/workflows",
        headers=_h(auth),
        json={"name": "Morning brief", "definition": {"steps": steps}},
    )
    assert resp.status_code == 201, resp.text
    workflow_id = resp.json()["id"]
    resp = await client.post(f"/api/v1/workflows/{workflow_id}/activate", headers=_h(auth))
    assert resp.status_code == 200, resp.text
    return workflow_id


async def _remember(client, alice, content: str) -> None:
    saved = await client.post("/api/v1/memories", headers=_h(alice), json={"content": content})
    assert saved.status_code == 201, saved.text


def _absent(response, *canaries: str) -> None:
    for canary in canaries:
        assert canary not in response.text, f"{canary} leaked: {response.request.url}"


# --------------------------------------------------------------------------- #
# Workflow runs
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("role", ROLES)
async def test_workflow_run_content_is_private_to_its_person(
    client, env, provider, runtime, session_factory, role
):
    owner, alice, other, agent_id = await _org(client, session_factory, f"wf-{role}", role)
    memory, payload = _canary("memory"), _canary("input")
    await _remember(client, alice, memory)
    workflow_id = await _workflow(
        client,
        owner,
        [{"id": "brief", "type": "agent", "agent_id": agent_id, "input": "Brief {{ input.ref }}"}],
    )
    # Alice's run: her agent recalls her private memory and repeats it.
    provider.queue(calls(("recall_memories", {})), text(f"Your note: {memory}"))
    started = await client.post(
        f"/api/v1/workflows/{workflow_id}/runs", headers=_h(alice), json={"input": {"ref": payload}}
    )
    assert started.status_code == 202, started.text
    run_id = started.json()["id"]
    await _drain(session_factory, runtime)

    # Another member of the same organization, whatever their role, sees metadata only.
    detail = await client.get(f"/api/v1/workflow-runs/{run_id}", headers=_h(other))
    assert detail.status_code == 200, detail.text
    _absent(detail, memory, payload)
    seen = detail.json()
    assert seen["content_withheld"] is True
    assert seen["input"] is None and seen["context"] is None
    assert (seen["id"], seen["status"], seen["workflow_id"]) == (run_id, "COMPLETED", workflow_id)
    assert seen["initiated_by"] == alice["user"]["id"]
    assert seen["finished_at"] is not None
    [step] = seen["steps"]
    assert (step["step_id"], step["status"], step["output"]) == ("brief", "SUCCEEDED", None)
    assert step["agent_run_id"] is not None

    listed = await client.get("/api/v1/workflow-runs", headers=_h(other))
    assert listed.status_code == 200
    _absent(listed, memory, payload)
    [item] = listed.json()["items"]
    assert item["id"] == run_id and item["content_withheld"] is True

    # The agent run behind the step, and the operations views, do not carry it either.
    agent_run_id = step["agent_run_id"]
    for path in (f"/api/v1/runs/{agent_run_id}", "/api/v1/runs", "/api/v1/operations/overview"):
        response = await client.get(path, headers=_h(other))
        assert response.status_code == 200, (path, response.text)
        _absent(response, memory, payload)
    # Neither do their notifications.
    _absent(await client.get("/api/v1/notifications", headers=_h(other)), memory, payload)

    # The person the run acted for still sees everything.
    mine = await client.get(f"/api/v1/workflow-runs/{run_id}", headers=_h(alice))
    assert mine.status_code == 200, mine.text
    body = mine.json()
    assert body["status"] == "COMPLETED", body
    assert body["content_withheld"] is False
    assert body["input"] == {"ref": payload}
    assert memory in body["steps"][0]["output"]["text"]
    assert memory in body["context"]["steps"]["brief"]["output"]["text"]

    # Another organization cannot see the run at all.
    outsider = await register_org(client, f"out-{role}@other.example.com", "Other Org")
    for path in (f"/api/v1/workflow-runs/{run_id}", f"/api/v1/runs/{agent_run_id}"):
        response = await client.get(path, headers=_h(outsider))
        assert response.status_code == 404
        _absent(response, memory, payload)
    _absent(await client.get("/api/v1/workflow-runs", headers=_h(outsider)), memory, payload)


@pytest.mark.parametrize("role", ["VIEWER", "MANAGER", "ADMIN"])
async def test_escalated_workflow_errors_stay_with_the_person(
    client, env, provider, runtime, session_factory, role
):
    owner, alice, other, agent_id = await _org(client, session_factory, f"esc-{role}", role)
    memory = _canary("reason")
    workflow_id = await _workflow(
        client,
        owner,
        [
            {
                "id": "brief",
                "type": "agent",
                "agent_id": agent_id,
                "input": "Brief me",
                "on_failure": "escalate",
            }
        ],
    )
    # The agent escalates, quoting private context in its reason.
    provider.queue(calls(("escalate_to_human", {"reason": f"Unsure about {memory}"})))
    started = await client.post(
        f"/api/v1/workflows/{workflow_id}/runs", headers=_h(alice), json={"input": {}}
    )
    run_id = started.json()["id"]
    await _drain(session_factory, runtime)

    mine = (await client.get(f"/api/v1/workflow-runs/{run_id}", headers=_h(alice))).json()
    assert mine["status"] == "ESCALATED" and memory in mine["error"]
    alice_notes = await client.get("/api/v1/notifications", headers=_h(alice))
    assert memory in alice_notes.text

    theirs = await client.get(f"/api/v1/workflow-runs/{run_id}", headers=_h(other))
    _absent(theirs, memory)
    seen = theirs.json()
    assert seen["status"] == "ESCALATED" and seen["error_code"] == "step_failed"
    assert seen["error"] is None and seen["steps"][0]["error"] is None
    for path in ("/api/v1/workflow-runs", "/api/v1/runs", "/api/v1/operations/overview"):
        _absent(await client.get(path, headers=_h(other)), memory)
    agent_run_id = mine["steps"][0]["agent_run_id"]
    trace = await client.get(f"/api/v1/runs/{agent_run_id}", headers=_h(other))
    _absent(trace, memory)
    assert trace.json()["status"] == "ESCALATED"
    assert trace.json()["escalation_reason"] is None
    # Operators and approvers are told something needs attention, not the private reason.
    _absent(await client.get("/api/v1/notifications", headers=_h(other)), memory)
    _absent(await client.get("/api/v1/notifications", headers=_h(owner)), memory)


async def test_approvers_see_the_request_they_decide_not_the_run(
    client, env, provider, runtime, session_factory
):
    owner, alice, manager, agent_id = await _org(client, session_factory, "appr", "MANAGER")
    memory, payload = _canary("memory"), _canary("input")
    await _remember(client, alice, memory)
    workflow_id = await _workflow(
        client,
        owner,
        [
            {"id": "brief", "type": "agent", "agent_id": agent_id, "input": "{{ input.ref }}"},
            {"id": "check", "type": "approval", "title": "Send the brief?", "details": "Daily"},
        ],
    )
    provider.queue(calls(("recall_memories", {})), text(f"Your note: {memory}"))
    started = await client.post(
        f"/api/v1/workflows/{workflow_id}/runs", headers=_h(alice), json={"input": {"ref": payload}}
    )
    run_id = started.json()["id"]
    await _drain(session_factory, runtime)

    detail = await client.get(f"/api/v1/workflow-runs/{run_id}", headers=_h(manager))
    _absent(detail, memory, payload)
    seen = detail.json()
    assert seen["status"] == "WAITING" and seen["content_withheld"] is True
    brief, check = seen["steps"]
    assert brief["output"] is None
    # The approver must see exactly what they are asked to decide.
    assert check["status"] == "WAITING"
    assert check["output"] == {"title": "Send the brief?", "details": "Daily"}

    decided = await client.post(f"/api/v1/workflow-runs/{run_id}/approve", headers=_h(manager))
    assert decided.status_code == 200, decided.text
    _absent(decided, memory, payload)
    assert decided.json()["content_withheld"] is True
    await _drain(session_factory, runtime)
    after = (await client.get(f"/api/v1/workflow-runs/{run_id}", headers=_h(manager))).json()
    assert after["status"] == "COMPLETED"
    assert after["steps"][1]["output"]["decision"] == "approved"
    assert after["steps"][0]["output"] is None

    # Viewers are not approvers: they do not see the request either.
    viewer = await _join(
        client, session_factory, owner["organization_id"], "v-appr@acme.example.com", "VIEWER"
    )
    as_viewer = (await client.get(f"/api/v1/workflow-runs/{run_id}", headers=_h(viewer))).json()
    assert [s["output"] for s in as_viewer["steps"]] == [None, None]


# --------------------------------------------------------------------------- #
# Agent runs
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("role", ["VIEWER", "MEMBER", "MANAGER", "ADMIN"])
async def test_agent_escalation_reason_is_private_to_its_person(
    client, env, provider, session_factory, role
):
    owner, alice, other, agent_id = await _org(client, session_factory, f"ag-{role}", role)
    memory = _canary("reason")
    provider.queue(calls(("escalate_to_human", {"reason": f"She mentioned {memory}"})))
    result = await client.post(
        f"/api/v1/agents/{agent_id}/execute", headers=_h(alice), json={"message": "hi"}
    )
    assert result.status_code == 200, result.text
    run_id = result.json()["run_id"]

    trace = await client.get(f"/api/v1/runs/{run_id}", headers=_h(other))
    assert trace.status_code == 200
    _absent(trace, memory)
    mine = (await client.get(f"/api/v1/runs/{run_id}", headers=_h(alice))).json()
    assert memory in mine["escalation_reason"] and mine["content_withheld"] is False
    assert memory in (await client.get("/api/v1/notifications", headers=_h(alice))).text
    seen = trace.json()
    assert seen["status"] == "ESCALATED" and seen["content_withheld"] is True
    assert seen["escalation_reason"] is None
    assert [s["step_type"] for s in seen["steps"]] == [s["step_type"] for s in mine["steps"]]
    for path in ("/api/v1/runs", "/api/v1/operations/overview", "/api/v1/notifications"):
        response = await client.get(path, headers=_h(other))
        assert response.status_code == 200
        _absent(response, memory)
    overview = (await client.get("/api/v1/operations/overview", headers=_h(alice))).json()
    assert memory in overview["recent_escalations"][0]["reason"]


async def test_approving_someone_elses_action_does_not_return_their_answer(
    client, env, provider, session_factory
):
    owner, alice, manager, agent_id = await _org(client, session_factory, "act", "MANAGER")
    memory = _canary("memory")
    await _remember(client, alice, memory)
    provider.queue(
        calls(("recall_memories", {})),
        calls(("notify_member", {"recipient_email": "alice-act@acme.example.com", "title": "Hi"})),
    )
    result = await client.post(
        f"/api/v1/agents/{agent_id}/execute", headers=_h(alice), json={"message": "remind me"}
    )
    assert result.json()["status"] == "awaiting_approval", result.text
    approval_id = result.json()["approval_id"]

    provider.queue(text(f"Done. By the way: {memory}"))
    decided = await client.post(f"/api/v1/approvals/{approval_id}/approve", headers=_h(manager))
    assert decided.status_code == 200, decided.text
    _absent(decided, memory)
    execution = decided.json()["execution"]
    assert execution["status"] == "completed" and execution["message"] is None
    assert execution["content_withheld"] is True

    # Alice still gets the answer in her conversation.
    messages = await client.get(
        f"/api/v1/conversations/{result.json()['conversation_id']}/messages", headers=_h(alice)
    )
    assert f"Done. By the way: {memory}" in messages.text


async def test_trace_keeps_platform_errors_and_withholds_tool_errors(
    client, env, provider, session_factory
):
    owner, alice, admin, agent_id = await _org(client, session_factory, "trace", "ADMIN")
    leaked = _canary("argument")
    # A failed tool call's error quotes what the model sent; a denial is policy text.
    provider.queue(
        calls(("recall_memories", {leaked: "x"})),
        calls(("payments_send", {"recipient": "Me", "amount": 1})),
        text("Done."),
    )
    result = await client.post(
        f"/api/v1/agents/{agent_id}/execute", headers=_h(alice), json={"message": "go"}
    )
    run_id = result.json()["run_id"]

    mine = (await client.get(f"/api/v1/runs/{run_id}", headers=_h(alice))).json()
    failed = [s for s in mine["steps"] if s["status"] == "FAILED"]
    assert failed and leaked in failed[0]["error"]

    trace = await client.get(f"/api/v1/runs/{run_id}", headers=_h(admin))
    _absent(trace, leaked)
    steps = trace.json()["steps"]
    assert [s["error"] for s in steps if s["status"] == "FAILED"] == [None]
    denied = [s for s in steps if s["status"] == "DENIED"]
    assert denied and "not available to this agent" in denied[0]["error"]
