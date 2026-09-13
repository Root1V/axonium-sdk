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
    return ChatCompletionRequest.build({**kwargs, "stream": stream})


class Completions:
    def __init__(self, client: Axonium) -> None:
        self._client = client

    def create(
        self,
        *,
        timeout: float | None = None,
        instance: str | None = None,
        idempotency_key: str | None = None,
        **kwargs: Any,
    ) -> ChatCompletion:
        """Create a non-streaming chat completion.

        Requires the ``inference:read`` scope plus ``model:<id>`` for the model being called;
        streaming requires ``inference:stream`` instead and is a separate call.

        ``timeout`` overrides the client's read timeout for this request alone. The default is
        deliberately long because some backends legitimately take minutes; setting it low and
        retrying is the documented way to end up paying for two generations at once.

        ``instance`` pins the request to one instance, by label (``"#2"``) or by full instance
        id. It rides on a header, never on ``model``: a grant covers a model, billing attributes
        to a model, and the catalog lists models. A pin opts out of load balancing *and* of
        failover — an unavailable pinned instance raises rather than quietly going elsewhere — so
        it is for reproducing a problem or comparing machines, not for normal traffic. It is kept
        across retries, because dropping it would answer a different question than the caller
        asked.

        ``idempotency_key`` makes a retry safe: a repeat with the same key and the same body returns
        the stored result without reaching a model, recording usage, or counting against the spend
        cap, for 24 hours. It is also what lets the SDK retry a client-side timeout at all —
        without a key that retry would be a second billable generation, so it is not attempted.
        Reuse a key only to retry the identical request; reusing it for a different one is
        :class:`~axonium.errors.IdempotencyKeyReuseError`.
        """
        request = _build(kwargs, stream=False)
        self._client._preflight(request.model, "chat")
        response = self._client._send(
            "POST",
            ENDPOINT,
            json=request.to_payload(),
            model=request.model,
            instance=instance,
            idempotency_key=idempotency_key,
            timeout=timeout,
        )
        return dispatch.parse(response, ChatCompletion)

    def stream(
        self,
        *,
        timeout: float | None = None,
        instance: str | None = None,
        idempotency_key: str | None = None,
        **kwargs: Any,
    ) -> ChatCompletionStream:
        """Stream a chat completion.

        Requires the ``inference:stream`` scope, which is distinct from the ``inference:read``
        scope non-streaming calls need: holding one does not grant the other.

        A separate method rather than ``create(stream=True)`` so the return type is honest, the
        scope requirement is explicit, and there is somewhere to say that streams are never
        retried automatically — a failed stream has already delivered partial output, so retrying
        it is a fresh billable generation rather than a resumption.

        ``instance`` pins the request to one instance, by label (``"#2"``) or by full instance
        id. It rides on a header, never on ``model``: a grant covers a model, billing attributes
        to a model, and the catalog lists models. A pin opts out of load balancing *and* of
        failover — an unavailable pinned instance raises rather than quietly going elsewhere — so
        it is for reproducing a problem or comparing machines, not for normal traffic. It is kept
        across retries, because dropping it would answer a different question than the caller
        asked.

        ``idempotency_key`` makes a retry safe: a repeat with the same key and the same body returns
        the stored result without reaching a model, recording usage, or counting against the spend
        cap, for 24 hours. It is also what lets the SDK retry a client-side timeout at all —
        without a key that retry would be a second billable generation, so it is not attempted.
        Reuse a key only to retry the identical request; reusing it for a different one is
        :class:`~axonium.errors.IdempotencyKeyReuseError`.

        On a stream the key has one boundary worth knowing: it replays a stream the gateway
        **finished** and whose delivery your connection dropped, never one the model itself broke —
        that needs resuming rather than replaying, and nothing in this space has built it. Within
        that boundary a streamed replay is as reliable as a non-streaming one, including with no
        wait between calls. Read ``meta.idempotent_replay`` to know which you got.
        """

        request = _build(kwargs, stream=True)
        self._client._preflight(request.model, "chat")
        opener = self._client._open_stream(
            ENDPOINT,
            json=request.to_payload(),
            model=request.model,
            instance=instance,
            idempotency_key=idempotency_key,
            timeout=timeout,
        )
        return ChatCompletionStream(opener, self._client._stream_diagnoser(request.model))


class AsyncCompletions:
    def __init__(self, client: AsyncAxonium) -> None:
        self._client = client

    async def create(
        self,
        *,
        timeout: float | None = None,
        instance: str | None = None,
        idempotency_key: str | None = None,
        **kwargs: Any,
    ) -> ChatCompletion:
        """Create a non-streaming chat completion. See :meth:`Completions.create`."""
        request = _build(kwargs, stream=False)
        await self._client._preflight(request.model, "chat")
        response = await self._client._send(
            "POST",
            ENDPOINT,
            json=request.to_payload(),
            model=request.model,
            instance=instance,
            idempotency_key=idempotency_key,
            timeout=timeout,
        )
        return dispatch.parse(response, ChatCompletion)

    def stream(
        self,
        *,
        timeout: float | None = None,
        instance: str | None = None,
        idempotency_key: str | None = None,
        **kwargs: Any,
    ) -> AsyncChatCompletionStream:
        """Stream a chat completion. See :meth:`Completions.stream`."""

        request = _build(kwargs, stream=True)
        opener = self._client._open_stream(
            ENDPOINT,
            json=request.to_payload(),
            model=request.model,
            instance=instance,
            idempotency_key=idempotency_key,
            timeout=timeout,
        )

        async def preflight() -> None:
            await self._client._preflight(request.model, "chat")

        return AsyncChatCompletionStream(
            opener, self._client._stream_diagnoser(request.model), preflight
        )


class Chat:
    def __init__(self, client: Axonium) -> None:
        self.completions = Completions(client)


class AsyncChat:
    def __init__(self, client: AsyncAxonium) -> None:
        self.completions = AsyncCompletions(client)
