"""Anthropic (Claude) provider adapter.

The `anthropic` SDK is imported lazily so importing this module never requires the
package or a network connection. Tests inject a fake client and never hit the API.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.ai.base import AIProvider
from app.ai.errors import (
    AIAuthenticationError,
    AIInvalidRequestError,
    AIProviderUnavailableError,
    AIRateLimitError,
    AITimeoutError,
    AIUnknownError,
)
from app.ai.types import (
    GenerationRequest,
    GenerationResponse,
    StreamChunk,
    StreamEventType,
    ToolCall,
)

_DEFAULT_MAX_TOKENS = 1024


class AnthropicProvider(AIProvider):
    name = "anthropic"
    default_model = "claude-sonnet-4-5"

    def __init__(self, api_key: str | None = None, *, client: Any | None = None) -> None:
        self._api_key = api_key
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self._api_key:
            raise AIAuthenticationError("ANTHROPIC_API_KEY is not configured.", provider=self.name)
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover - dependency always present in prod
            raise AIProviderUnavailableError(
                "anthropic SDK is not installed.", provider=self.name
            ) from exc
        self._client = AsyncAnthropic(api_key=self._api_key)
        return self._client

    @staticmethod
    def _split_messages(request: GenerationRequest) -> tuple[str | None, list[dict[str, Any]]]:
        system_parts: list[str] = []
        if request.system:
            system_parts.append(request.system)
        messages: list[dict[str, Any]] = []
        for msg in request.messages:
            if msg.role == "system":
                system_parts.append(msg.content)
            elif msg.role == "assistant" and msg.tool_calls:
                blocks: list[dict[str, Any]] = []
                if msg.content:
                    blocks.append({"type": "text", "text": msg.content})
                blocks.extend(
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                    for tc in msg.tool_calls
                )
                messages.append({"role": "assistant", "content": blocks})
            elif msg.role == "tool":
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.tool_call_id or "",
                                "content": msg.content,
                            }
                        ],
                    }
                )
            else:
                role = "assistant" if msg.role == "assistant" else "user"
                messages.append({"role": role, "content": msg.content})
        system = "\n\n".join(system_parts) if system_parts else None
        return system, messages

    @staticmethod
    def _to_tools(request: GenerationRequest) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema or {"type": "object", "properties": {}},
            }
            for t in request.tools
        ]

    def _map_error(self, exc: Exception) -> Exception:
        try:
            import anthropic
        except ImportError:
            return AIUnknownError(str(exc), provider=self.name)

        if isinstance(exc, getattr(anthropic, "AuthenticationError", ())):
            return AIAuthenticationError(str(exc), provider=self.name, status_code=401)
        if isinstance(exc, getattr(anthropic, "RateLimitError", ())):
            return AIRateLimitError(str(exc), provider=self.name, status_code=429)
        if isinstance(exc, getattr(anthropic, "APITimeoutError", ())):
            return AITimeoutError(str(exc), provider=self.name)
        if isinstance(exc, getattr(anthropic, "BadRequestError", ())):
            return AIInvalidRequestError(str(exc), provider=self.name, status_code=400)
        if isinstance(
            exc,
            (
                getattr(anthropic, "APIConnectionError", ()),
                getattr(anthropic, "InternalServerError", ()),
            ),
        ):
            return AIProviderUnavailableError(str(exc), provider=self.name)
        status = getattr(exc, "status_code", None)
        if isinstance(status, int) and status >= 500:
            return AIProviderUnavailableError(str(exc), provider=self.name, status_code=status)
        return AIUnknownError(str(exc), provider=self.name)

    async def generate(self, request: GenerationRequest, model: str) -> GenerationResponse:
        client = self._get_client()
        system, messages = self._split_messages(request)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_output_tokens or _DEFAULT_MAX_TOKENS,
            "messages": messages,
            "temperature": request.temperature,
        }
        if system:
            kwargs["system"] = system
        if request.tools:
            kwargs["tools"] = self._to_tools(request)
        try:
            resp = await client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - mapped to normalized errors
            raise self._map_error(exc) from exc

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in getattr(resp, "content", []) or []:
            btype = getattr(block, "type", None)
            if btype == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=getattr(block, "id", ""),
                        name=getattr(block, "name", ""),
                        arguments=getattr(block, "input", {}) or {},
                    )
                )
            else:
                text_parts.append(getattr(block, "text", "") or "")
        usage = getattr(resp, "usage", None)
        return GenerationResponse(
            content="".join(text_parts),
            provider=self.name,
            model=model,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            total_tokens=(getattr(usage, "input_tokens", 0) or 0)
            + (getattr(usage, "output_tokens", 0) or 0),
            request_id="",
            finish_reason=getattr(resp, "stop_reason", None),
            tool_calls=tool_calls,
        )

    async def stream(  # type: ignore[override]
        self, request: GenerationRequest, model: str
    ) -> AsyncIterator[StreamChunk]:
        client = self._get_client()
        system, messages = self._split_messages(request)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_output_tokens or _DEFAULT_MAX_TOKENS,
            "messages": messages,
            "temperature": request.temperature,
        }
        if system:
            kwargs["system"] = system
        try:
            async with client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    yield StreamChunk(type=StreamEventType.DELTA, delta=text)
                final = await stream.get_final_message()
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc) from exc

        usage = getattr(final, "usage", None)
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        yield StreamChunk(
            type=StreamEventType.DONE,
            finish_reason=getattr(final, "stop_reason", None),
            input_tokens=in_tok,
            output_tokens=out_tok,
            total_tokens=in_tok + out_tok,
        )
