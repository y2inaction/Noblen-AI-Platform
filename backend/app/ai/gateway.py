"""The AI Gateway — the single entry point the application uses for AI.

Responsibilities: provider/model selection, request/response normalization,
timeouts, bounded retries with backoff, cost estimation, structured logging with
request IDs, and normalized errors. It is deliberately DB-free — usage persistence
is handled by the API/service layer (`app.services.ai_usage_service`) so the
gateway stays pure and easily unit-tested.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from functools import lru_cache
from typing import TypeVar

from app.ai.base import AIProvider
from app.ai.errors import AIError, AITimeoutError, AIUnknownError, AIUnknownProviderError
from app.ai.pricing import pricing_registry
from app.ai.providers.anthropic_provider import AnthropicProvider
from app.ai.providers.mock import MockProvider
from app.ai.providers.openai_provider import OpenAIProvider
from app.ai.types import (
    EmbeddingRequest,
    EmbeddingResponse,
    GenerationRequest,
    GenerationResponse,
    StreamChunk,
    StreamEventType,
)
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("ai.gateway")

T = TypeVar("T")


class AIGateway:
    def __init__(
        self,
        *,
        providers: dict[str, AIProvider] | None = None,
        default_provider: str | None = None,
        default_model: str | None = None,
        default_embedding_provider: str | None = None,
        default_embedding_model: str | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        retry_base_delay: float | None = None,
    ) -> None:
        self._providers: dict[str, AIProvider] = providers or self._build_default_providers()
        self.default_provider = default_provider or settings.AI_DEFAULT_PROVIDER
        self.default_model = default_model or settings.AI_DEFAULT_MODEL
        self.default_embedding_provider = (
            default_embedding_provider or settings.AI_DEFAULT_EMBEDDING_PROVIDER
        )
        self.default_embedding_model = (
            default_embedding_model or settings.AI_DEFAULT_EMBEDDING_MODEL
        )
        self.timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else settings.AI_REQUEST_TIMEOUT_SECONDS
        )
        self.max_retries = max_retries if max_retries is not None else settings.AI_MAX_RETRIES
        self.retry_base_delay = (
            retry_base_delay
            if retry_base_delay is not None
            else settings.AI_RETRY_BASE_DELAY_SECONDS
        )

    @staticmethod
    def _build_default_providers() -> dict[str, AIProvider]:
        return {
            "anthropic": AnthropicProvider(api_key=settings.ANTHROPIC_API_KEY),
            "openai": OpenAIProvider(api_key=settings.OPENAI_API_KEY),
            "mock": MockProvider(),
        }

    @property
    def available_providers(self) -> list[str]:
        return sorted(self._providers.keys())

    def _resolve_provider(self, name: str | None) -> AIProvider:
        provider_name = (name or self.default_provider).lower()
        provider = self._providers.get(provider_name)
        if provider is None:
            raise AIUnknownProviderError(
                f"Unknown or unconfigured AI provider '{provider_name}'. "
                f"Available: {', '.join(self.available_providers)}."
            )
        return provider

    async def _with_resilience(
        self, factory: Callable[[], Awaitable[T]], *, provider: str, model: str, op: str
    ) -> T:
        """Run an async provider call with a timeout and bounded retries."""
        attempt = 0
        while True:
            error: AIError
            try:
                return await asyncio.wait_for(factory(), timeout=self.timeout_seconds)
            except TimeoutError:
                error = AITimeoutError(
                    f"AI {op} timed out after {self.timeout_seconds}s.",
                    provider=provider,
                    model=model,
                )
            except AIError as exc:
                error = exc
            except Exception as exc:  # noqa: BLE001 - normalize anything unexpected
                error = AIUnknownError(str(exc), provider=provider, model=model)

            if error.retryable and attempt < self.max_retries:
                delay = self.retry_base_delay * (2**attempt)
                logger.warning(
                    "ai_retry",
                    provider=provider,
                    model=model,
                    op=op,
                    attempt=attempt + 1,
                    error_code=error.error_code,
                    delay_seconds=round(delay, 3),
                )
                await asyncio.sleep(delay)
                attempt += 1
                continue
            raise error

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        provider = self._resolve_provider(request.provider)
        model = request.model or self.default_model or provider.default_model
        request_id = uuid.uuid4().hex
        started = time.perf_counter()

        try:
            response = await self._with_resilience(
                lambda: provider.generate(request, model),
                provider=provider.name,
                model=model,
                op="generate",
            )
        except AIError as err:
            logger.info(
                "ai_generate_failed",
                request_id=request_id,
                provider=provider.name,
                model=model,
                error_code=err.error_code,
                organization_id=str(request.organization_id) if request.organization_id else None,
            )
            raise

        latency_ms = int((time.perf_counter() - started) * 1000)
        cost, currency = pricing_registry.estimate_cost(
            provider.name, model, response.input_tokens, response.output_tokens
        )
        response.request_id = request_id
        response.latency_ms = latency_ms
        response.estimated_cost = cost
        response.estimated_cost_currency = currency

        logger.info(
            "ai_generate",
            request_id=request_id,
            provider=provider.name,
            model=model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            total_tokens=response.total_tokens,
            latency_ms=latency_ms,
            status="success",
            organization_id=str(request.organization_id) if request.organization_id else None,
            user_id=str(request.user_id) if request.user_id else None,
        )
        return response

    async def stream(self, request: GenerationRequest) -> AsyncIterator[StreamChunk]:
        provider = self._resolve_provider(request.provider)
        model = request.model or self.default_model or provider.default_model
        request_id = uuid.uuid4().hex
        started = time.perf_counter()

        # Establish the stream with a timeout guard; mid-stream errors are surfaced
        # as a normalized ERROR chunk rather than retried.
        try:
            iterator = provider.stream(request, model)
            async for chunk in iterator:
                yield chunk
        except AIError as err:
            logger.info(
                "ai_stream_failed",
                request_id=request_id,
                provider=provider.name,
                model=model,
                error_code=err.error_code,
            )
            yield StreamChunk(
                type=StreamEventType.ERROR,
                error_code=err.error_code,
                error_message=err.message,
            )
            return
        except Exception as exc:  # noqa: BLE001
            unknown = AIUnknownError(str(exc), provider=provider.name, model=model)
            yield StreamChunk(
                type=StreamEventType.ERROR,
                error_code=unknown.error_code,
                error_message=unknown.message,
            )
            return

        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "ai_stream",
            request_id=request_id,
            provider=provider.name,
            model=model,
            latency_ms=latency_ms,
            status="success",
            organization_id=str(request.organization_id) if request.organization_id else None,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        provider_name = request.provider or self.default_embedding_provider
        provider = self._resolve_provider(provider_name)
        model = request.model or self.default_embedding_model or provider.default_embedding_model
        request_id = uuid.uuid4().hex
        started = time.perf_counter()

        response = await self._with_resilience(
            lambda: provider.embed(request, model),
            provider=provider.name,
            model=model,
            op="embed",
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        cost, currency = pricing_registry.estimate_cost(
            provider.name, model, response.input_tokens, 0
        )
        response.request_id = request_id
        response.latency_ms = latency_ms
        response.estimated_cost = cost
        response.estimated_cost_currency = currency
        logger.info(
            "ai_embed",
            request_id=request_id,
            provider=provider.name,
            model=model,
            input_tokens=response.input_tokens,
            latency_ms=latency_ms,
            status="success",
            organization_id=str(request.organization_id) if request.organization_id else None,
        )
        return response


@lru_cache
def get_ai_gateway() -> AIGateway:
    """Return the process-wide gateway (FastAPI dependency; overridable in tests)."""
    return AIGateway()
