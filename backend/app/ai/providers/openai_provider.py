"""OpenAI provider adapter (chat generation, streaming, and embeddings).

The `openai` SDK is imported lazily; tests inject a fake client and never call the
real API.
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
    EmbeddingRequest,
    EmbeddingResponse,
    GenerationRequest,
    GenerationResponse,
    StreamChunk,
    StreamEventType,
)


class OpenAIProvider(AIProvider):
    name = "openai"
    default_model = "gpt-4o-mini"
    default_embedding_model = "text-embedding-3-small"

    def __init__(self, api_key: str | None = None, *, client: Any | None = None) -> None:
        self._api_key = api_key
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self._api_key:
            raise AIAuthenticationError("OPENAI_API_KEY is not configured.", provider=self.name)
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover
            raise AIProviderUnavailableError(
                "openai SDK is not installed.", provider=self.name
            ) from exc
        self._client = AsyncOpenAI(api_key=self._api_key)
        return self._client

    @staticmethod
    def _to_messages(request: GenerationRequest) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        for msg in request.messages:
            role = msg.role if msg.role in ("system", "user", "assistant") else "user"
            messages.append({"role": role, "content": msg.content})
        return messages

    def _map_error(self, exc: Exception) -> Exception:
        try:
            import openai
        except ImportError:
            return AIUnknownError(str(exc), provider=self.name)

        if isinstance(exc, getattr(openai, "AuthenticationError", ())):
            return AIAuthenticationError(str(exc), provider=self.name, status_code=401)
        if isinstance(exc, getattr(openai, "RateLimitError", ())):
            return AIRateLimitError(str(exc), provider=self.name, status_code=429)
        if isinstance(exc, getattr(openai, "APITimeoutError", ())):
            return AITimeoutError(str(exc), provider=self.name)
        if isinstance(exc, getattr(openai, "BadRequestError", ())):
            return AIInvalidRequestError(str(exc), provider=self.name, status_code=400)
        if isinstance(
            exc,
            (
                getattr(openai, "APIConnectionError", ()),
                getattr(openai, "InternalServerError", ()),
            ),
        ):
            return AIProviderUnavailableError(str(exc), provider=self.name)
        status = getattr(exc, "status_code", None)
        if isinstance(status, int) and status >= 500:
            return AIProviderUnavailableError(str(exc), provider=self.name, status_code=status)
        return AIUnknownError(str(exc), provider=self.name)

    async def generate(self, request: GenerationRequest, model: str) -> GenerationResponse:
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": self._to_messages(request),
            "temperature": request.temperature,
        }
        if request.max_output_tokens:
            kwargs["max_tokens"] = request.max_output_tokens
        try:
            resp = await client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc) from exc

        choice = resp.choices[0]
        usage = getattr(resp, "usage", None)
        return GenerationResponse(
            content=getattr(choice.message, "content", "") or "",
            provider=self.name,
            model=model,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            total_tokens=getattr(usage, "total_tokens", 0) or 0,
            request_id="",
            finish_reason=getattr(choice, "finish_reason", None),
        )

    async def stream(  # type: ignore[override]
        self, request: GenerationRequest, model: str
    ) -> AsyncIterator[StreamChunk]:
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": self._to_messages(request),
            "temperature": request.temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if request.max_output_tokens:
            kwargs["max_tokens"] = request.max_output_tokens
        try:
            stream = await client.chat.completions.create(**kwargs)
            finish_reason: str | None = None
            in_tok = out_tok = total_tok = 0
            async for chunk in stream:
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    in_tok = getattr(usage, "prompt_tokens", 0) or in_tok
                    out_tok = getattr(usage, "completion_tokens", 0) or out_tok
                    total_tok = getattr(usage, "total_tokens", 0) or total_tok
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = getattr(choice.delta, "content", None)
                if getattr(choice, "finish_reason", None):
                    finish_reason = choice.finish_reason
                if delta:
                    yield StreamChunk(type=StreamEventType.DELTA, delta=delta)
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc) from exc

        yield StreamChunk(
            type=StreamEventType.DONE,
            finish_reason=finish_reason,
            input_tokens=in_tok,
            output_tokens=out_tok,
            total_tokens=total_tok or (in_tok + out_tok),
        )

    async def embed(self, request: EmbeddingRequest, model: str) -> EmbeddingResponse:
        client = self._get_client()
        try:
            resp = await client.embeddings.create(model=model, input=request.texts)
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc) from exc

        vectors = [list(item.embedding) for item in resp.data]
        usage = getattr(resp, "usage", None)
        return EmbeddingResponse(
            provider=self.name,
            model=model,
            vectors=vectors,
            dimensions=len(vectors[0]) if vectors else 0,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            request_id="",
        )
