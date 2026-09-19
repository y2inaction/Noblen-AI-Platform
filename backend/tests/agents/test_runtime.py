"""Agent Runtime tests: loop, AUTO tools, approval pause/resume, and limits."""

import uuid

import pytest
from sqlalchemy import select

from app.agents import approvals as approval_service
from app.agents import conversations as conversation_service
from app.agents import registry
from app.agents.errors import RuntimeLimitExceeded
from app.agents.runtime import AgentRuntime
from app.agents.tools.seed import seed_builtin_tools
from app.ai.base import AIProvider
from app.ai.gateway import AIGateway
from app.ai.providers.mock import MockProvider
from app.ai.types import GenerationResponse, ToolCall
from app.models.approval import Approval
from app.models.conversation import ConversationMessage
from app.models.enums import ApprovalStatus
from app.models.organization import Organization
from app.models.tool import AgentTool, Tool


def _runtime() -> AgentRuntime:
    gw = AIGateway(
        providers={"mock": MockProvider()},
        default_provider="mock",
        default_model="mock-1",
        max_retries=0,
    )
    return AgentRuntime(gateway=gw)


async def _setup(db, *, tools=()):
    org = Organization(name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}")
    db.add(org)
    await db.flush()
    user_id = uuid.uuid4()
    await seed_builtin_tools(db)
    agent = await registry.create_agent(db, org.id, user_id, name="Bot")
    for tname in tools:
        tool = (await db.execute(select(Tool).where(Tool.name == tname))).scalar_one()
        db.add(AgentTool(agent_id=agent.id, tool_id=tool.id, enabled=True))
    await db.flush()
    version = await registry.create_version(db, org.id, agent.id, user_id)
    await registry.activate_version(db, org.id, agent.id, version.id)
    conv = await conversation_service.create_conversation(db, org.id, user_id, agent_id=agent.id)
    return org.id, user_id, agent.id, conv.id


async def _messages(db, org_id, conv_id):
    rows = (
        (
            await db.execute(
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == conv_id)
                .order_by(ConversationMessage.sequence.asc())
            )
        )
        .scalars()
        .all()
    )
    return rows


@pytest.mark.asyncio
async def test_plain_completion(db_session):
    org_id, user_id, agent_id, conv_id = await _setup(db_session)
    result = await _runtime().execute(
        db_session,
        organization_id=org_id,
        user_id=user_id,
        agent_id=agent_id,
        conversation_id=conv_id,
        input_message="hello agent",
    )
    assert result.status == "completed"
    assert result.message["role"] == "assistant"
    assert "hello agent" in result.message["content"]
    assert result.usage["requests"] == 1


@pytest.mark.asyncio
async def test_auto_tool_executes_and_continues(db_session):
    org_id, user_id, agent_id, conv_id = await _setup(db_session, tools=["get_current_time"])
    result = await _runtime().execute(
        db_session,
        organization_id=org_id,
        user_id=user_id,
        agent_id=agent_id,
        conversation_id=conv_id,
        input_message="what time is it? [[tool:get_current_time]]",
    )
    assert result.status == "completed"
    roles = [m.role for m in await _messages(db_session, org_id, conv_id)]
    # user -> assistant(tool_call) -> tool(result) -> assistant(final)
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert result.usage["requests"] == 2


@pytest.mark.asyncio
async def test_approval_required_pauses_then_resumes(db_session):
    org_id, user_id, agent_id, conv_id = await _setup(db_session, tools=["echo"])
    rt = _runtime()
    result = await rt.execute(
        db_session,
        organization_id=org_id,
        user_id=user_id,
        agent_id=agent_id,
        conversation_id=conv_id,
        input_message='please [[tool:echo|{"text":"hi there"}]]',
    )
    assert result.status == "awaiting_approval"
    assert result.approval_id is not None
    assert result.tool_name == "echo"

    approval = (
        await db_session.execute(select(Approval).where(Approval.id == result.approval_id))
    ).scalar_one()
    assert approval.status == ApprovalStatus.PENDING.value
    # No tool result message yet — execution stopped before running the tool.
    roles = [m.role for m in await _messages(db_session, org_id, conv_id)]
    assert roles == ["user", "assistant"]

    # Approve and resume.
    approver = uuid.uuid4()
    approval = await approval_service.approve(db_session, org_id, approval.id, approver)
    resumed = await rt.resume_after_approval(
        db_session, organization_id=org_id, user_id=approver, approval=approval, approved=True
    )
    assert resumed.status == "completed"
    msgs = await _messages(db_session, org_id, conv_id)
    tool_msgs = [m for m in msgs if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert "hi there" in tool_msgs[0].content


@pytest.mark.asyncio
async def test_rejected_approval_does_not_execute_tool(db_session):
    org_id, user_id, agent_id, conv_id = await _setup(db_session, tools=["echo"])
    rt = _runtime()
    result = await rt.execute(
        db_session,
        organization_id=org_id,
        user_id=user_id,
        agent_id=agent_id,
        conversation_id=conv_id,
        input_message='[[tool:echo|{"text":"secret"}]]',
    )
    approval = (
        await db_session.execute(select(Approval).where(Approval.id == result.approval_id))
    ).scalar_one()
    approver = uuid.uuid4()
    approval = await approval_service.reject(db_session, org_id, approval.id, approver)
    resumed = await rt.resume_after_approval(
        db_session, organization_id=org_id, user_id=approver, approval=approval, approved=False
    )
    assert resumed.status == "completed"
    tool_msgs = [m for m in await _messages(db_session, org_id, conv_id) if m.role == "tool"]
    # A tool message exists but marks rejection — the echo never ran with the text.
    assert len(tool_msgs) == 1
    assert "rejected" in tool_msgs[0].content.lower()
    assert "secret" not in tool_msgs[0].content


@pytest.mark.asyncio
async def test_runtime_limit_exceeded_on_endless_tool_calls(db_session):
    """A misbehaving provider that always requests a tool must be stopped."""

    class AlwaysToolProvider(AIProvider):
        name = "loop"
        default_model = "loop-1"

        async def generate(self, request, model):
            return GenerationResponse(
                content="",
                provider=self.name,
                model=model,
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                request_id="",
                finish_reason="tool_use",
                tool_calls=[
                    ToolCall(id=f"t_{uuid.uuid4().hex[:6]}", name="get_current_time", arguments={})
                ],
            )

        def stream(self, request, model):  # pragma: no cover
            raise NotImplementedError

    gw = AIGateway(
        providers={"loop": AlwaysToolProvider()},
        default_provider="loop",
        default_model="loop-1",
        max_retries=0,
    )
    org_id, user_id, agent_id, conv_id = await _setup(db_session, tools=["get_current_time"])
    with pytest.raises(RuntimeLimitExceeded):
        await AgentRuntime(gateway=gw).execute(
            db_session,
            organization_id=org_id,
            user_id=user_id,
            agent_id=agent_id,
            conversation_id=conv_id,
            input_message="go",
        )
