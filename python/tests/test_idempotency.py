"""Idempotency keys, and the retry rule they unlock.

A client-side timeout is the one failure this SDK refuses to retry, because the backend is
probably still generating and a retry would queue a second billable generation. An
``Idempotency-Key`` removes that objection and nothing else does, so the rule is conditional on
the key rather than relaxed outright.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from axonium import AsyncAxonium, Axonium, RetryPolicy
from axonium.client import IDEMPOTENCY_HEADER
from axonium.errors import (
    IdempotencyConflictError,
    InvalidRequestError,
    TimeoutError,
    TransportError,
)

AUTH_URL = "https://auth.test.invalid/oauth2/token"
CHAT = "https://gateway.test.invalid/v1/chat/completions"
MESSAGES = [{"role": "user", "content": "hi"}]
COMPLETION = {
    "id": "c1",
    "model": "qwen3-0.6b",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ],
}


@pytest.fixture(autouse=True)
def _token() -> None:
    respx.post(AUTH_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": "t", "token_type": "bearer", "expires_in": 300}
        )
    )


class TestKeyReachesTheWire:
    @respx.mock
    def test_the_header_is_sent_when_a_key_is_given(self, config_kwargs: dict[str, str]) -> None:
        route = respx.post(CHAT).mock(return_value=httpx.Response(200, json=COMPLETION))

        with Axonium(**config_kwargs) as client:
            client.chat.completions.create(
                model="qwen3-0.6b", messages=MESSAGES, idempotency_key="key-1"
            )

        assert route.calls.last.request.headers[IDEMPOTENCY_HEADER] == "key-1"

    @respx.mock
    def test_no_header_is_sent_without_a_key(self, config_kwargs: dict[str, str]) -> None:
        # The SDK does not invent keys: generating one silently would change billing semantics
        # for a caller who never asked for them.
        route = respx.post(CHAT).mock(return_value=httpx.Response(200, json=COMPLETION))

        with Axonium(**config_kwargs) as client:
            client.chat.completions.create(model="qwen3-0.6b", messages=MESSAGES)

        assert IDEMPOTENCY_HEADER not in route.calls.last.request.headers

    @respx.mock
    def test_a_replayed_response_is_flagged(self, config_kwargs: dict[str, str]) -> None:
        # A replay reached no model and recorded no usage, so its `usage` describes the original
        # generation. A caller adding it to a running total needs to know.
        respx.post(CHAT).mock(
            return_value=httpx.Response(200, json=COMPLETION, headers={"Idempotent-Replay": "true"})
        )

        with Axonium(**config_kwargs) as client:
            result = client.chat.completions.create(
                model="qwen3-0.6b", messages=MESSAGES, idempotency_key="key-1"
            )

        assert result.meta is not None
        assert result.meta.idempotent_replay is True


class TestTimeoutRetryIsConditionalOnTheKey:
    @respx.mock
    def test_a_timeout_is_retried_under_a_key(self, config_kwargs: dict[str, str]) -> None:
        route = respx.post(CHAT).mock(
            side_effect=[httpx.ReadTimeout("too slow"), httpx.Response(200, json=COMPLETION)]
        )

        with Axonium(**config_kwargs) as client:
            result = client.chat.completions.create(
                model="qwen3-0.6b", messages=MESSAGES, idempotency_key="key-1"
            )

        assert result.content == "ok"
        assert route.call_count == 2
        keys = {call.request.headers.get(IDEMPOTENCY_HEADER) for call in route.calls}
        assert keys == {"key-1"}, "the retry must carry the same key, or it is a new generation"

    @respx.mock
    def test_a_timeout_without_a_key_is_never_retried(self, config_kwargs: dict[str, str]) -> None:
        route = respx.post(CHAT).mock(
            side_effect=[httpx.ReadTimeout("too slow"), httpx.Response(200, json=COMPLETION)]
        )

        with Axonium(**config_kwargs) as client, pytest.raises(TimeoutError):
            client.chat.completions.create(model="qwen3-0.6b", messages=MESSAGES)

        assert route.call_count == 1, "retrying an unkeyed timeout queues a second generation"

    @respx.mock
    async def test_the_async_client_behaves_identically(
        self, config_kwargs: dict[str, str]
    ) -> None:
        route = respx.post(CHAT).mock(
            side_effect=[httpx.ReadTimeout("too slow"), httpx.Response(200, json=COMPLETION)]
        )

        async with AsyncAxonium(**config_kwargs) as client:
            result = await client.chat.completions.create(
                model="qwen3-0.6b", messages=MESSAGES, idempotency_key="key-1"
            )

        assert result.content == "ok"
        assert route.call_count == 2

    @respx.mock
    def test_a_connection_failure_is_still_not_retried(self, config_kwargs: dict[str, str]) -> None:
        # A key makes a *timeout* safe to repeat. It says nothing about a host refusing traffic,
        # which is not the expensive case and is already covered by the cooldown registry.
        route = respx.post(CHAT).mock(
            side_effect=[httpx.ConnectError("refused"), httpx.Response(200, json=COMPLETION)]
        )

        with Axonium(**config_kwargs) as client, pytest.raises(TransportError):
            client.chat.completions.create(
                model="qwen3-0.6b", messages=MESSAGES, idempotency_key="key-1"
            )

        assert route.call_count == 1


class TestConflictAndStreaming:
    @respx.mock
    def test_a_conflict_is_typed_and_not_retried(self, config_kwargs: dict[str, str]) -> None:
        # Three causes share this one type and are told apart only by prose, so none is retried:
        # two must never be, and the third the caller can decide about from the message.
        route = respx.post(CHAT).mock(
            return_value=httpx.Response(
                409,
                json={
                    "type": "https://prometheus.internal/errors/idempotency-conflict",
                    "title": "Idempotency Conflict",
                    "status": 409,
                    "detail": "was already used for a different request",
                    "request_id": "r1",
                },
            )
        )

        with (
            Axonium(**config_kwargs) as client,
            pytest.raises(IdempotencyConflictError) as caught,
        ):
            client.chat.completions.create(
                model="qwen3-0.6b", messages=MESSAGES, idempotency_key="reused"
            )

        assert caught.value.retryable is False
        assert route.call_count == 1

    def test_streaming_refuses_a_key(self, config_kwargs: dict[str, str]) -> None:
        # The gateway accepts a key on a stream, ignores it, and generates again, with nothing in
        # the response to say so. Refusing is the only way to stop a caller believing otherwise.
        with (
            Axonium(**config_kwargs) as client,
            pytest.raises(InvalidRequestError, match="cannot be made idempotent"),
        ):
            client.chat.completions.stream(
                model="qwen3-0.6b", messages=MESSAGES, idempotency_key="key-1"
            )


class TestEdges:
    @respx.mock
    def test_both_headers_travel_together(self, config_kwargs: dict[str, str]) -> None:
        # A pinned request can also be keyed: the two answer different questions.
        route = respx.post(CHAT).mock(return_value=httpx.Response(200, json=COMPLETION))

        with Axonium(**config_kwargs) as client:
            client.chat.completions.create(
                model="qwen3-0.6b", messages=MESSAGES, instance="#2", idempotency_key="key-1"
            )

        sent = route.calls.last.request.headers
        assert sent["X-Prometheus-Instance"] == "#2"
        assert sent[IDEMPOTENCY_HEADER] == "key-1"

    @respx.mock
    def test_a_keyed_timeout_still_gives_up_eventually(self, config_kwargs: dict[str, str]) -> None:
        # A key makes the retry free of a second generation, not free of a deadline: the attempt
        # budget still applies, or a permanently slow backend would loop forever.
        route = respx.post(CHAT).mock(side_effect=httpx.ReadTimeout("too slow"))

        policy = RetryPolicy(max_attempts=2, initial_backoff=0.0)
        with (
            Axonium(**config_kwargs, retry=policy) as c,
            pytest.raises(TimeoutError),
        ):
            c.chat.completions.create(
                model="qwen3-0.6b", messages=MESSAGES, idempotency_key="key-1"
            )

        assert route.call_count == 2

    async def test_the_async_stream_refuses_a_key_too(self, config_kwargs: dict[str, str]) -> None:
        async with AsyncAxonium(**config_kwargs) as client:
            with pytest.raises(InvalidRequestError, match="cannot be made idempotent"):
                await client.chat.completions.stream(
                    model="qwen3-0.6b", messages=MESSAGES, idempotency_key="key-1"
                )


def test_an_overlong_key_is_refused_before_the_wire(config_kwargs: dict[str, str]) -> None:
    # The gateway reports an over-length key as 409 idempotency-conflict — the same type a genuine
    # reuse produces — so a caller branching on that would go hunting a repeat that never happened.
    # Checking here costs nothing and names the real problem.
    with (
        Axonium(**config_kwargs) as client,
        pytest.raises(InvalidRequestError, match="at most 255"),
    ):
        client.chat.completions.create(
            model="qwen3-0.6b", messages=MESSAGES, idempotency_key="x" * 256
        )


def test_a_key_at_the_limit_is_accepted(config_kwargs: dict[str, str]) -> None:
    with respx.mock:
        respx.post(AUTH_URL).mock(
            return_value=httpx.Response(
                200, json={"access_token": "t", "token_type": "bearer", "expires_in": 300}
            )
        )
        route = respx.post(CHAT).mock(return_value=httpx.Response(200, json=COMPLETION))

        with Axonium(**config_kwargs) as client:
            client.chat.completions.create(
                model="qwen3-0.6b", messages=MESSAGES, idempotency_key="x" * 255
            )

    assert route.calls.last.request.headers[IDEMPOTENCY_HEADER] == "x" * 255
