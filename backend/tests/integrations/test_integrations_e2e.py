"""Integrations (M6) end to end: connections, tools, MCP, webhook triggers.

Real protocols, local endpoints: email goes to a real SMTP server (aiosmtpd) on
localhost; HTTP services (webhooks, CalDAV, HubSpot, an MCP server) are served by
an httpx MockTransport that records every request. The model is scripted.
"""

from __future__ import annotations

import json
import socket
import uuid
from collections.abc import Callable

import httpx
import pytest
import pytest_asyncio
from aiosmtpd.controller import Controller
from sqlalchemy import select

from app.agents.runtime import AgentRuntime, get_agent_runtime
from app.agents.tools.seed import seed_builtin_tools
from app.ai.gateway import AIGateway
from app.core.config import settings
from app.integrations import net, secrets
from app.main import app
from app.models.audit import AuditLog
from app.models.membership import OrganizationMember
from app.workflows.worker import tick
from tests.agents.test_controlled_autonomy import ScriptedProvider, calls, text
from tests.conftest import auth_headers, register_org

# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


class Services:
    """A MockTransport router: host -> handler, recording every request."""

    def __init__(self) -> None:
        self.handlers: dict[str, Callable[[httpx.Request], httpx.Response]] = {}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        handler = self.handlers.get(request.url.host)
        if handler is None:
            return httpx.Response(599)
        return handler(request)


class Mailbox:
    def __init__(self) -> None:
        self.messages: list[tuple[str, list[str], str]] = []

    async def handle_DATA(self, server, session, envelope):  # noqa: N802 (aiosmtpd API)
        self.messages.append(
            (
                envelope.mail_from,
                list(envelope.rcpt_tos),
                envelope.content.decode("utf-8", "replace"),
            )
        )
        return "250 OK"


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
async def services(client, session_factory, runtime, monkeypatch):
    monkeypatch.setattr(settings, "INTEGRATIONS_ALLOW_PRIVATE_NETWORKS", True)
    secrets._box.cache_clear()
    router = Services()
    net.set_transport(httpx.MockTransport(router))
    async with session_factory() as session:
        await seed_builtin_tools(session)
        await session.commit()
    app.dependency_overrides[get_agent_runtime] = lambda: runtime
    yield router
    app.dependency_overrides.pop(get_agent_runtime, None)
    net.set_transport(None)


@pytest.fixture
def mailbox():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    box = Mailbox()
    controller = Controller(box, hostname="127.0.0.1", port=port)
    controller.start()
    box.port = port  # type: ignore[attr-defined]
    yield box
    controller.stop()


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


async def _connect(client, auth, provider, name, config, secret=None) -> dict:
    resp = await client.post(
        "/api/v1/integrations",
        headers=_h(auth),
        json={"provider": provider, "name": name, "config": config, "secret": secret or {}},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _agent(client, auth, tools: dict[str, str | None]) -> str:
    """An active agent bound to the named tools (name -> permission mode or None)."""
    resp = await client.post(
        "/api/v1/agents", headers=_h(auth), json={"name": "Ops", "system_instructions": "Help."}
    )
    agent_id = resp.json()["id"]
    catalogue = (await client.get("/api/v1/tools?limit=200", headers=_h(auth))).json()["items"]
    ids = {t["name"]: t["id"] for t in catalogue}
    for name, mode in tools.items():
        bound = await client.post(
            f"/api/v1/agents/{agent_id}/tools",
            headers=_h(auth),
            json={"tool_id": ids[name], **({"permission_mode": mode} if mode else {})},
        )
        assert bound.status_code == 201, bound.text
    version = await client.post(f"/api/v1/agents/{agent_id}/versions", headers=_h(auth), json={})
    activated = await client.post(
        f"/api/v1/agents/{agent_id}/versions/{version.json()['id']}/activate", headers=_h(auth)
    )
    assert activated.status_code == 200, activated.text
    return agent_id


async def _execute(client, auth, agent_id, message="go") -> dict:
    resp = await client.post(
        f"/api/v1/agents/{agent_id}/execute", headers=_h(auth), json={"message": message}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _tool_result(provider: ScriptedProvider) -> dict:
    message = next(m for m in reversed(provider.requests[-1].messages) if m.role == "tool")
    return json.loads(message.content)


# --------------------------------------------------------------------------- #
# Connections
# --------------------------------------------------------------------------- #
async def test_connections_never_reveal_secrets(client, services, session_factory):
    admin = await register_org(client, "it@acme.example.com", "Acme IT")
    org = admin["organization_id"]
    created = await _connect(
        client,
        admin,
        "HUBSPOT",
        "crm",
        {},
        {"access_token": "pat-na1-SUPERSECRET-abcd"},
    )
    assert created["secret_fields"] == {"access_token": "••••abcd"}
    assert "SUPERSECRET" not in json.dumps(created)

    manager = await _member(client, session_factory, org, "mgr@acme.example.com", "MANAGER")
    viewer = await _member(client, session_factory, org, "v@acme.example.com", "VIEWER")
    listed = await client.get("/api/v1/integrations", headers=_h(viewer))
    assert listed.status_code == 200 and "SUPERSECRET" not in listed.text
    denied = await client.post(
        "/api/v1/integrations",
        headers=_h(manager),
        json={"provider": "HUBSPOT", "name": "x", "secret": {"access_token": "pat-12345678"}},
    )
    assert denied.status_code == 403

    # Secret keys merge on update; null removes. Config is validated per provider.
    updated = await client.patch(
        f"/api/v1/integrations/{created['id']}",
        headers=_h(admin),
        json={"secret": {"access_token": "pat-na1-ROTATED-9999"}},
    )
    assert updated.json()["secret_fields"] == {"access_token": "••••9999"}
    bad = [
        {"provider": "WEBHOOK", "name": "h1", "config": {"url": "ftp://x.example.com"}},
        {"provider": "SMTP", "name": "m1", "config": {"host": "smtp.example.com"}},
        {"provider": "MCP", "name": "p1", "config": {"url": "https://u:p@mcp.example.com"}},
        {"provider": "HUBSPOT", "name": "crm", "secret": {"access_token": "pat-12345678"}},
    ]
    for body in bad:
        resp = await client.post("/api/v1/integrations", headers=_h(admin), json=body)
        assert resp.status_code in (409, 422), resp.text

    # Audit entries carry the provider and changed field names, never secrets.
    async with session_factory() as s:
        audits = (await s.execute(select(AuditLog))).scalars().all()
    blob = json.dumps(
        [a.metadata_json for a in audits if a.action.startswith("integration.")], default=str
    )
    assert "SUPERSECRET" not in blob and "ROTATED" not in blob

    other = await register_org(client, "o@other.example.com", "Other")
    assert (
        await client.get(f"/api/v1/integrations/{created['id']}", headers=_h(other))
    ).status_code == 404


async def test_connection_test_endpoint_reports_failures(client, services):
    admin = await register_org(client, "t@acme.example.com", "Acme Test")
    services.handlers["api.hubapi.com"] = lambda r: httpx.Response(
        200 if r.headers["authorization"] == "Bearer pat-good-12345" else 401, json={"results": []}
    )
    good = await _connect(client, admin, "HUBSPOT", "good", {}, {"access_token": "pat-good-12345"})
    bad = await _connect(client, admin, "HUBSPOT", "bad", {}, {"access_token": "pat-bad-123456"})
    ok = (await client.post(f"/api/v1/integrations/{good['id']}/test", headers=_h(admin))).json()
    assert ok == {"ok": True, "error": None}
    failed = (await client.post(f"/api/v1/integrations/{bad['id']}/test", headers=_h(admin))).json()
    assert failed == {"ok": False, "error": "HubSpot rejected the access token."}
    shown = (await client.get(f"/api/v1/integrations/{bad['id']}", headers=_h(admin))).json()
    assert shown["last_error"] == "HubSpot rejected the access token."


# --------------------------------------------------------------------------- #
# Email
# --------------------------------------------------------------------------- #
async def test_agent_email_needs_approval_then_is_delivered(client, services, provider, mailbox):
    admin = await register_org(client, "ceo@acme.example.com", "Acme Mail")
    await _connect(
        client,
        admin,
        "SMTP",
        "mail",
        {
            "host": "127.0.0.1",
            "port": mailbox.port,
            "security": "none",
            "from_email": "office@acme.example.com",
            "from_name": "Acme Office",
        },
    )
    # Even bound as AUTO, a HIGH-risk tool waits for a person.
    agent_id = await _agent(client, admin, {"send_email": "AUTO"})
    provider.queue(
        calls(
            (
                "send_email",
                {"to": ["client@buyer.example.com"], "subject": "Quote", "body": "Here it is."},
            )
        )
    )
    paused = await _execute(client, admin, agent_id, "Email the client the quote")
    assert paused["status"] == "awaiting_approval"
    assert mailbox.messages == []

    provider.queue(text("Sent."))
    resp = await client.post(
        f"/api/v1/approvals/{paused['approval_id']}/approve", headers=_h(admin)
    )
    assert resp.status_code == 200, resp.text
    [(sender, rcpts, content)] = mailbox.messages
    assert sender == "office@acme.example.com" and rcpts == ["client@buyer.example.com"]
    assert "Subject: Quote" in content and "Here it is." in content
    assert "From: Acme Office <office@acme.example.com>" in content


async def test_email_tool_validates_and_explains_missing_connection(client, services, provider):
    admin = await register_org(client, "nomail@acme.example.com", "Acme NoMail")
    agent_id = await _agent(client, admin, {"send_email": None})
    provider.queue(calls(("send_email", {"to": ["x@y.example.com"], "subject": "s", "body": "b"})))
    paused = await _execute(client, admin, agent_id)
    provider.queue(text("Could not send."))
    await client.post(f"/api/v1/approvals/{paused['approval_id']}/approve", headers=_h(admin))
    assert "No active smtp connection" in _tool_result(provider)["error"]


# --------------------------------------------------------------------------- #
# Webhooks (outbound)
# --------------------------------------------------------------------------- #
async def test_signed_webhook_from_a_workflow(client, services, runtime, session_factory):
    admin = await register_org(client, "hook@acme.example.com", "Acme Hook")
    services.handlers["erp.example.com"] = lambda r: httpx.Response(200, json={"received": True})
    await _connect(
        client,
        admin,
        "WEBHOOK",
        "erp",
        {"url": "https://erp.example.com/noblen"},
        {"signing_secret": "whsec-123"},
    )
    definition = {
        "steps": [
            {
                "id": "notify_erp",
                "type": "tool",
                "tool": "call_webhook",
                "arguments": {"connection": "erp", "payload": {"order": "{{ input.order }}"}},
            }
        ]
    }
    workflow = (
        await client.post(
            "/api/v1/workflows", headers=_h(admin), json={"name": "ERP", "definition": definition}
        )
    ).json()
    await client.post(f"/api/v1/workflows/{workflow['id']}/activate", headers=_h(admin))
    run = (
        await client.post(
            f"/api/v1/workflows/{workflow['id']}/runs",
            headers=_h(admin),
            json={"input": {"order": 42}},
        )
    ).json()
    while await tick(session_factory, runtime):
        pass
    # HIGH risk: the workflow waited for a person before calling out.
    assert services.requests == []
    await client.post(f"/api/v1/workflow-runs/{run['id']}/approve", headers=_h(admin))
    while await tick(session_factory, runtime):
        pass
    detail = (await client.get(f"/api/v1/workflow-runs/{run['id']}", headers=_h(admin))).json()
    assert detail["status"] == "COMPLETED", detail
    [request] = services.requests
    assert json.loads(request.content) == {"order": 42}
    expected = net_signature = request.headers["x-noblen-signature"]
    from app.integrations.providers.webhook import signature

    assert net_signature == signature(
        "whsec-123", request.headers["x-noblen-timestamp"], request.content
    )
    assert expected.startswith("sha256=")


async def test_outbound_calls_to_private_networks_are_refused(
    client, services, provider, monkeypatch
):
    admin = await register_org(client, "ssrf@acme.example.com", "Acme SSRF")
    await _connect(client, admin, "WEBHOOK", "meta", {"url": "https://metadata.example.com/latest"})
    monkeypatch.setattr(settings, "INTEGRATIONS_ALLOW_PRIVATE_NETWORKS", False)

    async def to_metadata(host, port):
        return ["169.254.169.254"]

    monkeypatch.setattr(net, "resolve_host", to_metadata)
    agent_id = await _agent(client, admin, {"call_webhook": None})
    provider.queue(calls(("call_webhook", {"payload": {"x": 1}})))
    paused = await _execute(client, admin, agent_id)
    provider.queue(text("Blocked."))
    await client.post(f"/api/v1/approvals/{paused['approval_id']}/approve", headers=_h(admin))
    assert "not a public address" in _tool_result(provider)["error"]
    assert services.requests == []


# --------------------------------------------------------------------------- #
# Calendar (CalDAV) and CRM (HubSpot)
# --------------------------------------------------------------------------- #
_MULTISTATUS = """<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
 <d:response><d:href>/cal/1.ics</d:href><d:propstat><d:prop><d:getetag>"1"</d:getetag>
  <c:calendar-data>BEGIN:VCALENDAR
BEGIN:VEVENT
UID:1
DTSTART:20260929T090000Z
DTEND:20260929T100000Z
SUMMARY:Supplier call
END:VEVENT
END:VCALENDAR
</c:calendar-data></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
</d:multistatus>"""


async def test_calendar_read_and_approved_write(client, services, provider):
    admin = await register_org(client, "cal@acme.example.com", "Acme Cal")
    store: dict[str, bytes] = {}

    def caldav_server(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"].startswith("Basic ")
        if request.method == "REPORT":
            assert b"calendar-query" in request.content and b"<c:expand" in request.content
            return httpx.Response(207, text=_MULTISTATUS)
        if request.method == "PUT":
            assert request.headers["if-none-match"] == "*"
            store[request.url.path] = request.content
            return httpx.Response(201)
        return httpx.Response(405)

    services.handlers["dav.example.com"] = caldav_server
    await _connect(
        client,
        admin,
        "CALDAV",
        "team",
        {"calendar_url": "https://dav.example.com/cal"},
        {"username": "ops", "password": "app-password-1"},
    )
    agent_id = await _agent(
        client, admin, {"list_calendar_events": None, "create_calendar_event": None}
    )
    provider.queue(
        calls(("list_calendar_events", {"start": "2026-09-29T00:00", "end": "2026-09-30T00:00"})),
        text("You have a supplier call."),
    )
    await _execute(client, admin, agent_id, "What's on tomorrow?")
    listed = _tool_result(provider)
    assert [e["title"] for e in listed["events"]] == ["Supplier call"]

    provider.queue(
        calls(("create_calendar_event", {"title": "Board prep", "start": "2026-09-30T09:00"}))
    )
    paused = await _execute(client, admin, agent_id, "Book board prep")
    assert paused["status"] == "awaiting_approval" and store == {}
    provider.queue(text("Booked."))
    await client.post(f"/api/v1/approvals/{paused['approval_id']}/approve", headers=_h(admin))
    [ics] = store.values()
    # 09:00 in the organization's timezone (Africa/Lagos, UTC+1) is 08:00 UTC.
    assert b"SUMMARY:Board prep" in ics and b"DTSTART:20260930T080000Z" in ics
    assert b"DTEND:20260930T090000Z" in ics


async def test_crm_search_upsert_and_note(client, services, provider):
    admin = await register_org(client, "crm@acme.example.com", "Acme CRM")
    contacts: dict[str, dict] = {"101": {"email": "ada@buyer.example.com", "firstname": "Ada"}}
    notes: list[dict] = []

    def hubspot(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer pat-crm-123456"
        body = json.loads(request.content or b"{}")
        path = request.url.path
        if path.endswith("/contacts/search"):
            if "filterGroups" in body:
                email = body["filterGroups"][0]["filters"][0]["value"]
                hits = [(i, c) for i, c in contacts.items() if c["email"] == email]
            else:
                hits = [
                    (i, c)
                    for i, c in contacts.items()
                    if body["query"].lower() in json.dumps(c).lower()
                ]
            return httpx.Response(
                200, json={"results": [{"id": i, "properties": c} for i, c in hits]}
            )
        if path.endswith("/objects/contacts") and request.method == "POST":
            new_id = str(100 + len(contacts) + 1)
            contacts[new_id] = body["properties"]
            return httpx.Response(201, json={"id": new_id, "properties": body["properties"]})
        if "/objects/contacts/" in path and request.method == "PATCH":
            contact_id = path.rsplit("/", 1)[-1]
            contacts[contact_id].update(body["properties"])
            return httpx.Response(200, json={"id": contact_id, "properties": contacts[contact_id]})
        if path.endswith("/objects/notes"):
            notes.append(body)
            return httpx.Response(201, json={"id": "n1"})
        return httpx.Response(404)

    services.handlers["api.hubapi.com"] = hubspot
    await _connect(client, admin, "HUBSPOT", "crm", {}, {"access_token": "pat-crm-123456"})
    agent_id = await _agent(
        client,
        admin,
        {"find_crm_contacts": None, "upsert_crm_contact": "AUTO", "add_crm_note": "AUTO"},
    )
    provider.queue(calls(("find_crm_contacts", {"query": "ada"})), text("Found Ada."))
    await _execute(client, admin, agent_id)
    assert _tool_result(provider)["contacts"][0]["firstname"] == "Ada"

    provider.queue(
        calls(("upsert_crm_contact", {"email": "Bola@Buyer.example.com", "firstname": "Bola"})),
        text("Saved."),
    )
    await _execute(client, admin, agent_id)
    assert _tool_result(provider)["created"] is True
    assert any(c["email"] == "bola@buyer.example.com" for c in contacts.values())

    provider.queue(
        calls(
            ("add_crm_note", {"contact_email": "ada@buyer.example.com", "note": "Wants 20 units"})
        ),
        text("Noted."),
    )
    await _execute(client, admin, agent_id)
    [note] = notes
    assert note["properties"]["hs_note_body"] == "Wants 20 units"
    assert note["associations"][0]["to"]["id"] == "101"
    assert note["associations"][0]["types"][0]["associationTypeId"] == 202


# --------------------------------------------------------------------------- #
# MCP
# --------------------------------------------------------------------------- #
class FakeMcpServer:
    """A minimal Streamable HTTP MCP server: JSON for most replies, SSE for calls."""

    def __init__(self) -> None:
        self.tools = [
            {
                "name": "lookup_order",
                "description": "Look up an order",
                "inputSchema": {
                    "type": "object",
                    "properties": {"order_id": {"type": "string"}},
                    "required": ["order_id"],
                },
            },
            {
                "name": "delete.everything",
                "description": "Dangerous",
                "inputSchema": {"type": "object"},
            },
        ]
        self.calls: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(200)
        assert request.headers["authorization"] == "Bearer mcp-token-xyz"
        message = json.loads(request.content)
        method = message.get("method")
        if method == "initialize":
            return httpx.Response(
                200,
                headers={"Mcp-Session-Id": "sess-1"},
                json={
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "orders"}},
                },
            )
        assert request.headers.get("mcp-session-id") == "sess-1"
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/list":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": message["id"], "result": {"tools": self.tools}}
            )
        if method == "tools/call":
            self.calls.append(message["params"])
            result = {"content": [{"type": "text", "text": "Order 7: shipped"}], "isError": False}
            payload = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result})
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=f"event: message\ndata: {payload}\n\n",
            )
        return httpx.Response(400)


async def test_mcp_tools_are_imported_disabled_then_governed(
    client, services, provider, session_factory
):
    admin = await register_org(client, "mcp@acme.example.com", "Acme MCP")
    server = FakeMcpServer()
    services.handlers["mcp.example.com"] = server
    connection = await _connect(
        client,
        admin,
        "MCP",
        "orders",
        {"url": "https://mcp.example.com/mcp"},
        {"bearer_token": "mcp-token-xyz"},
    )
    synced = (
        await client.post(f"/api/v1/integrations/{connection['id']}/tools/sync", headers=_h(admin))
    ).json()
    assert {t["remote_name"] for t in synced} == {"lookup_order", "delete.everything"}
    assert all(t["enabled"] is False and t["risk_level"] == "HIGH" for t in synced)
    lookup = next(t for t in synced if t["remote_name"] == "lookup_order")
    assert lookup["name"].startswith("mcp_") and "." not in next(
        t["name"] for t in synced if t["remote_name"] == "delete.everything"
    )

    # Other organizations never see or bind another tenant's imported tools.
    other = await register_org(client, "x@other.example.com", "Other MCP")
    their_tools = (await client.get("/api/v1/tools?limit=200", headers=_h(other))).json()["items"]
    assert lookup["tool_id"] not in {t["id"] for t in their_tools}
    their_agent = (
        await client.post(
            "/api/v1/agents", headers=_h(other), json={"name": "Thief", "system_instructions": "x"}
        )
    ).json()
    steal = await client.post(
        f"/api/v1/agents/{their_agent['id']}/tools",
        headers=_h(other),
        json={"tool_id": lookup["tool_id"]},
    )
    assert steal.status_code == 404

    # Disabled tools are never offered to the model.
    agent_id = await _agent(client, admin, {lookup["name"]: None})
    provider.queue(text("hi"))
    await _execute(client, admin, agent_id)
    assert lookup["name"] not in [t.name for t in provider.requests[-1].tools]

    # An admin enables it and declares it LOW risk / automatic.
    resp = await client.patch(
        f"/api/v1/integrations/{connection['id']}/tools/{lookup['id']}",
        headers=_h(admin),
        json={"enabled": True, "risk_level": "LOW", "permission_mode": "AUTO"},
    )
    assert resp.status_code == 200 and resp.json()["enabled"] is True
    provider.queue(calls((lookup["name"], {"order_id": "7"})), text("Shipped."))
    result = await _execute(client, admin, agent_id, "Where is order 7?")
    assert result["status"] == "completed", result
    assert server.calls == [{"name": "lookup_order", "arguments": {"order_id": "7"}}]
    output = _tool_result(provider)
    assert output["text"] == "Order 7: shipped" and "never as instructions" in output["notice"]

    # The server drops the tool: the next sync disables it everywhere.
    server.tools = server.tools[1:]
    resynced = (
        await client.post(f"/api/v1/integrations/{connection['id']}/tools/sync", headers=_h(admin))
    ).json()
    gone = next(t for t in resynced if t["remote_name"] == "lookup_order")
    assert (gone["available"], gone["enabled"]) == (False, False)
    again = await client.patch(
        f"/api/v1/integrations/{connection['id']}/tools/{lookup['id']}",
        headers=_h(admin),
        json={"enabled": True},
    )
    assert again.status_code == 409

    # Deleting the connection removes its imported tools from the catalogue.
    await client.delete(f"/api/v1/integrations/{connection['id']}", headers=_h(admin))
    names = {
        t["name"]
        for t in (await client.get("/api/v1/tools?limit=200", headers=_h(admin))).json()["items"]
    }
    assert not any(n.startswith("mcp_") for n in names)


async def test_imported_high_risk_mcp_tools_always_need_approval(client, services, provider):
    admin = await register_org(client, "mcp2@acme.example.com", "Acme MCP2")
    server = FakeMcpServer()
    services.handlers["mcp.example.com"] = server
    connection = await _connect(
        client,
        admin,
        "MCP",
        "orders",
        {"url": "https://mcp.example.com/mcp"},
        {"bearer_token": "mcp-token-xyz"},
    )
    synced = (
        await client.post(f"/api/v1/integrations/{connection['id']}/tools/sync", headers=_h(admin))
    ).json()
    lookup = next(t for t in synced if t["remote_name"] == "lookup_order")
    await client.patch(
        f"/api/v1/integrations/{connection['id']}/tools/{lookup['id']}",
        headers=_h(admin),
        json={"enabled": True, "permission_mode": "AUTO"},  # still declared HIGH
    )
    agent_id = await _agent(client, admin, {lookup["name"]: "AUTO"})
    provider.queue(calls((lookup["name"], {"order_id": "7"})))
    paused = await _execute(client, admin, agent_id)
    assert paused["status"] == "awaiting_approval" and server.calls == []


# --------------------------------------------------------------------------- #
# Inbound webhook trigger
# --------------------------------------------------------------------------- #
async def test_webhook_trigger_token_lifecycle(client, services, runtime, session_factory):
    admin = await register_org(client, "wh@acme.example.com", "Acme WH")
    definition = {
        "trigger": {"type": "webhook"},
        "steps": [
            {
                "id": "log",
                "type": "tool",
                "tool": "create_task",
                "arguments": {"title": "Order from {{ input.customer }}"},
            }
        ],
    }
    workflow = (
        await client.post(
            "/api/v1/workflows",
            headers=_h(admin),
            json={"name": "Orders", "definition": definition},
        )
    ).json()
    activated = (
        await client.post(f"/api/v1/workflows/{workflow['id']}/activate", headers=_h(admin))
    ).json()
    token, path = activated["webhook_token"], activated["webhook_path"]
    assert token and path == f"/api/v1/hooks/workflows/{workflow['id']}"
    # Never shown again.
    fetched = (await client.get(f"/api/v1/workflows/{workflow['id']}", headers=_h(admin))).json()
    assert fetched["webhook_token"] is None

    body = {"customer": "Chi"}
    assert (await client.post(path, json=body)).status_code == 404
    assert (
        await client.post(path, json=body, headers={"X-Noblen-Webhook-Token": "wrong"})
    ).status_code == 404
    assert (
        await client.post(path, content=b"[1,2]", headers={"X-Noblen-Webhook-Token": token})
    ).status_code == 422
    accepted = await client.post(path, json=body, headers={"X-Noblen-Webhook-Token": token})
    assert accepted.status_code == 202, accepted.text
    while await tick(session_factory, runtime):
        pass
    run = (
        await client.get(f"/api/v1/workflow-runs/{accepted.json()['run_id']}", headers=_h(admin))
    ).json()
    assert run["status"] == "COMPLETED" and run["trigger_type"] == "WEBHOOK"
    assert run["initiated_by"] == admin["user"]["id"]
    tasks = (await client.get("/api/v1/tasks", headers=_h(admin))).json()["items"]
    assert [t["title"] for t in tasks] == ["Order from Chi"]

    # Pausing closes the hook; re-activating rotates the token.
    await client.post(f"/api/v1/workflows/{workflow['id']}/pause", headers=_h(admin))
    assert (
        await client.post(path, json=body, headers={"X-Noblen-Webhook-Token": token})
    ).status_code == 404
    rotated = (
        await client.post(f"/api/v1/workflows/{workflow['id']}/activate", headers=_h(admin))
    ).json()["webhook_token"]
    assert rotated != token
    assert (
        await client.post(path, json=body, headers={"X-Noblen-Webhook-Token": token})
    ).status_code == 404
    assert (
        await client.post(path, json=body, headers={"X-Noblen-Webhook-Token": rotated})
    ).status_code == 202


async def test_mcp_names_that_sanitise_alike_stay_distinct(client, services):
    admin = await register_org(client, "mcp3@acme.example.com", "Acme MCP3")
    server = FakeMcpServer()
    server.tools.append(
        {
            "name": "delete_everything",
            "description": "Same name once sanitised",
            "inputSchema": {"type": "object"},
        }
    )
    services.handlers["mcp.example.com"] = server
    connection = await _connect(
        client,
        admin,
        "MCP",
        "orders",
        {"url": "https://mcp.example.com/mcp"},
        {"bearer_token": "mcp-token-xyz"},
    )
    synced = await client.post(
        f"/api/v1/integrations/{connection['id']}/tools/sync", headers=_h(admin)
    )
    assert synced.status_code == 200, synced.text
    names = [t["name"] for t in synced.json()]
    assert len(names) == 3 and len(set(names)) == 3
    # A second sync is idempotent: same tools, same names.
    again = await client.post(
        f"/api/v1/integrations/{connection['id']}/tools/sync", headers=_h(admin)
    )
    assert sorted(t["name"] for t in again.json()) == sorted(names)
