"""Server-sent event parsing for streaming completions.

Two things about this stream are easy to get wrong, and both are handled here rather than at the
call sites:

**Failures arrive in-band.** By the time a backend fails mid-generation the ``200`` and
``text/event-stream`` headers are already committed, so a broken stream cannot be signalled with an
HTTP status. The gateway emits an error chunk instead, followed by the terminal sentinel. A client
that only checks the status code sees a truncated response as a successful one.

**Token counts may never arrive as ``usage``.** llama.cpp-family backends send no usage chunk at
all; the counts have to be recovered from the final chunk's ``timings``. Other backends do send
``usage``, so both are handled, and a derived figure is marked as estimated so a caller can tell
the difference.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from axonium.errors import StreamInterruptedError
from axonium.models.chat import ChatCompletionChunk
from axonium.models.common import Usage

__all__ = ["DONE", "SSEEvent", "StreamAccumulator", "decode_line"]

DATA_PREFIX = "data:"

#: Terminal sentinel. The gateway appends this itself at stream end regardless of whether the
#: backend sent one, so it can be relied on to mark completion.
DONE = "[DONE]"


@dataclass(frozen=True)
class SSEEvent:
    """One decoded ``data:`` line.

    Either a terminal marker or a payload — :func:`decode_line` never produces an event that is
    neither, which is why consumers do not have to handle that case.
    """

    payload: dict[str, Any] = field(default_factory=dict)
    done: bool = False


def decode_line(line: str) -> SSEEvent | None:
    """Decode one wire line, or return ``None`` for a line that carries no event.

    Blank lines separate records, and anything that is not a ``data:`` field (comments, ``event:``,
    ``id:``) is not part of this protocol and is skipped rather than treated as an error.
    """
    stripped = line.strip()
    if not stripped or not stripped.startswith(DATA_PREFIX):
        return None

    data = stripped[len(DATA_PREFIX) :].strip()
    if data == DONE:
        return SSEEvent(done=True)
    if not data:
        return None

    try:
        payload = json.loads(data)
    except ValueError:
        # A single unparseable chunk is not worth destroying an otherwise good stream over.
        return None

    return SSEEvent(payload=payload) if isinstance(payload, dict) else None


@dataclass
class StreamAccumulator:
    """Assembles chunks into a final result and decides when a stream has failed."""

    request_id: str | None = None
    trace_id: str | None = None

    _parts: list[str] = field(default_factory=list)
    _reasoning: list[str] = field(default_factory=list)
    _usage: Usage | None = None
    _timings: dict[str, Any] | None = None
    _done: bool = False

    @property
    def content(self) -> str:
        """Everything received so far, including on a stream that failed partway."""
        return "".join(self._parts)

    @property
    def reasoning(self) -> str:
        """The chain of thought received so far, assembled separately from the answer."""
        return "".join(self._reasoning)

    @property
    def done(self) -> bool:
        """Whether the terminal sentinel was seen."""
        return self._done

    def finish(self) -> None:
        """Record that the terminal sentinel was seen."""
        self._done = True

    def feed(self, event: SSEEvent) -> ChatCompletionChunk:
        """Consume one payload event and return its chunk.

        Raises :class:`~axonium.errors.StreamInterruptedError` on an in-band failure.
        """
        payload = event.payload

        # Detected by the presence of the key, not by matching its message: the gateway documents
        # only one failure string today, but that is an implementation detail rather than contract.
        if "error" in payload:
            raise StreamInterruptedError(
                f"The stream was interrupted: {payload['error']}",
                partial_content=self.content,
                request_id=self.request_id,
                trace_id=self.trace_id,
                raw=payload,
            )

        chunk = ChatCompletionChunk.model_validate(payload)

        if chunk.content:
            self._parts.append(chunk.content)
        if chunk.reasoning:
            self._reasoning.append(chunk.reasoning)
        if chunk.usage is not None:
            self._usage = chunk.usage
        if chunk.timings is not None:
            self._timings = chunk.timings.model_dump()

        return chunk

    def usage(self) -> Usage | None:
        """Token accounting for the completed stream, if it can be determined.

        A backend-reported ``usage`` wins. Otherwise the counts are reconstructed from the final
        chunk's timings and flagged ``estimated`` so a caller never mistakes a derived number for a
        reported one.
        """
        if self._usage is not None:
            return self._usage
        if not self._timings:
            return None

        prompt = _as_int(self._timings.get("prompt_n")) + _as_int(self._timings.get("cache_n"))
        completion = _as_int(self._timings.get("predicted_n"))
        if prompt == 0 and completion == 0:
            return None

        return Usage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
            estimated=True,
        )


def _as_int(value: Any) -> int:
    return value if isinstance(value, int) else 0
