"""Chat completion responses.

Bodies are forwarded from the backend verbatim, so only the fields the gateway contract actually
guarantees are modeled; everything else is preserved rather than dropped, and typed loosely enough
that a differently-shaped backend cannot make a valid response fail to parse.
"""

from __future__ import annotations

import json
from typing import Any

from axonium.errors import ToolCallArgumentsError
from axonium.models.common import APIObject, Usage, _Passthrough

__all__ = [
    "ChatChoice",
    "ChatCompletion",
    "ChatCompletionChunk",
    "ChoiceDelta",
    "CompletionMessage",
    "FunctionCall",
    "StreamChoice",
    "Timings",
    "ToolCall",
]


class FunctionCall(_Passthrough):
    """The function a tool call names, and the arguments it was called with."""

    name: str | None = None

    #: The arguments **as the model produced them**: a JSON string, not a decoded object.
    #:
    #: Kept raw on purpose. Streaming delivers this in fragments that are only valid once
    #: concatenated, and a generation cut short by ``max_tokens`` leaves a string that was never
    #: going to parse -- decoding here would turn that into an exception raised from inside a
    #: response model, for a caller who only wanted to see what the model had managed to say. Use
    #: :meth:`ToolCall.parse_arguments` when you want the object.
    arguments: str = ""


class ToolCall(_Passthrough):
    """A tool call, in the one shape both streaming and non-streaming produce.

    Streamed calls arrive split across fragments that are individually invalid JSON; the SDK
    reassembles them into exactly this, so the same caller code handles both.
    """

    id: str | None = None
    #: ``"function"`` for everything the gateway forwards today. Modelled rather than assumed,
    #: because the field exists on the wire precisely so it can grow.
    type: str | None = None
    function: FunctionCall = FunctionCall()

    @property
    def name(self) -> str | None:
        """The function name, without reaching through :attr:`function`."""
        return self.function.name

    def parse_arguments(self) -> dict[str, Any]:
        """Decode :attr:`FunctionCall.arguments` into an object.

        Raises :class:`~axonium.errors.ToolCallArgumentsError` when the string is not valid JSON.
        The usual cause is a generation that ran out of tokens mid-call, so check ``finish_reason``
        before calling this on a response you have not verified completed.
        """
        try:
            parsed = json.loads(self.function.arguments)
        except ValueError as error:
            raise ToolCallArgumentsError(
                f"The arguments for tool call {self.id!r} are not valid JSON ({error}). "
                f"A generation stopped by max_tokens leaves them truncated -- check "
                f"finish_reason. Raw value: {self.function.arguments!r}",
                tool_call=self,
            ) from error

        if not isinstance(parsed, dict):
            raise ToolCallArgumentsError(
                f"The arguments for tool call {self.id!r} decoded to {type(parsed).__name__}, "
                f"not an object. Raw value: {self.function.arguments!r}",
                tool_call=self,
            )
        return parsed


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
    #: ``None`` when the model returned tool calls instead of prose, and empty on a reasoning
    #: model that spent its whole token budget thinking.
    content: str | None = None
    #: A reasoning model's chain of thought, kept separate from the answer. Not part of the
    #: gateway's documented contract — backends that do not reason simply omit it.
    reasoning_content: str | None = None
    tool_calls: list[ToolCall] | None = None


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
        """Text of the first choice, or ``None`` for a tool-call response.

        Empty on a reasoning model that ran out of tokens before it finished thinking — check
        :attr:`reasoning` and ``finish_reason`` to tell that apart from a model with nothing to
        say.
        """
        if not self.choices:
            return None
        message = self.choices[0].message
        return message.content if message else None

    @property
    def reasoning(self) -> str | None:
        """The first choice's chain of thought, if the model produced one."""
        if not self.choices:
            return None
        message = self.choices[0].message
        return message.reasoning_content if message else None

    @property
    def tool_calls(self) -> list[ToolCall]:
        """Tool calls from the first choice.

        The gateway does not interpret them, and neither does this SDK beyond giving them a shape:
        ``arguments`` is still the model's own string, reachable raw.
        """
        if not self.choices:
            return []
        message = self.choices[0].message
        return message.tool_calls or [] if message else []


class ChoiceDelta(_Passthrough):
    """The incremental part of a streamed choice."""

    role: str | None = None
    content: str | None = None
    #: Reasoning models stream their thinking here first, then switch to ``content``.
    reasoning_content: str | None = None
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
    def reasoning(self) -> str | None:
        """Reasoning carried by this chunk, if the model is still thinking.

        A chunk carries one or the other, so a caller rendering progress can show thinking rather
        than appearing to hang while the model reasons.
        """
        if not self.choices:
            return None
        delta = self.choices[0].delta
        return delta.reasoning_content if delta else None

    @property
    def tool_call_fragments(self) -> list[dict[str, Any]]:
        """This chunk's raw tool-call fragments, which are *not* usable on their own.

        A fragment carries a slice of an ``arguments`` string that is invalid JSON by itself, and
        only the first one for a given ``index`` carries the identity. Use the stream's
        ``tool_calls`` for the assembled calls; this is here for a caller who wants to watch them
        arrive.
        """
        if not self.choices:
            return []
        delta = self.choices[0].delta
        return delta.tool_calls or [] if delta else []

    @property
    def finish_reason(self) -> str | None:
        return self.choices[0].finish_reason if self.choices else None
