"""The provider interface every AI adapter implements.

The gateway resolves the model before calling a provider, so adapters receive a
concrete `model`. Adapters translate normalized types to/from their vendor SDK and
map vendor exceptions to `app.ai.errors` — no vendor object escapes the adapter.
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator

from app.ai.errors import AIInvalidRequestError
from app.ai.types import (
    EmbeddingRequest,
    EmbeddingResponse,
    GenerationRequest,
    GenerationResponse,
    StreamChunk,
)


class AIProvider(abc.ABC):
    """Abstract base for all providers."""

    #: Stable provider identifier, e.g. "anthropic", "openai", "mock".
    name: str = "base"
    #: Provider's default chat model when none is specified.
    default_model: str = ""
    #: Provider's default embedding model when none is specified.
    default_embedding_model: str = ""

    @abc.abstractmethod
    async def generate(self, request: GenerationRequest, model: str) -> GenerationResponse:
        """Single-shot generation."""

    @abc.abstractmethod
    def stream(self, request: GenerationRequest, model: str) -> AsyncIterator[StreamChunk]:
        """Streaming generation. Returns an async iterator of normalized chunks."""

    async def embed(self, request: EmbeddingRequest, model: str) -> EmbeddingResponse:
        """Create embeddings. Providers that don't support embeddings raise."""
        raise AIInvalidRequestError(
            f"Provider '{self.name}' does not support embeddings.", provider=self.name
        )

    def supports_embeddings(self) -> bool:
        return type(self).embed is not AIProvider.embed
