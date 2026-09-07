"""Streamed chat completions.

A stream is exposed as a context manager rather than a bare iterator so the underlying connection
is always released, including when a caller stops consuming early.

**Streams are never retried automatically.** The gateway does not retry them internally either,
and by the time a stream fails part of the response has already been delivered to the caller — a
retry is a fresh generation, billed again, not a resumption. Deciding whether that is acceptable
belongs to the caller, who is the only one who knows what the partial output was used for.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from types import TracebackType
from typing import Any

import httpx

from axonium.errors import APIError
from axonium.models.chat import ChatCompletionChunk
from axonium.models.common import ResponseMeta, Usage
from axonium.transport import dispatch
from axonium.transport.sse import StreamAccumulator, decode_line

__all__ = ["AsyncChatCompletionStream", "ChatCompletionStream"]

logger = logging.getLogger("axonium.streaming")


class _StreamBase:
    def __init__(self, diagnose: Callable[[APIError], None]) -> None:
        self._response: httpx.Response | None = None
        self._state = StreamAccumulator()
        self._meta: ResponseMeta | None = None
        self._diagnose = diagnose

    def _raise_for_status(self, response: httpx.Response) -> None:
        try:
            dispatch.raise_for_status(response)
        except APIError as error:
            # Streaming is where the read/stream scope distinction trips people up, so the
            # diagnosis matters more here than anywhere else.
            self._diagnose(error)
            raise

    @property
    def content(self) -> str:
        """Everything received so far.

        Also populated on a stream that failed partway, which is what makes a partial result
        recoverable rather than lost.
        """
        return self._state.content

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

    def _begin(self, response: httpx.Response) -> None:
        self._response = response
        self._meta = ResponseMeta.from_headers(response.headers)
        self._state.request_id = self._meta.request_id
        self._state.trace_id = self._meta.trace_id


class ChatCompletionStream(_StreamBase):
    """A streaming completion.

    Iterate it inside a ``with`` block::

        with client.chat.completions.stream(model=..., messages=...) as stream:
            for chunk in stream:
                print(chunk.content or "", end="")
            print(stream.usage())
    """

    def __init__(self, opener: Any, diagnose: Callable[[APIError], None]) -> None:
        super().__init__(diagnose)
        self._opener = opener

    def __enter__(self) -> ChatCompletionStream:
        response = self._opener.__enter__()
        # An error response is a normal buffered body, so it has to be read before it can be
        # turned into a typed error.
        if response.is_error:
            response.read()
            self._raise_for_status(response)
        self._begin(response)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
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

    def __init__(
        self,
        opener: Any,
        diagnose: Callable[[APIError], None],
        preflight: Callable[[], Awaitable[None]],
    ) -> None:
        super().__init__(diagnose)
        self._opener = opener
        self._preflight = preflight

    async def __aenter__(self) -> AsyncChatCompletionStream:
        # The sync stream checks this when stream() is called; here the method that builds the
        # stream cannot await, so the check moves to the point the request is actually sent.
        await self._preflight()
        response = await self._opener.__aenter__()
        if response.is_error:
            await response.aread()
            self._raise_for_status(response)
        self._begin(response)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
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
