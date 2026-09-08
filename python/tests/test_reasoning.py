"""Reasoning models put their chain of thought in a field separate from the answer.

A caller reading only ``content`` sees an empty string while the model is still thinking, which
looks like a broken response rather than an unfinished one. These tests pin the separation: the
two never bleed into each other, and neither is inferred from the other.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from axonium import AsyncAxonium, Axonium
from axonium.models.chat import ChatCompletion
from axonium.transport.sse import StreamAccumulator, decode_line

AUTH_URL = "https://auth.test.invalid/oauth2/token"
CHAT_URL = "https://gateway.test.invalid/v1/chat/completions"

THINKING_ONLY = {
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "",
                "reasoning_content": "Okay, the user wants me to reply with pong. But I",
            },
            "finish_reason": "length",
        }
    ],
    "usage": {"prompt_tokens": 13, "completion_tokens": 16, "total_tokens": 29},
}

FINISHED = {
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "pong",
                "reasoning_content": "The user asked for a single word.",
            },
            "finish_reason": "stop",
        }
    ]
}

PLAIN = {
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}
    ]
}


@pytest.fixture(autouse=True)
def _token() -> None:
    respx.post(AUTH_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": "t", "token_type": "bearer", "expires_in": 300}
        )
    )


class TestCompletions:
    def test_reasoning_is_reachable_when_content_is_empty(self) -> None:
        # The case that motivated this: the model spent its whole budget thinking.
        completion = ChatCompletion.model_validate(THINKING_ONLY)

        assert completion.content == ""
        assert completion.reasoning is not None
        assert completion.reasoning.startswith("Okay, the user wants")
        assert completion.choices[0].finish_reason == "length"

    def test_both_are_available_when_the_model_finished(self) -> None:
        completion = ChatCompletion.model_validate(FINISHED)

        assert completion.content == "pong"
        assert completion.reasoning == "The user asked for a single word."

    def test_reasoning_is_none_for_a_model_that_does_not_reason(self) -> None:
        completion = ChatCompletion.model_validate(PLAIN)

        assert completion.content == "hi"
        assert completion.reasoning is None

    def test_content_never_falls_back_to_reasoning(self) -> None:
        # Conflating the two would hand a caller a chain of thought while it believed it had an
        # answer — worse than an obviously empty string.
        completion = ChatCompletion.model_validate(THINKING_ONLY)

        assert completion.content != completion.reasoning

    def test_both_are_none_without_choices(self) -> None:
        completion = ChatCompletion.model_validate({"choices": []})

        assert completion.content is None
        assert completion.reasoning is None

    @respx.mock
    async def test_reaches_a_caller_through_the_client(self, config_kwargs: dict[str, str]) -> None:
        respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=THINKING_ONLY))

        async with AsyncAxonium(**config_kwargs) as client:
            completion = await client.chat.completions.create(
                model="m", messages=[{"role": "user", "content": "hi"}]
            )

        assert completion.reasoning is not None
        assert completion.choices[0].message is not None
        assert completion.choices[0].message.reasoning_content == completion.reasoning


class TestStreamAccumulation:
    def feed(self, accumulator: StreamAccumulator, lines: list[str]) -> None:
        for line in lines:
            event = decode_line(line)
            if event is None:
                continue
            if event.done:
                accumulator.finish()
            else:
                accumulator.feed(event)

    def test_reasoning_and_content_assemble_separately(self) -> None:
        accumulator = StreamAccumulator()

        self.feed(
            accumulator,
            [
                'data: {"choices":[{"delta":{"reasoning_content":"Okay"}}]}',
                'data: {"choices":[{"delta":{"reasoning_content":", so"}}]}',
                'data: {"choices":[{"delta":{"content":"po"}}]}',
                'data: {"choices":[{"delta":{"content":"ng"}}]}',
                "data: [DONE]",
            ],
        )

        assert accumulator.reasoning == "Okay, so"
        assert accumulator.content == "pong"

    def test_reasoning_is_empty_for_a_plain_model(self) -> None:
        accumulator = StreamAccumulator()

        self.feed(accumulator, ['data: {"choices":[{"delta":{"content":"hi"}}]}'])

        assert accumulator.reasoning == ""
        assert accumulator.content == "hi"

    def test_a_chunk_exposes_whichever_it_carries(self) -> None:
        thinking = decode_line('data: {"choices":[{"delta":{"reasoning_content":"hmm"}}]}')
        answering = decode_line('data: {"choices":[{"delta":{"content":"yes"}}]}')
        assert thinking is not None and answering is not None

        accumulator = StreamAccumulator()
        thought = accumulator.feed(thinking)
        answer = accumulator.feed(answering)

        assert thought.reasoning == "hmm"
        assert thought.content is None
        assert answer.content == "yes"
        assert answer.reasoning is None

    def test_a_chunk_without_choices_exposes_neither(self) -> None:
        event = decode_line('data: {"usage":{"total_tokens":5}}')
        assert event is not None

        chunk = StreamAccumulator().feed(event)

        assert chunk.content is None
        assert chunk.reasoning is None


class TestStreaming:
    @respx.mock
    def test_a_stream_exposes_reasoning_as_it_arrives(self, config_kwargs: dict[str, str]) -> None:
        body = (
            b'data: {"choices":[{"delta":{"reasoning_content":"Think"}}]}\n\n'
            b'data: {"choices":[{"delta":{"reasoning_content":"ing"}}]}\n\n'
            b'data: {"choices":[{"delta":{"content":"pong"}}]}\n\n'
            b"data: [DONE]\n\n"
        )
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                200, content=body, headers={"Content-Type": "text/event-stream"}
            )
        )

        with (
            Axonium(**config_kwargs) as client,
            client.chat.completions.stream(
                model="m", messages=[{"role": "user", "content": "hi"}]
            ) as stream,
        ):
            rendered = [
                ("reasoning", c.reasoning) if c.reasoning else ("content", c.content)
                for c in stream
            ]

            assert stream.reasoning == "Thinking"
            assert stream.content == "pong"

        # A caller rendering progress can tell the two phases apart chunk by chunk.
        assert rendered == [
            ("reasoning", "Think"),
            ("reasoning", "ing"),
            ("content", "pong"),
        ]

    @respx.mock
    async def test_the_async_stream_behaves_identically(
        self, config_kwargs: dict[str, str]
    ) -> None:
        body = (
            b'data: {"choices":[{"delta":{"reasoning_content":"hmm"}}]}\n\n'
            b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            b"data: [DONE]\n\n"
        )
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                200, content=body, headers={"Content-Type": "text/event-stream"}
            )
        )

        async with (
            AsyncAxonium(**config_kwargs) as client,
            client.chat.completions.stream(
                model="m", messages=[{"role": "user", "content": "hi"}]
            ) as stream,
        ):
            async for _ in stream:
                pass

            assert stream.reasoning == "hmm"
            assert stream.content == "ok"

    @respx.mock
    def test_reasoning_survives_an_interrupted_stream(self, config_kwargs: dict[str, str]) -> None:
        from axonium.errors import StreamInterruptedError

        body = (
            b'data: {"choices":[{"delta":{"reasoning_content":"half a thou"}}]}\n\n'
            b'data: {"error": "stream interrupted"}\n\n'
            b"data: [DONE]\n\n"
        )
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                200, content=body, headers={"Content-Type": "text/event-stream"}
            )
        )

        with (
            Axonium(**config_kwargs) as client,
            client.chat.completions.stream(
                model="m", messages=[{"role": "user", "content": "hi"}]
            ) as stream,
        ):
            with pytest.raises(StreamInterruptedError):
                list(stream)

            # partial_content carries the answer, which is empty here; the thinking is still
            # readable on the stream itself.
            assert stream.content == ""
            assert stream.reasoning == "half a thou"
