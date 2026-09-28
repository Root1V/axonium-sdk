"""Streamed chat completions.

A stream is exposed as a context manager rather than a bare iterator so the underlying connection
is always released, including when a caller stops consuming early.

**A rejection that arrives instead of the stream is retried. A stream that has already begun never
is.** They are different failures and only one of them is safe to repeat:

*Before the stream begins*, the gateway reads the engine's status before the ``200`` and
``text/event-stream`` headers exist, so a refusal arrives as an ordinary error response — the same
status and body the non-streaming form of the endpoint returns. Nothing was generated and nothing
was billed, so reopening is a first generation rather than a second, and it is retried on exactly
the terms every other request is, ``Retry-After`` included. The gateway performs no internal
retries on a streamed request, so this attempt is the only one there is.

*Once the stream has begun*, a failure cannot arrive as a status — the headers are already
committed — so it arrives in band, and it is never retried. Part of the response was delivered and
part was billed; repeating it is a fresh generation, not a resumption. Deciding whether that is
acceptable belongs to the caller, who is the only one who knows what the partial output was used
for.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from types import TracebackType
from typing import Any

import httpx

from axonium.errors import APIError
from axonium.models.chat import ChatCompletionChunk, ToolCall
from axonium.models.common import ATTEMPTS_EXTENSION, WAITED_EXTENSION, ResponseMeta, Usage
from axonium.transport import dispatch
from axonium.transport.sse import StreamAccumulator, decode_line

__all__ = ["AsyncChatCompletionStream", "ChatCompletionStream"]

logger = logging.getLogger("axonium.streaming")


@dataclass(frozen=True)
class StreamOpen:
    """How to open a stream, and what to do when an open is refused.

    ``opener`` is a factory rather than a single opener because an open refused before the stream
    begins is retried, and the second attempt needs a connection of its own.

    ``after_failure`` is the retry decision every non-streaming request already goes through: it
    diagnoses the error, records any cooldown the gateway asked for, logs the wait, and returns the
    seconds to wait -- or ``None`` to stop and raise. Keeping it on the client rather than
    reimplementing it here is what makes "retried like any other request" true rather than
    approximately true.
    """

    opener: Callable[[], Any]
    after_failure: Callable[[APIError, int], float | None]
    succeeded: Callable[[], None]


class _StreamBase:
    def __init__(self, opening: StreamOpen) -> None:
        self._opening = opening
        self._opener: Any = None
        self._response: httpx.Response | None = None
        self._state = StreamAccumulator()
        self._meta: ResponseMeta | None = None

    @property
    def content(self) -> str:
        """Everything received so far.

        Also populated on a stream that failed partway, which is what makes a partial result
        recoverable rather than lost.
        """
        return self._state.content

    @property
    def reasoning(self) -> str:
        """The chain of thought received so far, if the model is a reasoning model.

        Assembled separately from :attr:`content`, so rendering progress can show that the model
        is thinking rather than appearing to hang before the first answer token arrives.
        """
        return self._state.reasoning

    @property
    def tool_calls(self) -> list[ToolCall]:
        """The tool calls the model asked for, as the same type non-streaming returns.

        Reassembled from fragments that are individually invalid JSON, so this is what a caller
        should read rather than the per-chunk ``tool_call_fragments``. ``arguments`` is a JSON
        string here exactly as it is non-streaming, and ``call.parse_arguments()`` decodes it.

        Populated as the stream runs, and complete once it ends. A stream that stopped on
        ``finish_reason == "length"`` leaves a truncated ``arguments`` that will not parse -- check
        the finish reason before decoding.
        """
        return self._state.tool_calls

    @property
    def meta(self) -> ResponseMeta | None:
        """Correlation IDs and rate-limit budget from the response headers."""
        return self._meta

    def usage(self) -> Usage | None:
        """Token accounting once the stream has completed.

        ``None`` while the stream is still running, or when the backend reported nothing to derive
        it from. A figure reconstructed from timings is marked ``estimated``.
        """
        return self._state.usage()

    def _begin(
        self, opener: Any, response: httpx.Response, *, attempts: int, waited: float
    ) -> None:
        # Stamped on the response rather than passed along, so whoever builds the ResponseMeta does
        # not have to know a retry loop exists -- the same way the non-streaming loop records it. A
        # stream that was reopened therefore says so, instead of reporting a first attempt.
        response.extensions[WAITED_EXTENSION] = waited
        response.extensions[ATTEMPTS_EXTENSION] = attempts
        self._opener = opener
        self._response = response
        self._meta = ResponseMeta.from_response(response)
        self._state.request_id = self._meta.request_id
        self._state.trace_id = self._meta.trace_id
        self._opening.succeeded()


class ChatCompletionStream(_StreamBase):
    """A streaming completion.

    Iterate it inside a ``with`` block::

        with client.chat.completions.stream(model=..., messages=...) as stream:
            for chunk in stream:
                print(chunk.content or "", end="")
            print(stream.usage())
    """

    def __enter__(self) -> ChatCompletionStream:
        """Open the stream, reopening it if the gateway refuses before the stream begins.

        The loop can only ever repeat an open that produced no stream: the moment a non-error
        response is in hand it returns, and nothing past that point reopens anything.
        """
        attempt = 1
        waited = 0.0
        while True:
            opener = self._opening.opener()
            response = opener.__enter__()
            if not response.is_error:
                self._begin(opener, response, attempts=attempt, waited=waited)
                return self

            # An error response is a normal buffered body, so it has to be read before it can be
            # turned into a typed error.
            response.read()
            try:
                dispatch.raise_for_status(response)
            except APIError as error:
                delay = self._opening.after_failure(error, attempt)
                # Released before sleeping rather than after: holding a refused connection open for
                # the length of a Retry-After keeps a socket for nothing.
                opener.__exit__(type(error), error, error.__traceback__)
                if delay is None:
                    raise
                time.sleep(delay)
                waited += delay
                attempt += 1

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._opener is not None:
            self._opener.__exit__(exc_type, exc, traceback)

    def __iter__(self) -> Iterator[ChatCompletionChunk]:
        if self._response is None:
            raise RuntimeError("Use the stream inside a `with` block before iterating it.")

        for line in self._response.iter_lines():
            event = decode_line(line)
            if event is None:
                continue
            if event.done:
                self._state.finish()
                return
            yield self._state.feed(event)

    def close(self) -> None:
        if self._response is not None:
            self._response.close()


class AsyncChatCompletionStream(_StreamBase):
    """A streaming completion. See :class:`ChatCompletionStream`."""

    def __init__(self, opening: StreamOpen, preflight: Callable[[], Awaitable[None]]) -> None:
        super().__init__(opening)
        self._preflight = preflight

    async def __aenter__(self) -> AsyncChatCompletionStream:
        """See :meth:`ChatCompletionStream.__enter__`."""
        # The sync stream checks this when stream() is called; here the method that builds the
        # stream cannot await, so the check moves to the point the request is actually sent.
        await self._preflight()

        attempt = 1
        waited = 0.0
        while True:
            opener = self._opening.opener()
            response = await opener.__aenter__()
            if not response.is_error:
                self._begin(opener, response, attempts=attempt, waited=waited)
                return self

            await response.aread()
            try:
                dispatch.raise_for_status(response)
            except APIError as error:
                delay = self._opening.after_failure(error, attempt)
                await opener.__aexit__(type(error), error, error.__traceback__)
                if delay is None:
                    raise
                await asyncio.sleep(delay)
                waited += delay
                attempt += 1

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._opener is not None:
            await self._opener.__aexit__(exc_type, exc, traceback)

    async def __aiter__(self) -> AsyncIterator[ChatCompletionChunk]:
        if self._response is None:
            raise RuntimeError("Use the stream inside an `async with` block before iterating it.")

        async for line in self._response.aiter_lines():
            event = decode_line(line)
            if event is None:
                continue
            if event.done:
                self._state.finish()
                return
            yield self._state.feed(event)

    async def aclose(self) -> None:
        if self._response is not None:
            await self._response.aclose()
