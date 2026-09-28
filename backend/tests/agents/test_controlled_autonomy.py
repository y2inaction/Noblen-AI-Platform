"""Noblen AI 3.0 (M1) — controlled autonomy, run traces and AI operations.

Deterministic scenarios: the model is a scripted provider (no LLM), tools are
real handler code, and tests assert on real side effects, run traces, approvals,
audit events and API responses.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.agents.runtime import AgentRuntime, get_agent_runtime
from app.agents.tools.base import ToolContext, ToolHandler, ToolResult
from app.agents.tools.builtins import BUILTIN_TOOLS
from app.agents.tools.registry import ToolRegistry, tool_registry
from app.agents.tools.seed import seed_builtin_tools
from app.ai.base import AIProvider
from app.ai.errors import AIProviderUnavailableError
from app.ai.gateway import AIGateway
from app.ai.types import GenerationRequest, GenerationResponse, ToolCall
from app.main import app
from app.models.audit import AuditLog
from app.models.membership import OrganizationMember
from app.models.tool import AgentTool, Tool
from app.rbac.permissions import Permission
from tests.agents.conftest import make_active_agent
from tests.conftest import auth_headers, register_org

# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
Script = GenerationResponse | Exception | Callable[[GenerationRequest], GenerationResponse]


class ScriptedProvider(AIProvider):
    """Replays prepared responses and asserts every request is well-formed."""

    name = "scripted"
    default_model = "scripted-1"

    def __init__(self) -> None:
        self.script: list[Script] = []
        self.requests: list[GenerationRequest] = []

    def queue(self, *items: Script) -> None:
        self.script.extend(items)

    async def generate(self, request: GenerationRequest, model: str) -> GenerationResponse:
        self.requests.append(request)
        _assert_tool_pairs_complete(request)
        item = self.script.pop(0) if self.script else text("done")
        if isinstance(item, Exception):
            raise item
        response = item(request) if callable(item) else item.model_copy(deep=True)
        response.provider, response.model = self.name, model
        return response

    def stream(self, request, model):  # pragma: no cover
        raise NotImplementedError


def _assert_tool_pairs_complete(request: GenerationRequest) -> None:
    """Every tool call must be answered before the model is called again —
    providers such as Anthropic reject the request otherwise."""
    calls = {tc.id for m in request.messages if m.role == "assistant" for tc in m.tool_calls or []}
    results = {m.tool_call_id for m in request.messages if m.role == "tool"}
    assert calls == results, f"unanswered tool calls: {calls - results}"


def text(content: str, finish_reason: str = "stop") -> GenerationResponse:
    return GenerationResponse(
        content=content,
        provider="",
        model="",
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        request_id="",
        finish_reason=finish_reason,
    )


def calls(*items: tuple[str, dict[str, Any]]) -> GenerationResponse:
    return GenerationResponse(
        content="",
        provider="",
        model="",
        input_tokens=10,
        output_tokens=2,
        total_tokens=12,
        request_id="",
        finish_reason="tool_use",
        tool_calls=[
            ToolCall(id=f"call_{name}_{uuid.uuid4().hex[:6]}", name=name, arguments=args)
            for name, args in items
        ],
    )


class Effects:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], uuid.UUID | None]] = []

    def names(self) -> list[str]:
        return [n for n, _, _ in self.calls]


def _handlers(effects: Effects) -> list[ToolHandler]:
    class CrmUpdate(ToolHandler):
        handler_identifier = name = "crm_update_lead"
        description = "Move a lead to a pipeline stage."
        input_schema = {
            "type": "object",
            "properties": {"lead_id": {"type": "string"}, "stage": {"type": "string"}},
            "required": ["lead_id", "stage"],
            "additionalProperties": False,
        }
        risk_level = "MEDIUM"
        required_permission = Permission.AGENT_RUN

        async def execute(self, context: ToolContext, arguments: dict) -> ToolResult:
            effects.calls.append((self.name, arguments, context.user_id))
            return ToolResult.success(**arguments)

    class SendPayment(ToolHandler):
        handler_identifier = name = "payments_send"
        description = "Send money to a recipient."
        input_schema = {
            "type": "object",
            "properties": {"recipient": {"type": "string"}, "amount": {"type": "number"}},
            "required": ["recipient", "amount"],
            "additionalProperties": False,
        }
        risk_level = "HIGH"
        required_permission = Permission.ORG_MANAGE_BILLING

        async def execute(self, context: ToolContext, arguments: dict) -> ToolResult:
            effects.calls.append((self.name, arguments, context.user_id))
            return ToolResult.success(status="sent", **arguments)

    class Crashing(ToolHandler):
        handler_identifier = name = "crashing_tool"
        description = "Crashes."
        input_schema = {"type": "object", "properties": {}}

        async def execute(self, context: ToolContext, arguments: dict) -> ToolResult:
            raise RuntimeError("db password=hunter2")

    return [CrmUpdate(), SendPayment(), Crashing()]


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def provider() -> ScriptedProvider:
    return ScriptedProvider()


@pytest.fixture
def effects() -> Effects:
    return Effects()


@pytest_asyncio.fixture
async def rt(client, session_factory, provider, effects):
    """Runtime with the scripted model and test tools, registered in the catalogue."""
    handlers = _handlers(effects)
    registry = ToolRegistry([*BUILTIN_TOOLS, *handlers])
    async with session_factory() as session:
        await seed_builtin_tools(session)
        for h in handlers:
            session.add(
                Tool(
                    name=h.name,
                    description=h.description,
                    input_schema=h.input_schema,
                    output_schema={},
                    permission_mode=h.default_permission_mode,
                    handler_identifier=h.handler_identifier,
                )
            )
        await session.commit()
    gateway = AIGateway(
        providers={"scripted": provider},
        default_provider="scripted",
        default_model="scripted-1",
        max_retries=0,
    )
    app.dependency_overrides[get_agent_runtime] = lambda: AgentRuntime(
        gateway=gateway, tools=registry
    )
    # The catalogue API only attaches tools with a registered handler.
    for h in handlers:
        tool_registry.register(h)
    yield
    for h in handlers:
        tool_registry._by_identifier.pop(h.handler_identifier, None)
        tool_registry._by_name.pop(h.name, None)
    app.dependency_overrides.pop(get_agent_runtime, None)


async def _execute(client, auth, agent_id, message="do the task"):
    return await client.post(
        f"/api/v1/agents/{agent_id}/execute",
        headers=auth_headers(auth),
        json={"message": message},
    )


async def _run(client, auth, run_id) -> dict:
    resp = await client.get(f"/api/v1/runs/{run_id}", headers=auth_headers(auth))
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _add_member(client, session_factory, org_id, email, role) -> dict:
    """Register a user (own org), then add them to `org_id` and select it."""
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


def _in_org(auth: dict, org_id: str) -> dict[str, str]:
    return {**auth_headers(auth), "X-Organization-Id": org_id}


async def _actions(session_factory) -> list[str]:
    async with session_factory() as s:
        return [a.action for a in (await s.execute(select(AuditLog))).scalars()]


# --------------------------------------------------------------------------- #
# Run trace
# --------------------------------------------------------------------------- #
async def test_every_execution_is_a_traced_run(client, rt, provider, session_factory):
    auth = await register_org(client, "trace@acme.example.com", "Acme")
    agent_id = await make_active_agent(client, auth, auth_headers, tools=["get_current_time"])
    provider.queue(calls(("get_current_time", {})), text("It is noon."))

    body = (await _execute(client, auth, agent_id)).json()
    assert body["status"] == "completed" and body["run_id"]
    run = await _run(client, auth, body["run_id"])
    assert run["status"] == "COMPLETED"
    assert [(s["step_type"], s["status"]) for s in run["steps"]] == [
        ("MODEL_CALL", "SUCCEEDED"),
        ("TOOL_CALL", "SUCCEEDED"),
        ("MODEL_CALL", "SUCCEEDED"),
    ]
    assert run["model_calls"] == 2 and run["tool_calls"] == 1
    assert run["input_tokens"] == 20 and run["last_provider"] == "scripted"
    # The trace stores operational facts, never model text.
    assert "noon" not in str(run["steps"])
    assert {"agent.run_started", "agent.run_completed"} <= set(await _actions(session_factory))

    listed = (await client.get("/api/v1/runs", headers=auth_headers(auth))).json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == body["run_id"]


# --------------------------------------------------------------------------- #
# Escalation
# --------------------------------------------------------------------------- #
async def test_agent_can_escalate_to_a_human(client, rt, provider):
    auth = await register_org(client, "esc@acme.example.com", "Acme Esc")
    agent_id = await make_active_agent(client, auth, auth_headers)
    # The escalation control tool is always offered to the model.
    provider.queue(calls(("escalate_to_human", {"reason": "Customer threatens legal action"})))
    body = (await _execute(client, auth, agent_id)).json()
    assert "escalate_to_human" in [t.name for t in provider.requests[0].tools]
    assert body["status"] == "escalated"
    assert body["escalation_reason"] == "Customer threatens legal action"
    assert "escalated to a human operator" in body["message"]["content"]
    run = await _run(client, auth, body["run_id"])
    assert run["status"] == "ESCALATED" and run["steps"][-1]["step_type"] == "ESCALATION"


@pytest.mark.parametrize(
    ("finish_reason", "expected"), [("refusal", "declined"), ("max_tokens", "truncated")]
)
async def test_refusal_and_truncation_escalate(client, rt, provider, finish_reason, expected):
    auth = await register_org(client, f"{finish_reason}@acme.example.com", "Acme R")
    agent_id = await make_active_agent(client, auth, auth_headers)
    provider.queue(text("partial", finish_reason=finish_reason))
    body = (await _execute(client, auth, agent_id)).json()
    assert body["status"] == "escalated" and expected in body["escalation_reason"]


async def test_provider_outage_fails_the_run_visibly(client, rt, provider):
    auth = await register_org(client, "down@acme.example.com", "Acme Down")
    agent_id = await make_active_agent(client, auth, auth_headers)
    provider.queue(AIProviderUnavailableError("down", provider="scripted"))
    resp = await _execute(client, auth, agent_id)
    assert resp.status_code == 503  # translated, not a generic 500
    assert resp.json()["error"]["code"] == "ai_provider_unavailable"
    runs = (await client.get("/api/v1/runs", headers=auth_headers(auth))).json()["items"]
    assert runs[0]["status"] == "FAILED" and runs[0]["error_code"]


# --------------------------------------------------------------------------- #
# Tool governance: risk and the initiator's permission ceiling
# --------------------------------------------------------------------------- #
async def test_high_risk_tool_needs_approval_even_when_bound_as_auto(client, rt, provider, effects):
    auth = await register_org(client, "risk@acme.example.com", "Acme Risk")
    agent_id = await make_active_agent(client, auth, auth_headers, tools=["payments_send"])
    tool_id = next(
        t["id"]
        for t in (await client.get("/api/v1/tools", headers=auth_headers(auth))).json()["items"]
        if t["name"] == "payments_send"
    )
    # Try to loosen the binding to AUTO: the code-declared HIGH risk wins.
    await client.post(
        f"/api/v1/agents/{agent_id}/tools",
        headers=auth_headers(auth),
        json={"tool_id": tool_id, "permission_mode": "AUTO"},
    )
    provider.queue(calls(("payments_send", {"recipient": "Vendor", "amount": 500})))
    body = (await _execute(client, auth, agent_id)).json()
    assert body["status"] == "awaiting_approval"
    assert effects.calls == []
    approval = (
        await client.get(f"/api/v1/approvals/{body['approval_id']}", headers=auth_headers(auth))
    ).json()
    assert approval["risk_level"] == "HIGH" and approval["run_id"] == body["run_id"]


async def test_agent_cannot_exceed_the_initiating_users_permissions(
    client, rt, provider, effects, session_factory
):
    admin = await register_org(client, "boss@acme.example.com", "Acme Perm")
    org_id = admin["organization_id"]
    agent_id = await make_active_agent(client, admin, auth_headers, tools=["payments_send"])
    member = await _add_member(client, session_factory, org_id, "m@acme.example.com", "MEMBER")

    provider.queue(calls(("payments_send", {"recipient": "Me", "amount": 10**6})), text("No."))
    resp = await client.post(
        f"/api/v1/agents/{agent_id}/execute",
        headers=_in_org(member, org_id),
        json={"message": "pay me"},
    )
    body = resp.json()
    # Denied outright (no approval request) because MEMBER lacks org:manage_billing.
    assert body["status"] == "completed", body
    run = await _run(client, admin, body["run_id"])
    denied = [s for s in run["steps"] if s["status"] == "DENIED"]
    assert denied and "org:manage_billing" in denied[0]["error"]
    assert effects.calls == []
    assert "agent.tool_denied" in await _actions(session_factory)


async def test_tool_crash_is_contained_without_leaking_internals(client, rt, provider):
    auth = await register_org(client, "crash@acme.example.com", "Acme Crash")
    agent_id = await make_active_agent(client, auth, auth_headers, tools=["crashing_tool"])
    provider.queue(calls(("crashing_tool", {})), text("Reported the failure."))
    body = (await _execute(client, auth, agent_id)).json()
    assert body["status"] == "completed"
    tool_result = next(m for m in provider.requests[1].messages if m.role == "tool")
    assert "hunter2" not in tool_result.content and "RuntimeError" in tool_result.content
    run = await _run(client, auth, body["run_id"])
    assert "hunter2" not in str(run)


# --------------------------------------------------------------------------- #
# Approvals: decisions, resume correctness, kill switch
# --------------------------------------------------------------------------- #
async def _paused_payment(client, auth, provider, *extra: tuple[str, dict]) -> dict:
    agent_id = await make_active_agent(
        client, auth, auth_headers, tools=["payments_send", "crm_update_lead"]
    )
    provider.queue(calls(("payments_send", {"recipient": "Vendor", "amount": 900}), *extra))
    body = (await _execute(client, auth, agent_id)).json()
    assert body["status"] == "awaiting_approval", body
    body["agent_id"] = agent_id
    return body


async def test_resume_answers_every_tool_call_of_the_paused_turn(client, rt, provider, effects):
    """Regression: a turn with [gated call, other call] used to resume with the
    second call unanswered, which real providers reject."""
    auth = await register_org(client, "turn@acme.example.com", "Acme Turn")
    paused = await _paused_payment(
        client, auth, provider, ("crm_update_lead", {"lead_id": "L1", "stage": "won"})
    )
    provider.queue(text("Paid and updated."))
    decision = await client.post(
        f"/api/v1/approvals/{paused['approval_id']}/approve", headers=auth_headers(auth)
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["execution"]["status"] == "completed"
    assert effects.names() == ["payments_send", "crm_update_lead"]
    # The follow-up model call was well-formed (asserted inside the provider).
    assert len(provider.requests) == 2


async def test_resume_keeps_run_context_in_no_memory_mode(client, rt, provider, effects):
    """Regression: with memory mode NONE, resuming reloaded only the user message,
    so the model never saw its own tool call or the approved result."""
    auth = await register_org(client, "nomem@acme.example.com", "Acme NoMem")
    agent = (
        await client.post(
            "/api/v1/agents",
            headers=auth_headers(auth),
            json={"name": "NoMem", "system_instructions": "x", "memory_mode": "NONE"},
        )
    ).json()
    tool_id = next(
        t["id"]
        for t in (await client.get("/api/v1/tools", headers=auth_headers(auth))).json()["items"]
        if t["name"] == "payments_send"
    )
    await client.post(
        f"/api/v1/agents/{agent['id']}/tools",
        headers=auth_headers(auth),
        json={"tool_id": tool_id},
    )
    ver = (
        await client.post(
            f"/api/v1/agents/{agent['id']}/versions", headers=auth_headers(auth), json={}
        )
    ).json()
    await client.post(
        f"/api/v1/agents/{agent['id']}/versions/{ver['id']}/activate", headers=auth_headers(auth)
    )
    provider.queue(calls(("payments_send", {"recipient": "V", "amount": 1})), text("ok"))
    body = (await _execute(client, auth, agent["id"])).json()
    await client.post(
        f"/api/v1/approvals/{body['approval_id']}/approve", headers=auth_headers(auth)
    )
    final = provider.requests[-1]
    assert [m.role for m in final.messages] == ["user", "assistant", "tool"]
    assert '"status": "sent"' in final.messages[-1].content


async def test_approved_action_runs_on_behalf_of_the_initiator(
    client, rt, provider, effects, session_factory
):
    admin = await register_org(client, "init@acme.example.com", "Acme Init")
    org_id = admin["organization_id"]
    operator = await _add_member(client, session_factory, org_id, "op@acme.example.com", "OPERATOR")
    paused = await _paused_payment(client, admin, provider)
    provider.queue(text("done"))
    resp = await client.post(
        f"/api/v1/approvals/{paused['approval_id']}/approve",
        headers=_in_org(operator, org_id),
        json={"note": "Invoice checked"},
    )
    assert resp.status_code == 200, resp.text
    approval = resp.json()["approval"]
    assert approval["approved_by"] == operator["user"]["id"]
    assert approval["decision_note"] == "Invoice checked"
    # Executed as the initiator (admin), not as the reviewer.
    assert effects.calls[0][2] == uuid.UUID(admin["user"]["id"])


async def test_modify_executes_reviewer_arguments_after_validation(client, rt, provider, effects):
    auth = await register_org(client, "modify@acme.example.com", "Acme Modify")
    paused = await _paused_payment(client, auth, provider)
    url = f"/api/v1/approvals/{paused['approval_id']}/modify"
    bad = await client.post(url, headers=auth_headers(auth), json={"arguments": {"amount": 1}})
    assert bad.status_code == 422
    provider.queue(text("done"))
    ok = await client.post(
        url,
        headers=auth_headers(auth),
        json={"arguments": {"recipient": "Vendor", "amount": 90}, "note": "Pay 90, not 900"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["approval"]["modified_payload"] == {"recipient": "Vendor", "amount": 90}
    assert effects.calls[0][1] == {"recipient": "Vendor", "amount": 90}


async def test_reject_note_reaches_the_agent_and_nothing_runs(client, rt, provider, effects):
    auth = await register_org(client, "reject@acme.example.com", "Acme Reject")
    paused = await _paused_payment(client, auth, provider)
    provider.queue(text("Understood."))
    resp = await client.post(
        f"/api/v1/approvals/{paused['approval_id']}/reject",
        headers=auth_headers(auth),
        json={"note": "Wrong vendor"},
    )
    assert resp.json()["execution"]["status"] == "completed"
    assert effects.calls == []
    tool_msg = next(m for m in provider.requests[-1].messages if m.role == "tool")
    assert "rejected" in tool_msg.content and "Wrong vendor" in tool_msg.content


async def test_pausing_the_agent_stops_a_run_awaiting_approval(client, rt, provider, effects):
    auth = await register_org(client, "kill@acme.example.com", "Acme Kill")
    paused = await _paused_payment(client, auth, provider)
    pause = await client.post(
        f"/api/v1/agents/{paused['agent_id']}/pause", headers=auth_headers(auth)
    )
    assert pause.status_code == 200, pause.text
    resp = await client.post(
        f"/api/v1/approvals/{paused['approval_id']}/approve", headers=auth_headers(auth)
    )
    assert resp.json()["execution"]["status"] == "escalated"
    assert effects.calls == []


async def test_disabling_a_tool_blocks_an_already_requested_action(
    client, rt, provider, effects, session_factory
):
    auth = await register_org(client, "disable@acme.example.com", "Acme Disable")
    paused = await _paused_payment(client, auth, provider)
    async with session_factory() as s:
        binding = (
            await s.execute(
                select(AgentTool)
                .join(Tool, Tool.id == AgentTool.tool_id)
                .where(
                    AgentTool.agent_id == uuid.UUID(paused["agent_id"]),
                    Tool.name == "payments_send",
                )
            )
        ).scalar_one()
        binding.permission_mode = "DISABLED"
        await s.commit()
    provider.queue(text("Could not pay."))
    resp = await client.post(
        f"/api/v1/approvals/{paused['approval_id']}/approve", headers=auth_headers(auth)
    )
    assert resp.json()["execution"]["status"] == "completed"
    assert effects.calls == []


# --------------------------------------------------------------------------- #
# AI Operations + security gates
# --------------------------------------------------------------------------- #
async def test_operations_overview_summarises_the_workforce(client, rt, provider):
    auth = await register_org(client, "ops@acme.example.com", "Acme Ops")
    agent_id = await make_active_agent(
        client, auth, auth_headers, tools=["payments_send", "crashing_tool"]
    )
    provider.queue(text("ok"))
    await _execute(client, auth, agent_id)
    provider.queue(calls(("escalate_to_human", {"reason": "unsure"})))
    await _execute(client, auth, agent_id)
    provider.queue(calls(("crashing_tool", {})), text("reported"))
    await _execute(client, auth, agent_id)
    provider.queue(calls(("payments_send", {"recipient": "X", "amount": 1})))
    await _execute(client, auth, agent_id)

    resp = await client.get("/api/v1/operations/overview", headers=auth_headers(auth))
    assert resp.status_code == 200, resp.text
    ov = resp.json()
    assert ov["runs_by_status"] == {"COMPLETED": 2, "ESCALATED": 1, "AWAITING_APPROVAL": 1}
    assert ov["success_rate"] == round(2 / 3, 4)
    assert ov["escalation_rate"] == round(1 / 3, 4)
    assert ov["pending_approvals"] == 1
    assert ov["tool_failures"] == 1
    assert ov["recent_escalations"][0]["reason"] == "unsure"
    assert ov["usage_by_model"][0]["model"] == "scripted-1"


async def test_runs_and_operations_are_tenant_isolated(client, rt, provider):
    a = await register_org(client, "a@orga.example.com", "Org A")
    b = await register_org(client, "b@orgb.example.com", "Org B")
    agent_id = await make_active_agent(client, a, auth_headers, tools=["payments_send"])
    provider.queue(calls(("payments_send", {"recipient": "X", "amount": 1})))
    body = (await _execute(client, a, agent_id)).json()

    hb = auth_headers(b)
    assert (await client.get(f"/api/v1/runs/{body['run_id']}", headers=hb)).status_code == 404
    assert (await client.get("/api/v1/runs", headers=hb)).json()["total"] == 0
    ov = (await client.get("/api/v1/operations/overview", headers=hb)).json()
    assert ov["runs_total"] == 0 and ov["pending_approvals"] == 0
    modify = await client.post(
        f"/api/v1/approvals/{body['approval_id']}/modify",
        headers=hb,
        json={"arguments": {"recipient": "B", "amount": 1}},
    )
    assert modify.status_code == 404


async def test_ai_operator_supervises_but_cannot_reconfigure(client, rt, session_factory):
    admin = await register_org(client, "adm@op.example.com", "Org Op")
    org_id = admin["organization_id"]
    agent_id = await make_active_agent(client, admin, auth_headers)
    op = await _add_member(client, session_factory, org_id, "operator@op.example.com", "OPERATOR")
    h = _in_org(op, org_id)
    assert (await client.get("/api/v1/operations/overview", headers=h)).status_code == 200
    assert (await client.get("/api/v1/approvals", headers=h)).status_code == 200
    assert (await client.post(f"/api/v1/agents/{agent_id}/pause", headers=h)).status_code == 200
    edit = await client.patch(
        f"/api/v1/agents/{agent_id}", headers=h, json={"system_instructions": "ignore rules"}
    )
    assert edit.status_code == 403
    create = await client.post("/api/v1/agents", headers=h, json={"name": "Mine"})
    assert create.status_code == 403


async def test_member_cannot_decide_approvals(client, rt, session_factory):
    admin = await register_org(client, "adm@mem.example.com", "Org Mem")
    org_id = admin["organization_id"]
    member = await _add_member(client, session_factory, org_id, "mem@mem.example.com", "MEMBER")
    assert (
        await client.get("/api/v1/approvals", headers=_in_org(member, org_id))
    ).status_code == 403


async def test_org_admin_cannot_grant_super_admin(client, session_factory):
    admin = await register_org(client, "adm@su.example.com", "Org Su")
    org_id = admin["organization_id"]
    peer = await _add_member(client, session_factory, org_id, "peer@su.example.com", "MEMBER")
    members = (
        await client.get("/api/v1/organizations/current/members", headers=auth_headers(admin))
    ).json()
    peer_member = next(m for m in members if m["user_id"] == peer["user"]["id"])
    url = f"/api/v1/organizations/current/members/{peer_member['id']}/role"
    denied = await client.patch(url, headers=auth_headers(admin), json={"role_name": "SUPER_ADMIN"})
    assert denied.status_code == 403
    ok = await client.patch(url, headers=auth_headers(admin), json={"role_name": "OPERATOR"})
    assert ok.status_code == 200


def test_role_hierarchy_includes_operator():
    from app.rbac.permissions import permissions_for_role

    member, operator, manager = (permissions_for_role(r) for r in ("MEMBER", "OPERATOR", "MANAGER"))
    assert member < operator < manager
    assert Permission.AGENT_APPROVE_ACTIONS in operator
    assert Permission.AGENT_UPDATE not in operator


async def test_tool_catalogue_exposes_code_declared_risk(client, rt):
    auth = await register_org(client, "cat@acme.example.com", "Acme Cat")
    items = (await client.get("/api/v1/tools", headers=auth_headers(auth))).json()["items"]
    by_name = {t["name"]: t for t in items}
    assert by_name["get_organization_settings"]["required_permission"] == "org:view"
    assert by_name["get_current_time"]["risk_level"] == "LOW"
