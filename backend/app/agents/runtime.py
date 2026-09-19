"""Agent Runtime — the controlled, provider-agnostic agentic execution loop.

All model calls go through the Phase 2 AI Gateway; the runtime never touches a
vendor SDK. It resolves the runnable version, builds scoped context/memory/tools,
runs a bounded generate→tool loop, enforces tool permissions server-side (creating
human approvals where required), persists operational messages, and records usage.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import approvals as approval_service
from app.agents import conversations as conversation_service
from app.agents import registry as agent_registry
from app.agents.errors import AgentInactive, RuntimeLimitExceeded
from app.agents.memory import load_messages
from app.agents.tools.base import ToolContext
from app.agents.tools.registry import ToolRegistry, tool_registry, validate_arguments
from app.ai.gateway import AIGateway, get_ai_gateway
from app.ai.types import GenerationRequest, Message, ToolCall, ToolSpec
from app.core.config import settings
from app.core.logging import bind_context, get_logger
from app.models.agent import Agent, AgentVersion
from app.models.enums import AgentStatus, ToolPermissionMode
from app.models.organization import Organization
from app.models.tool import AgentTool, Tool
from app.services import ai_usage_service

logger = get_logger("agents.runtime")


@dataclass
class _ToolBinding:
    handler: Any
    permission_mode: str
    input_schema: dict[str, Any]
    description: str


@dataclass
class UsageAccumulator:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: Decimal = Decimal("0")
    currency: str = "USD"
    requests: int = 0

    def add(self, response: Any) -> None:
        self.input_tokens += response.input_tokens
        self.output_tokens += response.output_tokens
        self.total_tokens += response.total_tokens
        if response.estimated_cost is not None:
            self.estimated_cost += response.estimated_cost
            self.currency = response.estimated_cost_currency
        self.requests += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost": float(self.estimated_cost),
            "estimated_cost_currency": self.currency,
            "requests": self.requests,
        }


@dataclass
class RuntimeResult:
    status: str  # completed | awaiting_approval
    conversation_id: uuid.UUID
    agent_id: uuid.UUID
    agent_version_id: uuid.UUID
    message: dict[str, Any] | None = None
    approval_id: uuid.UUID | None = None
    tool_name: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


class AgentRuntime:
    def __init__(self, gateway: AIGateway | None = None, tools: ToolRegistry | None = None) -> None:
        self._gateway = gateway or get_ai_gateway()
        self._tools = tools or tool_registry

    # ---- context builders ------------------------------------------------- #
    async def _org_settings(self, db: AsyncSession, organization_id: uuid.UUID) -> dict[str, Any]:
        org = (
            await db.execute(select(Organization).where(Organization.id == organization_id))
        ).scalar_one_or_none()
        if org is None:
            return {}
        return {
            "name": org.name,
            "currency": org.currency,
            "timezone": org.timezone,
            "locale": org.locale,
        }

    async def _load_tools(
        self, db: AsyncSession, organization_id: uuid.UUID, agent_id: uuid.UUID
    ) -> tuple[dict[str, _ToolBinding], list[ToolSpec]]:
        rows = (
            await db.execute(
                select(AgentTool, Tool)
                .join(Tool, Tool.id == AgentTool.tool_id)
                .where(AgentTool.agent_id == agent_id, AgentTool.enabled.is_(True))
            )
        ).all()
        bindings: dict[str, _ToolBinding] = {}
        specs: list[ToolSpec] = []
        for agent_tool, tool in rows:
            if not tool.enabled:
                continue
            handler = self._tools.get_by_identifier(tool.handler_identifier)
            if handler is None:
                continue  # only registered handlers may ever run
            mode = agent_tool.permission_mode or tool.permission_mode
            bindings[tool.name] = _ToolBinding(
                handler=handler,
                permission_mode=mode,
                input_schema=tool.input_schema or {},
                description=tool.description,
            )
            specs.append(
                ToolSpec(
                    name=tool.name,
                    description=tool.description,
                    input_schema=tool.input_schema or {"type": "object", "properties": {}},
                )
            )
        return bindings, specs

    # ---- public entrypoints ---------------------------------------------- #
    async def execute(
        self,
        db: AsyncSession,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        agent_id: uuid.UUID,
        conversation_id: uuid.UUID,
        input_message: str,
        version_id: uuid.UUID | None = None,
    ) -> RuntimeResult:
        if len(input_message) > settings.AGENT_MAX_INPUT_CHARS:
            from app.core.exceptions import ValidationError

            raise ValidationError("Input message is too long.")

        agent, version = await agent_registry.resolve_runnable_version(
            db, organization_id, agent_id, version_id=version_id
        )
        # Normal (non-test) execution requires an ACTIVE agent.
        if version_id is None and agent.status != AgentStatus.ACTIVE.value:
            raise AgentInactive(f"Agent is '{agent.status}', not ACTIVE.")

        # Ensure the conversation belongs to this tenant.
        await conversation_service.get_conversation(db, organization_id, conversation_id)

        bind_context(agent_id=str(agent_id), agent_version_id=str(version.id))

        # Persist the incoming user message before building context.
        await conversation_service.add_message(
            db,
            organization_id,
            conversation_id,
            role="user",
            content=input_message,
            created_by=user_id,
        )

        context = ToolContext(
            organization_id=organization_id,
            user_id=user_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            org_settings=await self._org_settings(db, organization_id),
        )
        bindings, specs = await self._load_tools(db, organization_id, agent_id)
        messages = await load_messages(
            db,
            organization_id,
            conversation_id,
            mode=version.memory_configuration.get("mode", agent.memory_mode),
            memory_configuration=version.memory_configuration,
        )
        return await self._loop(
            db,
            context=context,
            agent=agent,
            version=version,
            bindings=bindings,
            specs=specs,
            messages=messages,
            usage=UsageAccumulator(),
        )

    async def resume_after_approval(
        self,
        db: AsyncSession,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        approval: Any,
        approved: bool,
    ) -> RuntimeResult:
        """Continue a run after a human approves/rejects a gated tool call."""
        agent, version = await agent_registry.resolve_runnable_version(
            db, organization_id, approval.agent_id
        )
        conversation_id = approval.conversation_id
        context = ToolContext(
            organization_id=organization_id,
            user_id=user_id,
            agent_id=approval.agent_id,
            conversation_id=conversation_id,
            org_settings=await self._org_settings(db, organization_id),
        )
        bindings, specs = await self._load_tools(db, organization_id, approval.agent_id)

        # Produce the tool-result message for the gated call.
        if approved:
            binding = bindings.get(approval.tool_name)
            if binding is None:
                tool_output = {"error": "tool is no longer available"}
            else:
                result = await binding.handler.execute(context, approval.request_payload)
                tool_output = result.output if result.ok else {"error": result.error}
        else:
            tool_output = {"error": "Tool call was rejected by a human reviewer."}

        await conversation_service.add_message(
            db,
            organization_id,
            conversation_id,
            role="tool",
            content=json.dumps(tool_output),
            metadata={"tool_call_id": approval.tool_call_id, "name": approval.tool_name},
            agent_version_id=version.id,
        )

        messages = await load_messages(
            db,
            organization_id,
            conversation_id,
            mode=version.memory_configuration.get("mode", agent.memory_mode),
            memory_configuration=version.memory_configuration,
        )
        return await self._loop(
            db,
            context=context,
            agent=agent,
            version=version,
            bindings=bindings,
            specs=specs,
            messages=messages,
            usage=UsageAccumulator(),
        )

    # ---- the loop --------------------------------------------------------- #
    async def _loop(
        self,
        db: AsyncSession,
        *,
        context: ToolContext,
        agent: Agent,
        version: AgentVersion,
        bindings: dict[str, _ToolBinding],
        specs: list[ToolSpec],
        messages: list[Message],
        usage: UsageAccumulator,
    ) -> RuntimeResult:
        started = time.perf_counter()
        tool_calls_made = 0

        for iteration in range(settings.AGENT_MAX_ITERATIONS + 1):
            if iteration >= settings.AGENT_MAX_ITERATIONS:
                raise RuntimeLimitExceeded("Agent exceeded the maximum iteration budget.")
            if (time.perf_counter() - started) > settings.AGENT_MAX_RUNTIME_SECONDS:
                raise RuntimeLimitExceeded("Agent exceeded the maximum runtime budget.")

            request = GenerationRequest(
                messages=messages,
                system=version.system_instructions or None,
                provider=version.provider,
                model=version.model,
                temperature=version.temperature,
                max_output_tokens=version.max_tokens,
                tools=specs,
                organization_id=context.organization_id,
                user_id=context.user_id,
                agent_id=context.agent_id,
                agent_version_id=version.id,
                conversation_id=context.conversation_id,
            )
            response = await self._gateway.generate(request)
            usage.add(response)
            await ai_usage_service.record_generation(
                db,
                organization_id=context.organization_id,
                user_id=context.user_id,
                response=response,
                operation="agent",
                agent_id=context.agent_id,
                agent_version_id=version.id,
                conversation_id=context.conversation_id,
            )

            if not response.tool_calls:
                message = await conversation_service.add_message(
                    db,
                    context.organization_id,
                    context.conversation_id,  # type: ignore[arg-type]
                    role="assistant",
                    content=response.content,
                    metadata={"provider": response.provider, "model": response.model},
                    agent_version_id=version.id,
                )
                logger.info(
                    "agent_execution_completed",
                    conversation_id=str(context.conversation_id),
                    iterations=iteration + 1,
                    tool_calls=tool_calls_made,
                    status="completed",
                )
                return RuntimeResult(
                    status="completed",
                    conversation_id=context.conversation_id,  # type: ignore[arg-type]
                    agent_id=agent.id,
                    agent_version_id=version.id,
                    message={
                        "id": str(message.id),
                        "role": "assistant",
                        "content": response.content,
                    },
                    usage=usage.as_dict(),
                )

            # The model requested tools — persist the assistant tool-call message.
            await conversation_service.add_message(
                db,
                context.organization_id,
                context.conversation_id,  # type: ignore[arg-type]
                role="assistant",
                content=response.content,
                metadata={
                    "provider": response.provider,
                    "model": response.model,
                    "tool_calls": [tc.model_dump() for tc in response.tool_calls],
                },
                agent_version_id=version.id,
            )
            messages.append(
                Message(role="assistant", content=response.content, tool_calls=response.tool_calls)
            )

            for tool_call in response.tool_calls:
                outcome = await self._handle_tool_call(
                    db, context=context, agent=agent, tool_call=tool_call, bindings=bindings
                )
                if outcome["kind"] == "approval":
                    return RuntimeResult(
                        status="awaiting_approval",
                        conversation_id=context.conversation_id,  # type: ignore[arg-type]
                        agent_id=agent.id,
                        agent_version_id=version.id,
                        approval_id=outcome["approval_id"],
                        tool_name=tool_call.name,
                        usage=usage.as_dict(),
                    )
                # executed / disabled / unknown => a tool-result message was stored
                messages.append(
                    Message(
                        role="tool",
                        content=outcome["content"],
                        tool_call_id=tool_call.id,
                        name=tool_call.name,
                    )
                )
                if outcome["kind"] == "executed":
                    tool_calls_made += 1
                    if tool_calls_made >= settings.AGENT_MAX_TOOL_CALLS:
                        raise RuntimeLimitExceeded("Agent exceeded the maximum tool-call budget.")

        raise RuntimeLimitExceeded("Agent exceeded the maximum iteration budget.")

    async def _handle_tool_call(
        self,
        db: AsyncSession,
        *,
        context: ToolContext,
        agent: Agent,
        tool_call: ToolCall,
        bindings: dict[str, _ToolBinding],
    ) -> dict[str, Any]:
        binding = bindings.get(tool_call.name)

        async def _store_tool_result(output: dict[str, Any]) -> str:
            content = json.dumps(output)
            await conversation_service.add_message(
                db,
                context.organization_id,
                context.conversation_id,  # type: ignore[arg-type]
                role="tool",
                content=content,
                metadata={"tool_call_id": tool_call.id, "name": tool_call.name},
            )
            return content

        if binding is None:
            return {
                "kind": "unknown",
                "content": await _store_tool_result(
                    {"error": f"tool '{tool_call.name}' is not available to this agent"}
                ),
            }

        if binding.permission_mode == ToolPermissionMode.DISABLED.value:
            return {
                "kind": "disabled",
                "content": await _store_tool_result(
                    {"error": f"tool '{tool_call.name}' is disabled"}
                ),
            }

        # Validate arguments server-side before anything else.
        arg_error = validate_arguments(binding.input_schema, tool_call.arguments)
        if arg_error is not None:
            return {
                "kind": "executed",
                "content": await _store_tool_result({"error": f"invalid arguments: {arg_error}"}),
            }

        if binding.permission_mode == ToolPermissionMode.APPROVAL_REQUIRED.value:
            approval = await approval_service.create_approval(
                db,
                context.organization_id,
                agent_id=agent.id,
                conversation_id=context.conversation_id,
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                request_payload=tool_call.arguments,
                requested_by=context.user_id,
                reason=f"Agent requested tool '{tool_call.name}' (approval required).",
            )
            logger.info(
                "agent_tool_approval_required",
                tool_name=tool_call.name,
                approval_id=str(approval.id),
            )
            return {"kind": "approval", "approval_id": approval.id}

        # AUTO — execute now.
        result = await binding.handler.execute(context, tool_call.arguments)
        output = result.output if result.ok else {"error": result.error}
        logger.info("agent_tool_executed", tool_name=tool_call.name, ok=result.ok)
        return {"kind": "executed", "content": await _store_tool_result(output)}


def get_agent_runtime() -> AgentRuntime:
    """FastAPI dependency (overridable in tests with a mock-backed gateway)."""
    return AgentRuntime()
