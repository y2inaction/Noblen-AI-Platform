# ruff: noqa: F811  (pytest fixtures imported from the e2e module)
"""Milestone 8: a run records which integration connection its content came from.

The connection id is recorded, never the data the external service returned
(docs/architecture/milestone-8-provenance.md §5).
"""

from __future__ import annotations

import json
import uuid

import httpx

from app.models.run import AgentRun
from tests.agents.test_controlled_autonomy import calls, text
from tests.conftest import register_org
from tests.integrations.test_integrations_e2e import (  # noqa: F401 (fixtures)
    _MULTISTATUS,
    _agent,
    _connect,
    _execute,
    provider,
    runtime,
    services,
)


async def test_calendar_reads_record_the_connection_used(
    client,
    services,
    provider,
    session_factory,
):
    admin = await register_org(client, "prov-cal@acme.example.com", "Acme Prov")
    services.handlers["dav.example.com"] = lambda request: httpx.Response(207, text=_MULTISTATUS)
    connection = await _connect(
        client,
        admin,
        "CALDAV",
        "team",
        {"calendar_url": "https://dav.example.com/cal"},
        {"username": "ops", "password": "app-password-1"},
    )
    agent_id = await _agent(client, admin, {"list_calendar_events": None})
    provider.queue(
        calls(("list_calendar_events", {"start": "2026-09-29T00:00", "end": "2026-09-30T00:00"})),
        text("You have a supplier call."),
    )
    result = await _execute(client, admin, agent_id, "What's on tomorrow?")

    async with session_factory() as s:
        run = await s.get(AgentRun, uuid.UUID(result["run_id"]))
    assert run is not None
    assert {(r["type"], r.get("id")) for r in run.sources} == {("integration", connection["id"])}
    assert "Supplier call" not in json.dumps(run.sources)
