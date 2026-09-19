"""Fake vendor SDK clients for provider adapter tests. No network, no real SDK."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


# --------------------------------------------------------------------------- #
# Anthropic fakes
# --------------------------------------------------------------------------- #
class _FakeAnthropicMessages:
    def __init__(self, text: str = "hello world", raise_exc: Exception | None = None) -> None:
        self._text = text
        self._raise = raise_exc

    async def create(self, **kwargs: Any) -> Any:
        if self._raise is not None:
            raise self._raise
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self._text)],
            usage=SimpleNamespace(input_tokens=11, output_tokens=7),
            stop_reason="end_turn",
        )

    def stream(self, **kwargs: Any) -> Any:
        text = self._text
        raise_exc = self._raise

        class _Ctx:
            async def __aenter__(self_inner) -> Any:
                if raise_exc is not None:
                    raise raise_exc
                return self_inner

            async def __aexit__(self_inner, *a: Any) -> bool:
                return False

            @property
            def text_stream(self_inner):
                async def gen():
                    for word in text.split():
                        yield word + " "

                return gen()

            async def get_final_message(self_inner) -> Any:
                return SimpleNamespace(
                    usage=SimpleNamespace(input_tokens=11, output_tokens=3),
                    stop_reason="end_turn",
                )

        return _Ctx()


class FakeAnthropicClient:
    def __init__(self, text: str = "hello world", raise_exc: Exception | None = None) -> None:
        self.messages = _FakeAnthropicMessages(text, raise_exc)


# --------------------------------------------------------------------------- #
# OpenAI fakes
# --------------------------------------------------------------------------- #
class _FakeOpenAIChatCompletions:
    def __init__(self, text: str = "hi there", raise_exc: Exception | None = None) -> None:
        self._text = text
        self._raise = raise_exc

    async def create(self, **kwargs: Any) -> Any:
        if self._raise is not None:
            raise self._raise
        if kwargs.get("stream"):
            text = self._text

            async def gen():
                for word in text.split():
                    yield SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(content=word + " "),
                                finish_reason=None,
                            )
                        ],
                        usage=None,
                    )
                yield SimpleNamespace(
                    choices=[
                        SimpleNamespace(delta=SimpleNamespace(content=None), finish_reason="stop")
                    ],
                    usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2, total_tokens=7),
                )

            return gen()
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=self._text), finish_reason="stop")
            ],
            usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2, total_tokens=7),
        )


class _FakeOpenAIEmbeddings:
    def __init__(self, dims: int = 4, raise_exc: Exception | None = None) -> None:
        self._dims = dims
        self._raise = raise_exc

    async def create(self, *, model: str, input: list[str]) -> Any:  # noqa: A002
        if self._raise is not None:
            raise self._raise
        data = [SimpleNamespace(embedding=[0.1 * (i + 1)] * self._dims) for i in range(len(input))]
        return SimpleNamespace(data=data, usage=SimpleNamespace(prompt_tokens=3 * len(input)))


class FakeOpenAIClient:
    def __init__(
        self,
        text: str = "hi there",
        *,
        raise_exc: Exception | None = None,
        embed_raise: Exception | None = None,
        dims: int = 4,
    ) -> None:
        self.chat = SimpleNamespace(completions=_FakeOpenAIChatCompletions(text, raise_exc))
        self.embeddings = _FakeOpenAIEmbeddings(dims, embed_raise)
