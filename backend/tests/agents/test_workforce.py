"""Noblen AI 3.0 (M2) — first AI Workforce: templates, work tools, notifications,
separation of duties and background execution.

Deterministic: the model is scripted; tools, services and API are real.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.agents.runtime import AgentRuntime, get_agent_runtime
from app.agents.tools.seed import seed_builtin_tools
from app.agents.worker import process_next
from app.ai.gateway import AIGateway
from app.main import app
from app.models.membership import OrganizationMember
from app.models.run import AgentRun
from app.models.work import Notification, Task
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
    return auth


def _h(auth: dict, org_id: str | None = None) -> dict[str, str]:
    headers = auth_headers(auth)
    if org_id:
        headers["X-Organization-Id"] = org_id
    return headers


async def _from_template(client, auth, key: str) -> dict:
    resp = await client.post(f"/api/v1/agent-templates/{key}/instantiate", headers=_h(auth))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _execute(client, auth, agent_id, message="go", **extra):
    return await client.post(
        f"/api/v1/agents/{agent_id}/execute",
        headers=_h(auth),
        json={"message": message, **extra},
    )


async def _notifications(session_factory, user_id: str) -> list[Notification]:
    async with session_factory() as s:
        rows = await s.execute(
            select(Notification).where(Notification.recipient_id == uuid.UUID(user_id))
        )
        return list(rows.scalars())


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
async def test_templates_are_listed_and_instantiate_ready_to_run(client, wf):
    auth = await register_org(client, "ceo@acme.example.com", "Acme")
    listed = (await client.get("/api/v1/agent-templates", headers=_h(auth))).json()
    assert {t["key"] for t in listed} == {"executive-ai", "customer-ai"}

    agent = await _from_template(client, auth, "executive-ai")
    assert agent["status"] == "ACTIVE" and agent["agent_type"] == "EXECUTIVE"
    bindings = (await client.get(f"/api/v1/agents/{agent['id']}/tools", headers=_h(auth))).json()
    tools = (await client.get("/api/v1/tools", headers=_h(auth))).json()["items"]
    name_of = {t["id"]: t["name"] for t in tools}
    modes = {name_of[b["tool_id"]]: b["permission_mode"] for b in bindings}
    assert {"create_task", "list_tasks", "search_knowledge", "notify_member"} <= set(modes)
    assert modes["notify_member"] == "APPROVAL_REQUIRED"


async def test_only_agent_authors_can_instantiate_templates(client, wf, session_factory):
    admin = await register_org(client, "adm@t.example.com", "Org T")
    org = admin["organization_id"]
    member = await _member(client, session_factory, org, "m@t.example.com", "MEMBER")
    resp = await client.post(
        "/api/v1/agent-templates/customer-ai/instantiate", headers=_h(member, org)
    )
    assert resp.status_code == 403
    missing = await client.post("/api/v1/agent-templates/nope/instantiate", headers=_h(admin))
    assert missing.status_code == 404


# --------------------------------------------------------------------------- #
# Executive AI scenarios
# --------------------------------------------------------------------------- #
async def test_executive_ai_captures_an_action_item_as_an_assigned_task(
    client, wf, provider, session_factory
):
    ceo = await register_org(client, "ceo2@acme.example.com", "Acme Exec")
    org = ceo["organization_id"]
    cfo = await _member(client, session_factory, org, "cfo@acme.example.com", "MANAGER")
    agent = await _from_template(client, ceo, "executive-ai")
    provider.queue(
        calls(
            (
                "create_task",
                {
                    "title": "Send Q3 board pack",
                    "assignee_email": "CFO@acme.example.com",
                    "due_at": "2026-10-03",
                    "priority": "HIGH",
                },
            )
        ),
        text("Noted: the CFO owns the Q3 board pack, due 3 October."),
    )
    body = (
        await _execute(client, ceo, agent["id"], "From today's meeting: CFO sends the Q3 pack")
    ).json()
    assert body["status"] == "completed", body

    async with session_factory() as s:
        task = (await s.execute(select(Task))).scalar_one()
    assert task.title == "Send Q3 board pack" and task.priority == "HIGH"
    assert str(task.assignee_id) == cfo["user"]["id"]
    assert str(task.created_by_agent_id) == agent["id"]
    assert str(task.source_run_id) == body["run_id"]
    assert task.due_at is not None and task.due_at.date().isoformat() == "2026-10-03"
    # The assignee is told about it.
    kinds = [n.kind for n in await _notifications(session_factory, cfo["user"]["id"])]
    assert kinds == ["task_assigned"]


async def test_executive_ai_briefing_reads_real_open_tasks(client, wf, provider):
    ceo = await register_org(client, "brief@acme.example.com", "Acme Brief")
    for title in ("Renew office lease", "Hire ops lead"):
        await client.post("/api/v1/tasks", headers=_h(ceo), json={"title": title})
    done = (await client.post("/api/v1/tasks", headers=_h(ceo), json={"title": "Old item"})).json()
    await client.patch(f"/api/v1/tasks/{done['id']}", headers=_h(ceo), json={"status": "DONE"})
    agent = await _from_template(client, ceo, "executive-ai")
    provider.queue(calls(("list_tasks", {"open_only": True})), text("Two open items."))
    await _execute(client, ceo, agent["id"], "Brief me")
    result = next(m for m in provider.requests[1].messages if m.role == "tool")
    assert "Renew office lease" in result.content and "Hire ops lead" in result.content
    assert "Old item" not in result.content


async def test_messages_to_colleagues_need_approval_then_arrive(
    client, wf, provider, session_factory
):
    ceo = await register_org(client, "ceo3@acme.example.com", "Acme Msg")
    org = ceo["organization_id"]
    op = await _member(client, session_factory, org, "op@acme.example.com", "OPERATOR")
    coo = await _member(client, session_factory, org, "coo@acme.example.com", "MEMBER")
    agent = await _from_template(client, ceo, "executive-ai")
    provider.queue(
        calls(
            (
                "notify_member",
                {"recipient_email": "coo@acme.example.com", "title": "Reminder: ops review"},
            )
        )
    )
    paused = (await _execute(client, ceo, agent["id"], "Remind the COO")).json()
    assert paused["status"] == "awaiting_approval"
    assert await _notifications(session_factory, coo["user"]["id"]) == []
    # Everyone who can decide is told an approval is waiting.
    for approver in (ceo, op):
        kinds = [n.kind for n in await _notifications(session_factory, approver["user"]["id"])]
        assert "approval_requested" in kinds

    provider.queue(text("Reminder sent."))
    resp = await client.post(
        f"/api/v1/approvals/{paused['approval_id']}/approve", headers=_h(op, org)
    )
    assert resp.status_code == 200, resp.text
    delivered = await _notifications(session_factory, coo["user"]["id"])
    assert [(n.kind, n.title) for n in delivered] == [("agent_message", "Reminder: ops review")]
    assert str(delivered[0].sent_by_agent_id) == agent["id"]


async def test_work_tools_cannot_reach_outside_the_organization(
    client, wf, provider, session_factory
):
    ceo = await register_org(client, "ceo4@acme.example.com", "Acme Scope")
    outsider = await register_org(client, "x@other.example.com", "Other Org")
    agent = await _from_template(client, ceo, "executive-ai")
    provider.queue(
        calls(("create_task", {"title": "Leak", "assignee_email": "x@other.example.com"})),
        text("Could not assign."),
    )
    await _execute(client, ceo, agent["id"])
    result = next(m for m in provider.requests[1].messages if m.role == "tool")
    assert "not an active member" in result.content
    async with session_factory() as s:
        assert (await s.execute(select(Task))).first() is None
    assert await _notifications(session_factory, outsider["user"]["id"]) == []


# --------------------------------------------------------------------------- #
# Customer AI scenarios
# --------------------------------------------------------------------------- #
async def test_customer_ai_escalates_refund_demands_and_alerts_operators(
    client, wf, provider, session_factory
):
    owner = await register_org(client, "own@shop.example.com", "Shop")
    org = owner["organization_id"]
    op = await _member(client, session_factory, org, "op@shop.example.com", "OPERATOR")
    agent = await _from_template(client, owner, "customer-ai")
    provider.queue(
        calls(("escalate_to_human", {"reason": "Customer demands a refund for order 1182"}))
    )
    body = (await _execute(client, owner, agent["id"], "I want my money back NOW")).json()
    assert body["status"] == "escalated"
    # Operators are alerted; the model's reason is run content (ADR-0035), so only
    # the person the run acts for receives it.
    op_notes = await _notifications(session_factory, op["user"]["id"])
    assert any(n.kind == "run_escalated" and n.body is None for n in op_notes)
    owner_notes = await _notifications(session_factory, owner["user"]["id"])
    assert any(n.kind == "run_escalated" and "order 1182" in (n.body or "") for n in owner_notes)


async def test_customer_ai_logs_a_follow_up_task(client, wf, provider, session_factory):
    owner = await register_org(client, "own2@shop.example.com", "Shop 2")
    agent = await _from_template(client, owner, "customer-ai")
    provider.queue(
        calls(("create_task", {"title": "Call back Ada re: bulk order", "priority": "HIGH"})),
        text("Thanks Ada, our team will call you back today."),
    )
    body = (await _execute(client, owner, agent["id"], "Bulk order of 200 units?")).json()
    assert body["status"] == "completed"
    tasks = (await client.get("/api/v1/tasks?open_only=true", headers=_h(owner))).json()
    assert [t["title"] for t in tasks["items"]] == ["Call back Ada re: bulk order"]


# --------------------------------------------------------------------------- #
# Separation of duties
# --------------------------------------------------------------------------- #
async def test_independent_approval_blocks_self_approval(client, wf, provider, session_factory):
    ceo = await register_org(client, "sod@acme.example.com", "Acme SoD")
    org = ceo["organization_id"]
    op = await _member(client, session_factory, org, "op@sod.example.com", "OPERATOR")
    updated = await client.patch(
        "/api/v1/organizations/current",
        headers=_h(ceo),
        json={"require_independent_approval": True},
    )
    assert updated.json()["require_independent_approval"] is True
    agent = await _from_template(client, ceo, "executive-ai")
    provider.queue(
        calls(("notify_member", {"recipient_email": "op@sod.example.com", "title": "Hi"}))
    )
    paused = (await _execute(client, ceo, agent["id"])).json()

    self_approve = await client.post(
        f"/api/v1/approvals/{paused['approval_id']}/approve", headers=_h(ceo)
    )
    assert self_approve.status_code == 403
    assert self_approve.json()["error"]["code"] == "independent_approval_required"
    provider.queue(text("done"))
    other = await client.post(
        f"/api/v1/approvals/{paused['approval_id']}/approve", headers=_h(op, org)
    )
    assert other.status_code == 200, other.text


async def test_self_approval_is_allowed_when_setting_is_off(client, wf, provider):
    ceo = await register_org(client, "nosod@acme.example.com", "Acme NoSoD")
    agent = await _from_template(client, ceo, "executive-ai")
    provider.queue(
        calls(("notify_member", {"recipient_email": "nosod@acme.example.com", "title": "Hi"})),
        text("done"),
    )
    paused = (await _execute(client, ceo, agent["id"])).json()
    resp = await client.post(f"/api/v1/approvals/{paused['approval_id']}/approve", headers=_h(ceo))
    assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# Background execution
# --------------------------------------------------------------------------- #
async def test_background_runs_are_queued_then_executed_by_a_worker(
    client, wf, provider, runtime, session_factory
):
    ceo = await register_org(client, "bg@acme.example.com", "Acme BG")
    agent = await _from_template(client, ceo, "executive-ai")
    resp = await _execute(client, ceo, agent["id"], "Prepare my week", background=True)
    assert resp.status_code == 202, resp.text
    queued = resp.json()
    assert queued["status"] == "queued" and provider.requests == []
    run = (await client.get(f"/api/v1/runs/{queued['run_id']}", headers=_h(ceo))).json()
    assert run["status"] == "QUEUED" and run["started_at"] is None

    provider.queue(text("Your week: three priorities."))
    assert await process_next(session_factory, runtime) == uuid.UUID(queued["run_id"])
    assert await process_next(session_factory, runtime) is None  # queue drained

    run = (await client.get(f"/api/v1/runs/{queued['run_id']}", headers=_h(ceo))).json()
    assert run["status"] == "COMPLETED" and run["model_calls"] == 1
    conversation = (
        await client.get(
            f"/api/v1/conversations/{queued['conversation_id']}/messages", headers=_h(ceo)
        )
    ).json()
    items = conversation["items"] if isinstance(conversation, dict) else conversation
    assert items[-1]["content"] == "Your week: three priorities."


async def test_queued_run_for_a_paused_agent_escalates(
    client, wf, provider, runtime, session_factory
):
    ceo = await register_org(client, "bgp@acme.example.com", "Acme BGP")
    agent = await _from_template(client, ceo, "executive-ai")
    queued = (await _execute(client, ceo, agent["id"], background=True)).json()
    await client.post(f"/api/v1/agents/{agent['id']}/pause", headers=_h(ceo))
    await process_next(session_factory, runtime)
    async with session_factory() as s:
        run = await s.get(AgentRun, uuid.UUID(queued["run_id"]))
    assert run is not None and run.status == "ESCALATED"
    assert provider.requests == []


async def test_background_is_not_available_for_test_versions(client, wf):
    ceo = await register_org(client, "bgv@acme.example.com", "Acme BGV")
    agent = await _from_template(client, ceo, "executive-ai")
    resp = await _execute(
        client, ceo, agent["id"], background=True, version_id=agent["active_version_id"]
    )
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# Tasks & notifications API
# --------------------------------------------------------------------------- #
async def test_tasks_api_is_tenant_scoped_and_permission_gated(client, wf, session_factory):
    a = await register_org(client, "a@ta.example.com", "Org TA")
    b = await register_org(client, "b@tb.example.com", "Org TB")
    task = (await client.post("/api/v1/tasks", headers=_h(a), json={"title": "A's task"})).json()
    assert (await client.get(f"/api/v1/tasks/{task['id']}", headers=_h(b))).status_code == 404
    assert (await client.get("/api/v1/tasks", headers=_h(b))).json()["total"] == 0
    patched = await client.patch(
        f"/api/v1/tasks/{task['id']}", headers=_h(b), json={"status": "DONE"}
    )
    assert patched.status_code == 404
    # Assignees must belong to the organization.
    bad = await client.post(
        "/api/v1/tasks",
        headers=_h(a),
        json={"title": "x", "assignee_id": b["user"]["id"]},
    )
    assert bad.status_code == 422
    viewer = await _member(
        client, session_factory, a["organization_id"], "v@ta.example.com", "VIEWER"
    )
    hv = _h(viewer, a["organization_id"])
    assert (await client.get("/api/v1/tasks", headers=hv)).status_code == 200
    assert (await client.post("/api/v1/tasks", headers=hv, json={"title": "no"})).status_code == 403
    done = await client.patch(f"/api/v1/tasks/{task['id']}", headers=_h(a), json={"status": "DONE"})
    assert done.json()["completed_at"] is not None


async def test_notifications_are_private_to_their_recipient(client, wf, session_factory):
    a = await register_org(client, "n@na.example.com", "Org NA")
    org = a["organization_id"]
    colleague = await _member(client, session_factory, org, "c@na.example.com", "MEMBER")
    await client.post(
        "/api/v1/tasks",
        headers=_h(a),
        json={"title": "For you", "assignee_id": colleague["user"]["id"]},
    )
    mine = (await client.get("/api/v1/notifications", headers=_h(colleague, org))).json()
    assert mine["unread"] == 1 and mine["items"][0]["kind"] == "task_assigned"
    note_id = mine["items"][0]["id"]
    # The creator cannot see or mark the colleague's notification.
    assert (await client.get("/api/v1/notifications", headers=_h(a))).json()["total"] == 0
    stolen = await client.post(f"/api/v1/notifications/{note_id}/read", headers=_h(a))
    assert stolen.status_code == 404
    ok = await client.post(f"/api/v1/notifications/{note_id}/read", headers=_h(colleague, org))
    assert ok.status_code == 204
    after = (await client.get("/api/v1/notifications", headers=_h(colleague, org))).json()
    assert after["unread"] == 0
    everything = await client.post("/api/v1/notifications/read-all", headers=_h(colleague, org))
    assert everything.json() == {"updated": 0}
