"""Provider adapter tests: normalization + failure mapping (mocked, no network)."""

import pytest

from app.ai.errors import (
    AIError,
    AIProviderUnavailableError,
    AIUnknownError,
)
from app.ai.providers.anthropic_provider import AnthropicProvider
from app.ai.providers.mock import MockProvider
from app.ai.providers.openai_provider import OpenAIProvider
from app.ai.types import EmbeddingRequest, GenerationRequest, Message, StreamEventType
from tests.ai.fakes import FakeAnthropicClient, FakeOpenAIClient


def _req() -> GenerationRequest:
    return GenerationRequest(messages=[Message(role="user", content="hi")], system="be nice")


@pytest.mark.asyncio
async def test_anthropic_generate_normalizes():
    provider = AnthropicProvider(client=FakeAnthropicClient(text="hello world"))
    resp = await provider.generate(_req(), "claude-sonnet-4-5")
    assert resp.provider == "anthropic"
    assert resp.model == "claude-sonnet-4-5"
    assert resp.content == "hello world"
    assert resp.input_tokens == 11
    assert resp.output_tokens == 7
    assert resp.total_tokens == 18
    assert resp.finish_reason == "end_turn"


@pytest.mark.asyncio
async def test_anthropic_stream_normalizes():
    provider = AnthropicProvider(client=FakeAnthropicClient(text="a b c"))
    chunks = [c async for c in provider.stream(_req(), "claude-sonnet-4-5")]
    deltas = [c for c in chunks if c.type == StreamEventType.DELTA]
    done = [c for c in chunks if c.type == StreamEventType.DONE]
    assert "".join(c.delta for c in deltas).strip() == "a b c"
    assert len(done) == 1
    assert done[0].input_tokens == 11


@pytest.mark.asyncio
async def test_openai_generate_normalizes():
    provider = OpenAIProvider(client=FakeOpenAIClient(text="hi there"))
    resp = await provider.generate(_req(), "gpt-4o-mini")
    assert resp.provider == "openai"
    assert resp.content == "hi there"
    assert resp.input_tokens == 5
    assert resp.output_tokens == 2
    assert resp.total_tokens == 7
    assert resp.finish_reason == "stop"


@pytest.mark.asyncio
async def test_openai_stream_normalizes():
    provider = OpenAIProvider(client=FakeOpenAIClient(text="one two"))
    chunks = [c async for c in provider.stream(_req(), "gpt-4o-mini")]
    deltas = "".join(c.delta for c in chunks if c.type == StreamEventType.DELTA).strip()
    done = [c for c in chunks if c.type == StreamEventType.DONE][0]
    assert deltas == "one two"
    assert done.total_tokens == 7


@pytest.mark.asyncio
async def test_openai_embeddings_normalize():
    provider = OpenAIProvider(client=FakeOpenAIClient(dims=4))
    resp = await provider.embed(EmbeddingRequest(texts=["a", "b"]), "text-embedding-3-small")
    assert resp.provider == "openai"
    assert len(resp.vectors) == 2
    assert resp.dimensions == 4
    assert resp.input_tokens == 6


@pytest.mark.asyncio
async def test_provider_failure_maps_to_normalized_error():
    # A server-side (5xx) failure maps to a retryable provider-unavailable error.
    class Boom(Exception):
        status_code = 503

    provider = OpenAIProvider(client=FakeOpenAIClient(raise_exc=Boom("upstream down")))
    with pytest.raises(AIProviderUnavailableError):
        await provider.generate(_req(), "gpt-4o-mini")


@pytest.mark.asyncio
async def test_unmapped_exception_becomes_unknown_error():
    provider = AnthropicProvider(client=FakeAnthropicClient(raise_exc=ValueError("weird")))
    with pytest.raises(AIError) as exc_info:
        await provider.generate(_req(), "claude-sonnet-4-5")
    assert isinstance(exc_info.value, AIUnknownError)


@pytest.mark.asyncio
async def test_anthropic_without_key_raises_auth_error():
    provider = AnthropicProvider(api_key=None)
    from app.ai.errors import AIAuthenticationError

    with pytest.raises(AIAuthenticationError):
        await provider.generate(_req(), "claude-sonnet-4-5")


@pytest.mark.asyncio
async def test_mock_provider_generate_and_embed():
    provider = MockProvider()
    resp = await provider.generate(_req(), "mock-1")
    assert resp.provider == "mock"
    assert resp.output_tokens >= 1
    emb = await provider.embed(EmbeddingRequest(texts=["x"]), "mock-embed-1")
    assert emb.dimensions == 8
    assert len(emb.vectors) == 1
