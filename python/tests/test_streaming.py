from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from axonium import AsyncAxonium, Axonium
from axonium.errors import ForbiddenError, StreamInterruptedError
from axonium.transport.sse import DONE, StreamAccumulator, decode_line

AUTH_URL = "https://auth.test.invalid/oauth2/token"
CHAT_URL = "https://gateway.test.invalid/v1/chat/completions"


@pytest.fixture(autouse=True)
def _token() -> None:
    respx.post(AUTH_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": "t", "token_type": "bearer", "expires_in": 300}
        )
    )


@pytest.fixture
def wire(spec_dir: Path) -> Any:
    """Loads the shared cross-language SSE fixtures as literal wire bytes."""

    def load(name: str) -> bytes:
        return (spec_dir / "fixtures" / f"{name}.sse").read_bytes()

    return load


def sse_response(body: bytes, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        content=body,
        headers={"Content-Type": "text/event-stream", **(headers or {})},
    )


class TestLineDecoding:
    def test_decodes_a_data_line(self) -> None:
        event = decode_line('data: {"choices": []}')

        assert event is not None
        assert event.payload == {"choices": []}
        assert not event.done

    def test_recognizes_the_terminal_sentinel(self) -> None:
        # The gateway appends this itself regardless of what the backend sent.
        event = decode_line(f"data: {DONE}")

        assert event is not None
        assert event.done

    @pytest.mark.parametrize(
        "line",
        ["", "   ", ": a comment", "event: message", "id: 42", "data:", "data: not json"],
        ids=["blank", "whitespace", "comment", "event-field", "id-field", "empty", "unparseable"],
    )
    def test_lines_carrying_no_event_are_skipped(self, line: str) -> None:
        # Blank lines separate records, and a single bad chunk is not worth destroying an
        # otherwise good stream over.
        assert decode_line(line) is None

    def test_a_non_object_payload_is_skipped(self) -> None:
        assert decode_line("data: [1, 2, 3]") is None

    def test_tolerates_a_missing_space_after_the_colon(self) -> None:
        event = decode_line('data:{"a": 1}')

        assert event is not None
        assert event.payload == {"a": 1}


class TestAccumulator:
    def feed_all(self, accumulator: StreamAccumulator, lines: list[str]) -> None:
        for line in lines:
            event = decode_line(line)
            if event is None:
                continue
            if event.done:
                accumulator.finish()
            else:
                accumulator.feed(event)

    def test_assembles_content_across_chunks(self) -> None:
        accumulator = StreamAccumulator()

        self.feed_all(
            accumulator,
            [
                'data: {"choices":[{"delta":{"content":"Hello"}}]}',
                'data: {"choices":[{"delta":{"content":", world"}}]}',
                f"data: {DONE}",
            ],
        )

        assert accumulator.content == "Hello, world"
        assert accumulator.done

    def test_an_in_band_error_raises_and_keeps_the_partial_output(self) -> None:
        # The failure arrives on a 200 that already committed its headers, so nothing but the
        # chunk itself signals it.
        accumulator = StreamAccumulator(request_id="req-1", trace_id="trace-1")
        accumulator.feed(decode_line('data: {"choices":[{"delta":{"content":"Hello, wor"}}]}'))  # type: ignore[arg-type]

        with pytest.raises(StreamInterruptedError) as caught:
            accumulator.feed(decode_line('data: {"error": "stream interrupted"}'))  # type: ignore[arg-type]

        assert caught.value.partial_content == "Hello, wor"
        assert caught.value.request_id == "req-1"
        assert caught.value.trace_id == "trace-1"

    def test_any_error_key_signals_failure_not_just_the_documented_message(self) -> None:
        # The gateway emits one message today, but that is implementation detail rather than
        # contract, so detection keys off the field's presence.
        accumulator = StreamAccumulator()

        with pytest.raises(StreamInterruptedError):
            accumulator.feed(decode_line('data: {"error": {"code": "something_new"}}'))  # type: ignore[arg-type]

    def test_prefers_a_backend_reported_usage(self) -> None:
        accumulator = StreamAccumulator()

        self.feed_all(
            accumulator,
            ['data: {"usage":{"prompt_tokens":5,"completion_tokens":9,"total_tokens":14}}'],
        )
        usage = accumulator.usage()

        assert usage is not None
        assert usage.total_tokens == 14
        assert usage.estimated is False

    def test_derives_usage_from_timings_when_no_usage_chunk_arrives(self) -> None:
        # llama.cpp-family backends never send a usage chunk while streaming.
        accumulator = StreamAccumulator()

        self.feed_all(
            accumulator, ['data: {"timings":{"prompt_n":13,"cache_n":2,"predicted_n":20}}']
        )
        usage = accumulator.usage()

        assert usage is not None
        assert usage.prompt_tokens == 15, "prompt_n plus cache_n"
        assert usage.completion_tokens == 20
        assert usage.total_tokens == 35
        assert usage.estimated is True, "a derived figure must never look like a reported one"

    def test_usage_is_none_when_nothing_reported_it(self) -> None:
        accumulator = StreamAccumulator()

        self.feed_all(accumulator, ['data: {"choices":[{"delta":{"content":"hi"}}]}'])

        assert accumulator.usage() is None

    def test_finish_marks_the_stream_complete(self) -> None:
        accumulator = StreamAccumulator()

        accumulator.finish()

        assert accumulator.done

    def test_empty_timings_do_not_fabricate_a_zero_count(self) -> None:
        accumulator = StreamAccumulator()

        self.feed_all(accumulator, ['data: {"timings":{"prompt_ms":1.5}}'])

        assert accumulator.usage() is None


class TestSyncStreaming:
    @respx.mock
    def test_streams_a_completion_from_the_shared_fixture(
        self, config_kwargs: dict[str, str], wire: Any
    ) -> None:
        respx.post(CHAT_URL).mock(return_value=sse_response(wire("chat_stream_ok")))

        with (
            Axonium(**config_kwargs) as client,
            client.chat.completions.stream(
                model="llama3-8b-q4", messages=[{"role": "user", "content": "Hi"}]
            ) as stream,
        ):
            chunks = list(stream)
            text = stream.content
            usage = stream.usage()

        assert text == "Hello, world!"
        assert "".join(chunk.content or "" for chunk in chunks) == "Hello, world!"
        assert chunks[-1].finish_reason == "stop"
        assert usage is not None
        assert usage.estimated is True

    @respx.mock
    def test_requests_a_stream_from_the_gateway(
        self, config_kwargs: dict[str, str], wire: Any
    ) -> None:
        route = respx.post(CHAT_URL).mock(return_value=sse_response(wire("chat_stream_ok")))

        with (
            Axonium(**config_kwargs) as client,
            client.chat.completions.stream(
                model="m", messages=[{"role": "user", "content": "Hi"}]
            ) as stream,
        ):
            list(stream)

        assert b'"stream":true' in route.calls.last.request.read()

    @respx.mock
    def test_detects_an_interrupted_stream_and_surfaces_partial_output(
        self, config_kwargs: dict[str, str], wire: Any
    ) -> None:
        # The HTTP status is 200; only the in-band chunk reveals the failure.
        respx.post(CHAT_URL).mock(return_value=sse_response(wire("chat_stream_interrupted")))

        with (
            Axonium(**config_kwargs) as client,
            client.chat.completions.stream(
                model="m", messages=[{"role": "user", "content": "Hi"}]
            ) as stream,
        ):
            with pytest.raises(StreamInterruptedError) as caught:
                list(stream)

            assert stream.content == "Hello, wor"

        assert caught.value.partial_content == "Hello, wor"

    @respx.mock
    def test_a_stream_that_only_terminates_yields_nothing(
        self, config_kwargs: dict[str, str], wire: Any
    ) -> None:
        respx.post(CHAT_URL).mock(return_value=sse_response(wire("chat_stream_empty")))

        with (
            Axonium(**config_kwargs) as client,
            client.chat.completions.stream(
                model="m", messages=[{"role": "user", "content": "Hi"}]
            ) as stream,
        ):
            assert list(stream) == []
            assert stream.content == ""

    @respx.mock
    def test_uses_a_backend_reported_usage_when_one_arrives(
        self, config_kwargs: dict[str, str], wire: Any
    ) -> None:
        respx.post(CHAT_URL).mock(return_value=sse_response(wire("chat_stream_usage")))

        with (
            Axonium(**config_kwargs) as client,
            client.chat.completions.stream(
                model="m", messages=[{"role": "user", "content": "Hi"}]
            ) as stream,
        ):
            list(stream)
            usage = stream.usage()

        assert usage is not None
        assert usage.total_tokens == 14
        assert usage.estimated is False

    @respx.mock
    def test_a_missing_stream_scope_surfaces_as_a_typed_error(
        self, config_kwargs: dict[str, str]
    ) -> None:
        # Streaming needs inference:stream, which inference:read does not imply — a common
        # integration mistake worth surfacing precisely.
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                403,
                json={
                    "type": "https://prometheus.internal/errors/forbidden",
                    "status": 403,
                    "detail": "Missing inference:stream scope.",
                },
            )
        )

        with Axonium(**config_kwargs) as client:
            opened = client.chat.completions.stream(
                model="m", messages=[{"role": "user", "content": "Hi"}]
            )

            with pytest.raises(ForbiddenError, match="inference:stream"), opened:
                pass

    @respx.mock
    def test_correlation_ids_are_available_on_a_stream(
        self, config_kwargs: dict[str, str], wire: Any
    ) -> None:
        respx.post(CHAT_URL).mock(
            return_value=sse_response(
                wire("chat_stream_ok"), headers={"X-Request-ID": "req-7", "X-Trace-ID": "trace-7"}
            )
        )

        with (
            Axonium(**config_kwargs) as client,
            client.chat.completions.stream(
                model="m", messages=[{"role": "user", "content": "Hi"}]
            ) as stream,
        ):
            list(stream)
            meta = stream.meta

        assert meta is not None
        assert meta.request_id == "req-7"

    @respx.mock
    def test_a_stream_cut_short_without_the_sentinel_yields_what_arrived(
        self, config_kwargs: dict[str, str]
    ) -> None:
        # A connection dropped mid-stream never delivers [DONE]. Iteration ends with whatever was
        # received rather than hanging or raising.
        body = b'data: {"choices":[{"delta":{"content":"Half a sen"}}]}\n\n'
        respx.post(CHAT_URL).mock(return_value=sse_response(body))

        with (
            Axonium(**config_kwargs) as client,
            client.chat.completions.stream(
                model="m", messages=[{"role": "user", "content": "Hi"}]
            ) as stream,
        ):
            chunks = list(stream)

            assert stream.content == "Half a sen"
            assert len(chunks) == 1

    @respx.mock
    def test_close_releases_the_connection(self, config_kwargs: dict[str, str], wire: Any) -> None:
        respx.post(CHAT_URL).mock(return_value=sse_response(wire("chat_stream_ok")))

        with (
            Axonium(**config_kwargs) as client,
            client.chat.completions.stream(
                model="m", messages=[{"role": "user", "content": "Hi"}]
            ) as stream,
        ):
            stream.close()
            stream.close()

    def test_closing_a_stream_that_was_never_opened_is_harmless(
        self, config_kwargs: dict[str, str]
    ) -> None:
        with Axonium(**config_kwargs) as client:
            client.chat.completions.stream(
                model="m", messages=[{"role": "user", "content": "Hi"}]
            ).close()

    def test_iterating_before_opening_is_a_clear_error(self, config_kwargs: dict[str, str]) -> None:
        with Axonium(**config_kwargs) as client:
            stream = client.chat.completions.stream(
                model="m", messages=[{"role": "user", "content": "Hi"}]
            )

            with pytest.raises(RuntimeError, match="`with` block"):
                list(stream)


class TestAsyncStreaming:
    @respx.mock
    async def test_streams_a_completion_from_the_shared_fixture(
        self, config_kwargs: dict[str, str], wire: Any
    ) -> None:
        respx.post(CHAT_URL).mock(return_value=sse_response(wire("chat_stream_ok")))

        async with (
            AsyncAxonium(**config_kwargs) as client,
            client.chat.completions.stream(
                model="llama3-8b-q4", messages=[{"role": "user", "content": "Hi"}]
            ) as stream,
        ):
            chunks = [chunk async for chunk in stream]
            text = stream.content
            usage = stream.usage()

        assert text == "Hello, world!"
        assert chunks[-1].finish_reason == "stop"
        assert usage is not None
        assert usage.prompt_tokens == 15

    @respx.mock
    async def test_detects_an_interrupted_stream(
        self, config_kwargs: dict[str, str], wire: Any
    ) -> None:
        respx.post(CHAT_URL).mock(return_value=sse_response(wire("chat_stream_interrupted")))

        async with (
            AsyncAxonium(**config_kwargs) as client,
            client.chat.completions.stream(
                model="m", messages=[{"role": "user", "content": "Hi"}]
            ) as stream,
        ):
            with pytest.raises(StreamInterruptedError):
                [chunk async for chunk in stream]

            assert stream.content == "Hello, wor"

    @respx.mock
    async def test_an_error_response_raises_on_entry(self, config_kwargs: dict[str, str]) -> None:
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                400,
                json={
                    "type": "https://prometheus.internal/errors/unknown-model",
                    "status": 400,
                },
            )
        )

        from axonium.errors import UnknownModelError

        async with AsyncAxonium(**config_kwargs) as client:
            with pytest.raises(UnknownModelError):
                async with client.chat.completions.stream(
                    model="nope", messages=[{"role": "user", "content": "Hi"}]
                ):
                    pass

    @respx.mock
    async def test_aclose_releases_the_connection(
        self, config_kwargs: dict[str, str], wire: Any
    ) -> None:
        respx.post(CHAT_URL).mock(return_value=sse_response(wire("chat_stream_ok")))

        async with (
            AsyncAxonium(**config_kwargs) as client,
            client.chat.completions.stream(
                model="m", messages=[{"role": "user", "content": "Hi"}]
            ) as stream,
        ):
            await stream.aclose()
            await stream.aclose()

    @respx.mock
    async def test_a_stream_cut_short_without_the_sentinel_yields_what_arrived(
        self, config_kwargs: dict[str, str]
    ) -> None:
        body = b'data: {"choices":[{"delta":{"content":"Half a sen"}}]}\n\n'
        respx.post(CHAT_URL).mock(return_value=sse_response(body))

        async with (
            AsyncAxonium(**config_kwargs) as client,
            client.chat.completions.stream(
                model="m", messages=[{"role": "user", "content": "Hi"}]
            ) as stream,
        ):
            chunks = [chunk async for chunk in stream]

            assert stream.content == "Half a sen"
            assert len(chunks) == 1

    async def test_closing_a_stream_that_was_never_opened_is_harmless(
        self, config_kwargs: dict[str, str]
    ) -> None:
        async with AsyncAxonium(**config_kwargs) as client:
            await client.chat.completions.stream(
                model="m", messages=[{"role": "user", "content": "Hi"}]
            ).aclose()

    async def test_iterating_before_opening_is_a_clear_error(
        self, config_kwargs: dict[str, str]
    ) -> None:
        async with AsyncAxonium(**config_kwargs) as client:
            stream = client.chat.completions.stream(
                model="m", messages=[{"role": "user", "content": "Hi"}]
            )

            with pytest.raises(RuntimeError, match="`async with` block"):
                [chunk async for chunk in stream]
