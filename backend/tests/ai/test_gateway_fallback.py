"""Gateway fallback across providers/models (Noblen AI 3.0, M1)."""

from __future__ import annotations

import pytest

from app.ai.errors import AIAuthenticationError, AIInvalidRequestError, AIProviderUnavailableError
from app.ai.gateway import AIGateway
from app.ai.providers.mock import MockProvider
from app.ai.types import GenerationRequest, Message


def _request(**kw) -> GenerationRequest:
    return GenerationRequest(messages=[Message(role="user", content="hi")], **kw)


def _gateway(primary: MockProvider) -> AIGateway:
    primary.name = "primary"  # type: ignore[misc]
    backup = MockProvider()
    backup.name = "backup"  # type: ignore[misc]
    return AIGateway(
        providers={"primary": primary, "backup": backup},
        default_provider="primary",
        default_model="primary-1",
        max_retries=0,
    )


async def test_falls_back_in_order_and_reports_it():
    gw = _gateway(MockProvider(fail_with=AIProviderUnavailableError("down", provider="primary")))
    resp = await gw.generate(_request(fallbacks=["backup:backup-large"]))
    assert resp.provider == "backup" and resp.model == "backup-large"
    assert resp.metadata["fallback_from"] == "primary:primary-1"


async def test_misconfigured_primary_also_falls_back():
    gw = _gateway(MockProvider(fail_with=AIAuthenticationError("bad key", provider="primary")))
    resp = await gw.generate(_request(fallbacks=["backup:"]))
    assert resp.provider == "backup" and resp.model == "mock-1"  # provider default model


async def test_invalid_requests_do_not_fall_back():
    gw = _gateway(MockProvider(fail_with=AIInvalidRequestError("bad", provider="primary")))
    with pytest.raises(AIInvalidRequestError):
        await gw.generate(_request(fallbacks=["backup:backup-1"]))


async def test_unknown_fallback_providers_are_skipped_and_last_error_raised():
    gw = _gateway(MockProvider(fail_with=AIProviderUnavailableError("down", provider="primary")))
    with pytest.raises(AIProviderUnavailableError):
        await gw.generate(_request(fallbacks=["nope:model"]))


async def test_platform_fallbacks_from_settings(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_FALLBACK_MODELS", ["backup:backup-2"])
    gw = _gateway(MockProvider(fail_with=AIProviderUnavailableError("down", provider="primary")))
    resp = await gw.generate(_request())
    assert resp.model == "backup-2"


async def test_primary_success_is_unchanged():
    gw = _gateway(MockProvider())
    resp = await gw.generate(_request(fallbacks=["backup:backup-1"]))
    assert resp.provider == "primary" and resp.model == "primary-1"
    assert "fallback_from" not in resp.metadata
