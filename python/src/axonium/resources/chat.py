"""The chat completions endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from axonium.models.chat import ChatCompletion
from axonium.models.requests import ChatCompletionRequest
from axonium.streaming import AsyncChatCompletionStream, ChatCompletionStream
from axonium.transport import dispatch

if TYPE_CHECKING:
    from axonium.client import AsyncAxonium, Axonium

__all__ = ["AsyncChat", "AsyncCompletions", "Chat", "Completions"]

ENDPOINT = "/v1/chat/completions"


def _build(kwargs: dict[str, Any], *, stream: bool) -> ChatCompletionRequest:
    return ChatCompletionRequest(**kwargs, stream=stream)


class Completions:
    def __init__(self, client: Axonium) -> None:
        self._client = client

    def create(self, *, timeout: float | None = None, **kwargs: Any) -> ChatCompletion:
        """Create a non-streaming chat completion.

        Requires the ``inference:read`` scope plus ``model:<id>`` for the model being called;
        streaming requires ``inference:stream`` instead and is a separate call.

        ``timeout`` overrides the client's read timeout for this request alone. The default is
        deliberately long because some backends legitimately take minutes; setting it low and
        retrying is the documented way to end up paying for two generations at once.
        """
        request = _build(kwargs, stream=False)
        response = self._client._send(
            "POST", ENDPOINT, json=request.to_payload(), model=request.model, timeout=timeout
        )
        return dispatch.parse(response, ChatCompletion)

    def stream(self, *, timeout: float | None = None, **kwargs: Any) -> ChatCompletionStream:
        """Stream a chat completion.

        Requires the ``inference:stream`` scope, which is distinct from the ``inference:read``
        scope non-streaming calls need: holding one does not grant the other.

        A separate method rather than ``create(stream=True)`` so the return type is honest, the
        scope requirement is explicit, and there is somewhere to say that streams are never
        retried automatically — a failed stream has already delivered partial output, so retrying
        it is a fresh billable generation rather than a resumption.
        """
        request = _build(kwargs, stream=True)
        opener = self._client._open_stream(
            ENDPOINT, json=request.to_payload(), model=request.model, timeout=timeout
        )
        return ChatCompletionStream(opener)


class AsyncCompletions:
    def __init__(self, client: AsyncAxonium) -> None:
        self._client = client

    async def create(self, *, timeout: float | None = None, **kwargs: Any) -> ChatCompletion:
        """Create a non-streaming chat completion. See :meth:`Completions.create`."""
        request = _build(kwargs, stream=False)
        response = await self._client._send(
            "POST", ENDPOINT, json=request.to_payload(), model=request.model, timeout=timeout
        )
        return dispatch.parse(response, ChatCompletion)

    def stream(self, *, timeout: float | None = None, **kwargs: Any) -> AsyncChatCompletionStream:
        """Stream a chat completion. See :meth:`Completions.stream`."""
        request = _build(kwargs, stream=True)
        opener = self._client._open_stream(
            ENDPOINT, json=request.to_payload(), model=request.model, timeout=timeout
        )
        return AsyncChatCompletionStream(opener)


class Chat:
    def __init__(self, client: Axonium) -> None:
        self.completions = Completions(client)


class AsyncChat:
    def __init__(self, client: AsyncAxonium) -> None:
        self.completions = AsyncCompletions(client)
