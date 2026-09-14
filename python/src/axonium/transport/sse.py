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

**Tool calls arrive in pieces that are not individually valid JSON.** A call is split across as
many deltas as it takes -- ``{``, ``"``, ``city`` -- and only the first carries the identity
(``id``, ``type``, ``function.name``). ``index`` is the correlation key, because ``id`` never
repeats. They are reassembled here so a caller never has to, and the result is the same shape a
non-streaming completion returns.
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
class _PartialToolCall:
    """One tool call being assembled from its fragments."""

    id: str | None = None
    type: str | None = None
    name: str | None = None
    _arguments: list[str] = field(default_factory=list)

    def absorb(self, fragment: dict[str, Any]) -> None:
        """Fold one wire fragment in.

        Identity is recorded the first time it is seen rather than overwritten: ``id`` is
        documented never to repeat, so a second, different one would be a correlation bug, and
        adopting it silently would repoint a call that is already accumulating arguments.
        """
        if self.id is None and isinstance(fragment.get("id"), str):
            self.id = fragment["id"]
        if self.type is None and isinstance(fragment.get("type"), str):
            self.type = fragment["type"]

        function = fragment.get("function")
        if not isinstance(function, dict):
            return
        if self.name is None and isinstance(function.get("name"), str):
            self.name = function["name"]
        piece = function.get("arguments")
        if isinstance(piece, str):
            self._arguments.append(piece)

    def assemble(self) -> dict[str, Any]:
        """The call in the shape a non-streaming completion returns.

        ``arguments`` stays a JSON *string*, exactly as non-streaming delivers it, rather than
        being parsed here. That is what lets one piece of caller code handle both, and it means a
        stream cut short by ``max_tokens`` still hands back the fragment that did arrive instead of
        raising or dropping the call. Decode it with ``json.loads`` when the stream finished
        cleanly.
        """
        return {
            "id": self.id,
            "type": self.type,
            "function": {"name": self.name, "arguments": "".join(self._arguments)},
        }


@dataclass
class StreamAccumulator:
    """Assembles chunks into a final result and decides when a stream has failed."""

    request_id: str | None = None
    trace_id: str | None = None

    _parts: list[str] = field(default_factory=list)
    _reasoning: list[str] = field(default_factory=list)
    _usage: Usage | None = None
    _timings: dict[str, Any] | None = None
    _tool_calls: dict[int, _PartialToolCall] = field(default_factory=dict)
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
        for fragment in chunk.tool_call_fragments:
            self._absorb_tool_call(fragment)

        return chunk

    def _absorb_tool_call(self, fragment: dict[str, Any]) -> None:
        index = fragment.get("index")
        if not isinstance(index, int):
            # Every backend seen so far sends it, and it is the only way to tell two concurrent
            # calls apart. Falling back to slot 0 keeps the single-call case working instead of
            # dropping the call outright, which is the only case a stream without indices can
            # represent unambiguously anyway.
            index = 0
        self._tool_calls.setdefault(index, _PartialToolCall()).absorb(fragment)

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        """The tool calls assembled so far, in the non-streaming shape.

        Ordered by the wire ``index`` rather than by arrival, so a backend that interleaves two
        calls still yields them in the order the model asked for.
        """
        return [self._tool_calls[index].assemble() for index in sorted(self._tool_calls)]

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

        cached = _as_int(self._timings.get("cache_n"))
        # input includes the cached prefix; cache_read says how many of those were cached. Summing
        # is the copy, not an addition of two disjoint buckets.
        prompt = _as_int(self._timings.get("prompt_n")) + cached
        completion = _as_int(self._timings.get("predicted_n"))
        if prompt == 0 and completion == 0:
            return None

        return Usage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
            # By value, not by key: the Timings model dumps every field, so an absent cache_n is
            # present as None. Keying on the name alone would report a measured zero where the
            # backend reported nothing, and make a cold cache indistinguishable from an unknown.
            cache_read_tokens=cached if self._timings.get("cache_n") is not None else None,
            estimated=True,
        )


def _as_int(value: Any) -> int:
    return value if isinstance(value, int) else 0
