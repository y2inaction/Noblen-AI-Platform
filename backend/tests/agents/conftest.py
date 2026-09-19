"""Fixtures for Agent Engine tests: seeded tools + a mock-backed runtime."""

from __future__ import annotations

import pytest
import pytest_asyncio

from app.agents.runtime import AgentRuntime, get_agent_runtime
from app.agents.tools.seed import seed_builtin_tools
from app.ai.gateway import AIGateway
from app.ai.providers.mock import MockProvider
from app.main import app


def mock_gateway() -> AIGateway:
    return AIGateway(
        providers={"mock": MockProvider()},
        default_provider="mock",
        default_model="mock-1",
        default_embedding_provider="mock",
        default_embedding_model="mock-embed-1",
        max_retries=0,
    )


@pytest_asyncio.fixture
async def seeded_tools(session_factory):
    async with session_factory() as session:
        await seed_builtin_tools(session)
        await session.commit()


@pytest.fixture
def mock_runtime(client, seeded_tools):
    """Override the runtime dependency with a mock-gateway-backed runtime."""
    app.dependency_overrides[get_agent_runtime] = lambda: AgentRuntime(gateway=mock_gateway())
    yield
    app.dependency_overrides.pop(get_agent_runtime, None)


async def create_agent(client, auth, headers_fn, **overrides):
    payload = {
        "name": overrides.get("name", "Test Agent"),
        "system_instructions": overrides.get("system_instructions", "You are helpful."),
        "memory_mode": overrides.get("memory_mode", "CONVERSATION"),
    }
    resp = await client.post("/api/v1/agents", headers=headers_fn(auth), json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def tool_id_by_name(client, auth, headers_fn, name: str) -> str:
    resp = await client.get("/api/v1/tools", headers=headers_fn(auth))
    assert resp.status_code == 200, resp.text
    for item in resp.json()["items"]:
        if item["name"] == name:
            return item["id"]
    raise AssertionError(f"tool {name} not seeded")


async def make_active_agent(client, auth, headers_fn, *, tools: list[str] | None = None) -> str:
    """Create an agent, attach tools, cut a version, activate it. Returns agent_id."""
    agent = await create_agent(client, auth, headers_fn)
    agent_id = agent["id"]
    for tool_name in tools or []:
        tid = await tool_id_by_name(client, auth, headers_fn, tool_name)
        r = await client.post(
            f"/api/v1/agents/{agent_id}/tools",
            headers=headers_fn(auth),
            json={"tool_id": tid},
        )
        assert r.status_code == 201, r.text
    ver = await client.post(
        f"/api/v1/agents/{agent_id}/versions", headers=headers_fn(auth), json={}
    )
    assert ver.status_code == 201, ver.text
    act = await client.post(
        f"/api/v1/agents/{agent_id}/versions/{ver.json()['id']}/activate",
        headers=headers_fn(auth),
    )
    assert act.status_code == 200, act.text
    assert act.json()["status"] == "ACTIVE"
    return agent_id
