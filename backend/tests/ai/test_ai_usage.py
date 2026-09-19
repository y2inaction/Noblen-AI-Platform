"""Usage metering + cost estimation unit tests (service + pricing)."""

import uuid
from decimal import Decimal

import pytest

from app.ai.pricing import pricing_registry
from app.ai.types import GenerationResponse
from app.services import ai_usage_service


def test_pricing_known_model_estimates_cost():
    cost, currency = pricing_registry.estimate_cost(
        "openai", "gpt-4o-mini", input_tokens=1_000_000, output_tokens=1_000_000
    )
    assert currency == "USD"
    # 0.15 (input) + 0.60 (output) per 1M tokens.
    assert cost == Decimal("0.750000")


def test_pricing_unknown_model_returns_none():
    cost, _ = pricing_registry.estimate_cost("openai", "no-such-model", 100, 100)
    assert cost is None


@pytest.mark.asyncio
async def test_record_generation_persists_tokens_and_attribution(db_session):
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    response = GenerationResponse(
        content="ok",
        provider="mock",
        model="mock-1",
        input_tokens=12,
        output_tokens=8,
        total_tokens=20,
        estimated_cost=Decimal("0.000000"),
        estimated_cost_currency="USD",
        request_id="req-123",
        finish_reason="stop",
        latency_ms=42,
    )
    record = await ai_usage_service.record_generation(
        db_session, organization_id=org_id, user_id=user_id, response=response
    )
    await db_session.commit()

    assert record.organization_id == org_id
    assert record.user_id == user_id
    assert record.input_tokens == 12
    assert record.output_tokens == 8
    assert record.total_tokens == 20
    assert record.latency_ms == 42
    assert record.status == "success"
    assert record.request_id == "req-123"


@pytest.mark.asyncio
async def test_list_usage_is_org_scoped(db_session):
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()

    def _resp(model: str) -> GenerationResponse:
        return GenerationResponse(
            content="",
            provider="mock",
            model=model,
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
            request_id="r",
        )

    await ai_usage_service.record_generation(
        db_session, organization_id=org_a, user_id=None, response=_resp("m1")
    )
    await ai_usage_service.record_generation(
        db_session, organization_id=org_b, user_id=None, response=_resp("m2")
    )
    await db_session.commit()

    records_a, total_a = await ai_usage_service.list_usage(db_session, organization_id=org_a)
    assert total_a == 1
    assert all(r.organization_id == org_a for r in records_a)
