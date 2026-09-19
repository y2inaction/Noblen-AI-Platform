"""A deterministic, offline mock provider.

Used as the default provider in automated tests (no network, no API key) and as a
safe local fallback. Token counts are naive word counts — enough to exercise the
gateway, usage metering, and cost estimation paths.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.ai.base import AIProvider
from app.ai.errors import AIInvalidRequestError
from app.ai.types import (
    EmbeddingRequest,
    EmbeddingResponse,
    GenerationRequest,
    GenerationResponse,
    StreamChunk,
    StreamEventType,
)


def _count_tokens(text: str) -> int:
    return max(1, len(text.split()))


class MockProvider(AIProvider):
    name = "mock"
    default_model = "mock-1"
    default_embedding_model = "mock-embed-1"

    def __init__(self, *, fail_with: Exception | None = None, dimensions: int = 8) -> None:
        self._fail_with = fail_with
        self._dimensions = dimensions

    def _maybe_fail(self) -> None:
        if self._fail_with is not None:
            raise self._fail_with

    def _reply(self, request: GenerationRequest) -> str:
        last_user = next(
            (m.content for m in reversed(request.messages) if m.role == "user"),
            "",
        )
        return f"[mock:{request.model or self.default_model}] {last_user}".strip()

    async def generate(self, request: GenerationRequest, model: str) -> GenerationResponse:
        self._maybe_fail()
        content = self._reply(request)
        input_text = (request.system or "") + " ".join(m.content for m in request.messages)
        return GenerationResponse(
            content=content,
            provider=self.name,
            model=model,
            input_tokens=_count_tokens(input_text),
            output_tokens=_count_tokens(content),
            total_tokens=_count_tokens(input_text) + _count_tokens(content),
            request_id="",  # filled by the gateway
            finish_reason="stop",
            metadata={"mock": True},
        )

    async def stream(  # type: ignore[override]
        self, request: GenerationRequest, model: str
    ) -> AsyncIterator[StreamChunk]:
        self._maybe_fail()
        content = self._reply(request)
        input_text = (request.system or "") + " ".join(m.content for m in request.messages)
        words = content.split()
        for i, word in enumerate(words):
            yield StreamChunk(
                type=StreamEventType.DELTA,
                delta=word + (" " if i < len(words) - 1 else ""),
            )
        yield StreamChunk(
            type=StreamEventType.DONE,
            finish_reason="stop",
            input_tokens=_count_tokens(input_text),
            output_tokens=len(words),
            total_tokens=_count_tokens(input_text) + len(words),
        )

    async def embed(self, request: EmbeddingRequest, model: str) -> EmbeddingResponse:
        self._maybe_fail()
        if not request.texts:
            raise AIInvalidRequestError("No texts provided.", provider=self.name)
        vectors: list[list[float]] = []
        total_tokens = 0
        for text in request.texts:
            total_tokens += _count_tokens(text)
            # Deterministic pseudo-embedding from the text hash.
            seed = sum(ord(c) for c in text) or 1
            vectors.append([((seed * (i + 1)) % 100) / 100.0 for i in range(self._dimensions)])
        return EmbeddingResponse(
            provider=self.name,
            model=model,
            vectors=vectors,
            dimensions=self._dimensions,
            input_tokens=total_tokens,
            request_id="",
            metadata={"mock": True},
        )
