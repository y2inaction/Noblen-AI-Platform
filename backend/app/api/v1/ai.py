"""AI Core API endpoints.

All endpoints require authentication, enforce RBAC + tenant isolation, apply a
per-organization rate limit, and record usage. Provider credentials are never
accepted from or returned to the client.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.dependencies import enforce_ai_rate_limit
from app.ai.errors import (
    AIAuthenticationError,
    AIError,
    AIInvalidRequestError,
    AIProviderUnavailableError,
    AIQuotaError,
    AIRateLimitError,
    AITimeoutError,
    AIUnknownProviderError,
)
from app.ai.gateway import AIGateway, get_ai_gateway
from app.ai.types import EmbeddingRequest, GenerationRequest, Message, StreamEventType
from app.api.deps import TenantContext, require_permission
from app.core.exceptions import (
    AppError,
    RateLimitError,
    ValidationError,
)
from app.core.logging import get_logger
from app.db.session import get_db
from app.rbac.permissions import Permission
from app.schemas.ai import (
    ALLOWED_CHAT_PROVIDERS,
    ALLOWED_EMBEDDING_PROVIDERS,
    EmbedRequestIn,
    EmbedResponseOut,
    GenerateRequestIn,
    GenerateResponseOut,
    UsageListOut,
    UsageRecordOut,
)
from app.services import ai_usage_service

logger = get_logger("api.ai")
router = APIRouter(prefix="/ai", tags=["ai"])


def _translate_ai_error(err: AIError) -> AppError:
    """Map a normalized AI error to a client-safe HTTP error (no secrets leaked)."""
    if isinstance(err, AIAuthenticationError):
        return AppError(
            "The AI provider is not configured correctly. Please contact support.",
            status_code=502,
            error_code="ai_provider_unconfigured",
        )
    if isinstance(err, AIRateLimitError):
        return RateLimitError("The AI provider is rate limiting requests. Please retry shortly.")
    if isinstance(err, AITimeoutError):
        return AppError("The AI request timed out.", status_code=504, error_code="ai_timeout")
    if isinstance(err, (AIProviderUnavailableError, AIQuotaError)):
        return AppError(
            "The AI provider is temporarily unavailable. Please retry shortly.",
            status_code=503,
            error_code="ai_provider_unavailable",
        )
    if isinstance(err, AIUnknownProviderError):
        return ValidationError(str(err.message))
    if isinstance(err, AIInvalidRequestError):
        return ValidationError("The AI request was invalid.")
    return AppError("An unexpected AI error occurred.", status_code=502, error_code="ai_error")


def _validate_provider(provider: str | None, allowed: set[str]) -> None:
    if provider is not None and provider.lower() not in allowed:
        raise ValidationError(
            f"Provider '{provider}' is not permitted. Allowed: {', '.join(sorted(allowed))}."
        )


@router.post("/generate", response_model=GenerateResponseOut)
async def generate(
    body: GenerateRequestIn,
    ctx: TenantContext = Depends(require_permission(Permission.AI_GENERATE)),
    _rate: None = Depends(enforce_ai_rate_limit),
    gateway: AIGateway = Depends(get_ai_gateway),
    db: AsyncSession = Depends(get_db),
) -> GenerateResponseOut:
    _validate_provider(body.provider, ALLOWED_CHAT_PROVIDERS)
    request = GenerationRequest(
        messages=[Message(role=m.role, content=m.content) for m in body.messages],
        system=body.system,
        provider=body.provider,
        model=body.model,
        temperature=body.temperature,
        max_output_tokens=body.max_output_tokens,
        organization_id=ctx.organization_id,
        user_id=ctx.user.id,
    )
    try:
        response = await gateway.generate(request)
    except AIError as err:
        await ai_usage_service.record_failure(
            db,
            organization_id=ctx.organization_id,
            user_id=ctx.user.id,
            provider=(body.provider or gateway.default_provider),
            model=(body.model or gateway.default_model),
            operation="generate",
            error_type=err.error_code,
        )
        await db.commit()
        raise _translate_ai_error(err) from err

    await ai_usage_service.record_generation(
        db, organization_id=ctx.organization_id, user_id=ctx.user.id, response=response
    )
    await db.commit()

    return GenerateResponseOut(
        content=response.content,
        provider=response.provider,
        model=response.model,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        total_tokens=response.total_tokens,
        estimated_cost=float(response.estimated_cost)
        if response.estimated_cost is not None
        else None,
        estimated_cost_currency=response.estimated_cost_currency,
        request_id=response.request_id,
        finish_reason=response.finish_reason,
        latency_ms=response.latency_ms,
    )


@router.post("/stream")
async def stream(
    body: GenerateRequestIn,
    ctx: TenantContext = Depends(require_permission(Permission.AI_STREAM)),
    _rate: None = Depends(enforce_ai_rate_limit),
    gateway: AIGateway = Depends(get_ai_gateway),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    _validate_provider(body.provider, ALLOWED_CHAT_PROVIDERS)
    request = GenerationRequest(
        messages=[Message(role=m.role, content=m.content) for m in body.messages],
        system=body.system,
        provider=body.provider,
        model=body.model,
        temperature=body.temperature,
        max_output_tokens=body.max_output_tokens,
        stream=True,
        organization_id=ctx.organization_id,
        user_id=ctx.user.id,
    )
    provider_name = body.provider or gateway.default_provider
    model_name = body.model or gateway.default_model

    async def event_stream() -> AsyncIterator[bytes]:
        errored = False
        recorded = False
        try:
            async for chunk in gateway.stream(request):
                payload: dict[str, Any]
                if chunk.type == StreamEventType.DELTA:
                    payload = {"type": "delta", "delta": chunk.delta}
                elif chunk.type == StreamEventType.DONE:
                    payload = {
                        "type": "done",
                        "finish_reason": chunk.finish_reason,
                        "input_tokens": chunk.input_tokens,
                        "output_tokens": chunk.output_tokens,
                        "total_tokens": chunk.total_tokens,
                    }
                    # Persist usage from the terminal chunk.
                    from app.ai.pricing import pricing_registry
                    from app.ai.types import GenerationResponse

                    cost, currency = pricing_registry.estimate_cost(
                        provider_name, model_name, chunk.input_tokens, chunk.output_tokens
                    )
                    await ai_usage_service.record_generation(
                        db,
                        organization_id=ctx.organization_id,
                        user_id=ctx.user.id,
                        operation="stream",
                        response=GenerationResponse(
                            content="",
                            provider=provider_name,
                            model=model_name,
                            input_tokens=chunk.input_tokens,
                            output_tokens=chunk.output_tokens,
                            total_tokens=chunk.total_tokens,
                            estimated_cost=cost,
                            estimated_cost_currency=currency,
                            request_id="",
                            finish_reason=chunk.finish_reason,
                        ),
                    )
                    await db.commit()
                    recorded = True
                else:  # ERROR
                    errored = True
                    payload = {
                        "type": "error",
                        "code": chunk.error_code,
                        "message": chunk.error_message,
                    }
                yield f"data: {json.dumps(payload)}\n\n".encode()
        except Exception as exc:  # noqa: BLE001 - never leak a stack trace to the stream
            logger.exception("ai_stream_endpoint_error", error_type=type(exc).__name__)
            yield (
                b'data: {"type": "error", "code": "ai_error", '
                b'"message": "An unexpected AI error occurred."}\n\n'
            )
        finally:
            if errored and not recorded:
                await ai_usage_service.record_failure(
                    db,
                    organization_id=ctx.organization_id,
                    user_id=ctx.user.id,
                    provider=provider_name,
                    model=model_name,
                    operation="stream",
                    error_type="ai_error",
                )
                await db.commit()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/embed", response_model=EmbedResponseOut)
async def embed(
    body: EmbedRequestIn,
    ctx: TenantContext = Depends(require_permission(Permission.AI_EMBED)),
    _rate: None = Depends(enforce_ai_rate_limit),
    gateway: AIGateway = Depends(get_ai_gateway),
    db: AsyncSession = Depends(get_db),
) -> EmbedResponseOut:
    _validate_provider(body.provider, ALLOWED_EMBEDDING_PROVIDERS)
    request = EmbeddingRequest(
        texts=body.texts,
        provider=body.provider,
        model=body.model,
        organization_id=ctx.organization_id,
        user_id=ctx.user.id,
    )
    try:
        response = await gateway.embed(request)
    except AIError as err:
        await ai_usage_service.record_failure(
            db,
            organization_id=ctx.organization_id,
            user_id=ctx.user.id,
            provider=(body.provider or gateway.default_embedding_provider),
            model=(body.model or gateway.default_embedding_model),
            operation="embed",
            error_type=err.error_code,
        )
        await db.commit()
        raise _translate_ai_error(err) from err

    await ai_usage_service.record_embedding(
        db, organization_id=ctx.organization_id, user_id=ctx.user.id, response=response
    )
    await db.commit()

    return EmbedResponseOut(
        provider=response.provider,
        model=response.model,
        vectors=response.vectors,
        dimensions=response.dimensions,
        input_tokens=response.input_tokens,
        estimated_cost=float(response.estimated_cost)
        if response.estimated_cost is not None
        else None,
        estimated_cost_currency=response.estimated_cost_currency,
        request_id=response.request_id,
        latency_ms=response.latency_ms,
    )


@router.get("/usage", response_model=UsageListOut)
async def list_usage(
    ctx: TenantContext = Depends(require_permission(Permission.AI_VIEW_USAGE)),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> UsageListOut:
    records, total = await ai_usage_service.list_usage(
        db, organization_id=ctx.organization_id, limit=limit, offset=offset
    )
    return UsageListOut(items=[UsageRecordOut.model_validate(r) for r in records], total=total)
