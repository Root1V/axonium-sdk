"""Chat completion responses.

Bodies are forwarded from the backend verbatim, so only the fields the gateway contract actually
guarantees are modeled; everything else is preserved rather than dropped, and typed loosely enough
that a differently-shaped backend cannot make a valid response fail to parse.
"""

from __future__ import annotations

from typing import Any

from axonium.models.common import APIObject, Usage, _Passthrough

__all__ = [
    "ChatChoice",
    "ChatCompletion",
    "ChatCompletionChunk",
    "ChoiceDelta",
    "CompletionMessage",
    "StreamChoice",
    "Timings",
]


class Timings(_Passthrough):
    """Backend timing figures.

    Emitted by llama.cpp-family backends only; MLX, vLLM and SGLang backends do not include it, so
    never depend on its presence.
    """

    prompt_n: int | None = None
    prompt_ms: float | None = None
    prompt_per_second: float | None = None
    predicted_n: int | None = None
    predicted_ms: float | None = None
    predicted_per_second: float | None = None
    predicted_per_token_ms: float | None = None
    cache_n: int | None = None


class CompletionMessage(_Passthrough):
    """The assistant message in a completion."""

    role: str | None = None
    #: ``None`` when the model returned tool calls instead of prose.
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class ChatChoice(_Passthrough):
    index: int | None = None
    message: CompletionMessage | None = None
    #: ``"stop"``, ``"length"``, ``"tool_calls"``, or whatever else the backend reports.
    finish_reason: str | None = None


class ChatCompletion(APIObject):
    """A non-streaming chat completion.

    ``id``, ``created`` and ``system_fingerprint`` are backend-dependent rather than guaranteed by
    the gateway contract, so they are optional here.
    """

    id: str | None = None
    object: str | None = None
    created: int | None = None
    model: str | None = None
    choices: list[ChatChoice] = []  # noqa: RUF012 - pydantic copies defaults per instance
    usage: Usage | None = None
    system_fingerprint: str | None = None
    timings: Timings | None = None

    @property
    def content(self) -> str | None:
        """Text of the first choice, or ``None`` for a tool-call response."""
        if not self.choices:
            return None
        message = self.choices[0].message
        return message.content if message else None

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        """Tool calls from the first choice.

        Passed through untouched — the gateway does not interpret them.
        """
        if not self.choices:
            return []
        message = self.choices[0].message
        return message.tool_calls or [] if message else []


class ChoiceDelta(_Passthrough):
    """The incremental part of a streamed choice."""

    role: str | None = None
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class StreamChoice(_Passthrough):
    index: int | None = None
    delta: ChoiceDelta | None = None
    finish_reason: str | None = None


class ChatCompletionChunk(_Passthrough):
    """One chunk of a streamed completion.

    Chunks are forwarded from the backend essentially verbatim, so almost everything is optional:
    what a given backend puts in each chunk varies, and a chunk carrying only a ``finish_reason``
    or only ``timings`` is normal rather than malformed.
    """

    id: str | None = None
    object: str | None = None
    created: int | None = None
    model: str | None = None
    choices: list[StreamChoice] = []  # noqa: RUF012 - pydantic copies defaults per instance
    #: Present on some backends; llama.cpp-family models send token counts only via ``timings``.
    usage: Usage | None = None
    timings: Timings | None = None

    @property
    def content(self) -> str | None:
        """Text carried by this chunk's first choice, if any."""
        if not self.choices:
            return None
        delta = self.choices[0].delta
        return delta.content if delta else None

    @property
    def finish_reason(self) -> str | None:
        return self.choices[0].finish_reason if self.choices else None
