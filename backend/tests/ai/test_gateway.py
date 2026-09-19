"""AI Gateway tests: selection, normalization, cost, retries, timeouts, streaming."""

import asyncio
from collections.abc import AsyncIterator

import pytest

from app.ai.base import AIProvider
from app.ai.errors import AIProviderUnavailableError, AITimeoutError, AIUnknownProviderError
from app.ai.gateway import AIGateway
from app.ai.providers.mock import MockProvider
from app.ai.types import GenerationRequest, Message, StreamChunk, StreamEventType


def _gateway(**providers) -> AIGateway:
    provs = providers or {"mock": MockProvider()}
    return AIGateway(
        providers=provs,
        default_provider=next(iter(provs)),
        default_model="mock-1",
        max_retries=2,
        retry_base_delay=0.0,
        timeout_seconds=5.0,
    )


def _req(**kw) -> GenerationRequest:
    return GenerationRequest(messages=[Message(role="user", content="hello there")], **kw)


@pytest.mark.asyncio
async def test_default_provider_and_model_selection():
    gw = _gateway()
    resp = await gw.generate(_req())
    assert resp.provider == "mock"
    assert resp.model == "mock-1"
    assert resp.request_id  # gateway assigns one
    assert resp.latency_ms >= 0


@pytest.mark.asyncio
async def test_explicit_model_override():
    gw = _gateway()
    resp = await gw.generate(_req(model="mock-xyz"))
    assert resp.model == "mock-xyz"


@pytest.mark.asyncio
async def test_unknown_provider_raises():
    gw = _gateway()
    with pytest.raises(AIUnknownProviderError):
        await gw.generate(_req(provider="does-not-exist"))


@pytest.mark.asyncio
async def test_cost_estimation_present_for_known_model():
    gw = _gateway()
    resp = await gw.generate(_req(model="mock-1"))
    # mock-1 is priced at 0 in the registry -> cost is 0, not None.
    assert resp.estimated_cost is not None
    assert resp.estimated_cost_currency == "USD"


@pytest.mark.asyncio
async def test_cost_none_for_unknown_model():
    gw = _gateway()
    resp = await gw.generate(_req(model="totally-unpriced-model"))
    assert resp.estimated_cost is None


@pytest.mark.asyncio
async def test_streaming_yields_deltas_then_done():
    gw = _gateway()
    chunks = [c async for c in gw.stream(_req())]
    assert chunks[-1].type == StreamEventType.DONE
    text = "".join(c.delta for c in chunks if c.type == StreamEventType.DELTA)
    assert "hello there" in text


@pytest.mark.asyncio
async def test_timeout_maps_to_timeout_error():
    class SlowProvider(AIProvider):
        name = "slow"
        default_model = "slow-1"

        async def generate(self, request, model):
            await asyncio.sleep(1.0)
            raise AssertionError("should have timed out")

        def stream(self, request, model):  # pragma: no cover
            raise NotImplementedError

    gw = AIGateway(
        providers={"slow": SlowProvider()},
        default_provider="slow",
        default_model="slow-1",
        max_retries=0,
        timeout_seconds=0.05,
    )
    with pytest.raises(AITimeoutError):
        await gw.generate(_req())


@pytest.mark.asyncio
async def test_retries_then_succeeds():
    class FlakyProvider(AIProvider):
        name = "flaky"
        default_model = "flaky-1"

        def __init__(self):
            self.calls = 0

        async def generate(self, request, model):
            self.calls += 1
            if self.calls < 3:
                raise AIProviderUnavailableError("try again", provider="flaky")
            return await MockProvider().generate(request, model)

        def stream(self, request, model):  # pragma: no cover
            raise NotImplementedError

    flaky = FlakyProvider()
    gw = AIGateway(
        providers={"flaky": flaky},
        default_provider="flaky",
        default_model="flaky-1",
        max_retries=2,
        retry_base_delay=0.0,
    )
    resp = await gw.generate(_req())
    assert flaky.calls == 3
    assert resp.provider == "mock"  # returned by the underlying success


@pytest.mark.asyncio
async def test_non_retryable_error_not_retried():
    from app.ai.errors import AIInvalidRequestError

    class BadProvider(AIProvider):
        name = "bad"
        default_model = "bad-1"

        def __init__(self):
            self.calls = 0

        async def generate(self, request, model):
            self.calls += 1
            raise AIInvalidRequestError("nope", provider="bad")

        def stream(self, request, model):  # pragma: no cover
            raise NotImplementedError

    bad = BadProvider()
    gw = AIGateway(
        providers={"bad": bad}, default_provider="bad", default_model="bad-1", max_retries=3
    )
    with pytest.raises(AIInvalidRequestError):
        await gw.generate(_req())
    assert bad.calls == 1  # never retried


@pytest.mark.asyncio
async def test_stream_error_emits_error_chunk():
    class StreamErrProvider(AIProvider):
        name = "serr"
        default_model = "serr-1"

        async def generate(self, request, model):  # pragma: no cover
            raise NotImplementedError

        async def stream(self, request, model) -> AsyncIterator[StreamChunk]:
            raise AIProviderUnavailableError("stream boom", provider="serr")
            yield  # pragma: no cover

    gw = AIGateway(
        providers={"serr": StreamErrProvider()},
        default_provider="serr",
        default_model="serr-1",
        max_retries=0,
    )
    chunks = [c async for c in gw.stream(_req())]
    assert chunks[-1].type == StreamEventType.ERROR
    assert chunks[-1].error_code == "ai_provider_unavailable_error"
