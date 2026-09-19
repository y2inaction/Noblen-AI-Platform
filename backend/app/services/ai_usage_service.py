"""Persistence and querying for AI usage records (tenant-scoped)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.types import EmbeddingResponse, GenerationResponse
from app.db.tenant import tenant_scoped
from app.models.ai_usage import AIUsageRecord


async def record_generation(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID | None,
    response: GenerationResponse,
    operation: str = "generate",
    agent_id: uuid.UUID | None = None,
    agent_version_id: uuid.UUID | None = None,
    conversation_id: uuid.UUID | None = None,
    workflow_id: uuid.UUID | None = None,
) -> AIUsageRecord:
    record = AIUsageRecord(
        organization_id=organization_id,
        user_id=user_id,
        agent_id=agent_id,
        agent_version_id=agent_version_id,
        conversation_id=conversation_id,
        workflow_id=workflow_id,
        provider=response.provider,
        model=response.model,
        operation=operation,
        request_id=response.request_id,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        total_tokens=response.total_tokens,
        estimated_cost=response.estimated_cost,
        estimated_cost_currency=response.estimated_cost_currency,
        latency_ms=response.latency_ms,
        status="success",
    )
    db.add(record)
    await db.flush()
    return record


async def record_embedding(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID | None,
    response: EmbeddingResponse,
) -> AIUsageRecord:
    record = AIUsageRecord(
        organization_id=organization_id,
        user_id=user_id,
        provider=response.provider,
        model=response.model,
        operation="embed",
        request_id=response.request_id,
        input_tokens=response.input_tokens,
        output_tokens=0,
        total_tokens=response.input_tokens,
        estimated_cost=response.estimated_cost,
        estimated_cost_currency=response.estimated_cost_currency,
        latency_ms=response.latency_ms,
        status="success",
    )
    db.add(record)
    await db.flush()
    return record


async def record_failure(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID | None,
    provider: str,
    model: str,
    operation: str,
    error_type: str,
    request_id: str = "",
) -> AIUsageRecord:
    record = AIUsageRecord(
        organization_id=organization_id,
        user_id=user_id,
        provider=provider,
        model=model,
        operation=operation,
        request_id=request_id,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        estimated_cost=Decimal("0"),
        latency_ms=0,
        status="error",
        error_type=error_type,
    )
    db.add(record)
    await db.flush()
    return record


async def list_usage(
    db: AsyncSession, *, organization_id: uuid.UUID, limit: int = 50, offset: int = 0
) -> tuple[list[AIUsageRecord], int]:
    """Return (records, total) scoped to the given organization only."""
    from sqlalchemy import func

    base = tenant_scoped(select(AIUsageRecord), AIUsageRecord, organization_id)
    total = (
        await db.execute(
            tenant_scoped(select(func.count(AIUsageRecord.id)), AIUsageRecord, organization_id)
        )
    ).scalar_one()
    rows = (
        (
            await db.execute(
                base.order_by(AIUsageRecord.created_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), int(total)
