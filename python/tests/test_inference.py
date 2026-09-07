from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from axonium import AsyncAxonium, Axonium, RetryPolicy
from axonium.errors import (
    BackendUnavailableError,
    ModalityMismatchError,
    RateLimitError,
    UnknownModelError,
    UpstreamError,
)

AUTH_URL = "https://auth.test.invalid/oauth2/token"
CHAT_URL = "https://gateway.test.invalid/v1/chat/completions"
EMBED_URL = "https://gateway.test.invalid/v1/embeddings"
IMAGE_URL = "https://gateway.test.invalid/v1/images/generations"

COMPLETION = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "model": "llama3-8b-q4",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Hello! How can I help?"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 9, "total_tokens": 14},
}


def problem(suffix: str, status: int, **extra: Any) -> dict[str, Any]:
    return {
        "type": f"https://prometheus.internal/errors/{suffix}",
        "status": status,
        "detail": f"about {suffix}",
        **extra,
    }


@pytest.fixture(autouse=True)
def _token() -> None:
    respx.post(AUTH_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": "t", "token_type": "bearer", "expires_in": 300}
        )
    )


@pytest.fixture
def no_wait() -> RetryPolicy:
    """Retries with every delay removed, so the tests exercise the logic without sleeping.

    ``max_backoff=0`` also means any server-supplied ``Retry-After`` above zero is surfaced rather
    than slept through, which is the same rule that keeps a long wait from blocking a real caller.
    """
    return RetryPolicy(initial_backoff=0.0, max_backoff=0.0, jitter=False)


class Caller:
    """Drives whichever client kind is under test through one identical surface."""

    def __init__(self, is_async: bool) -> None:
        self.is_async = is_async

    async def chat(self, config: dict[str, str], **kwargs: Any) -> Any:
        retry = kwargs.pop("retry", None)
        if self.is_async:
            async with AsyncAxonium(retry=retry, **config) as client:
                return await client.chat.completions.create(**kwargs)
        with Axonium(retry=retry, **config) as client:
            return client.chat.completions.create(**kwargs)

    async def embed(self, config: dict[str, str], **kwargs: Any) -> Any:
        if self.is_async:
            async with AsyncAxonium(**config) as client:
                return await client.embeddings.create(**kwargs)
        with Axonium(**config) as client:
            return client.embeddings.create(**kwargs)

    async def image(self, config: dict[str, str], **kwargs: Any) -> Any:
        if self.is_async:
            async with AsyncAxonium(**config) as client:
                return await client.images.generate(**kwargs)
        with Axonium(**config) as client:
            return client.images.generate(**kwargs)


@pytest.fixture(params=[False, True], ids=["sync", "async"])
def caller(request: pytest.FixtureRequest) -> Caller:
    return Caller(bool(request.param))


class TestChatCompletions:
    @respx.mock
    async def test_creates_a_completion(
        self, caller: Caller, config_kwargs: dict[str, str]
    ) -> None:
        route = respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=COMPLETION))

        completion = await caller.chat(
            config_kwargs, model="llama3-8b-q4", messages=[{"role": "user", "content": "Hello"}]
        )

        assert completion.content == "Hello! How can I help?"
        assert completion.usage is not None
        assert completion.usage.total_tokens == 14
        assert route.calls.last.request.headers["Authorization"] == "Bearer t"

    @respx.mock
    async def test_sends_stream_false_for_a_non_streaming_call(
        self, caller: Caller, config_kwargs: dict[str, str]
    ) -> None:
        # Streaming needs the inference:stream scope and is a separate call, so this must never
        # accidentally ask for a stream.
        route = respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=COMPLETION))

        await caller.chat(config_kwargs, model="m", messages=[{"role": "user", "content": "hi"}])

        assert route.calls.last.request.read().find(b'"stream":false') != -1

    @respx.mock
    async def test_exposes_tool_calls(self, caller: Caller, config_kwargs: dict[str, str]) -> None:
        body = {
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{"id": "call_1", "function": {"name": "get_weather"}}],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
        respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=body))

        completion = await caller.chat(
            config_kwargs, model="m", messages=[{"role": "user", "content": "weather?"}]
        )

        assert completion.content is None
        assert completion.tool_calls[0]["id"] == "call_1"
        assert completion.choices[0].finish_reason == "tool_calls"

    @respx.mock
    async def test_keeps_backend_specific_timings_when_present(
        self, caller: Caller, config_kwargs: dict[str, str]
    ) -> None:
        # llama.cpp backends add this; others do not, so it must be optional but not discarded.
        body = {**COMPLETION, "timings": {"predicted_n": 20, "predicted_ms": 50.3}}
        respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=body))

        completion = await caller.chat(
            config_kwargs, model="m", messages=[{"role": "user", "content": "hi"}]
        )

        assert completion.timings is not None
        assert completion.timings.predicted_n == 20

    @respx.mock
    async def test_content_is_none_when_a_backend_returns_no_choices(
        self, caller: Caller, config_kwargs: dict[str, str]
    ) -> None:
        respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json={"choices": []}))

        completion = await caller.chat(
            config_kwargs, model="m", messages=[{"role": "user", "content": "hi"}]
        )

        assert completion.content is None
        assert completion.tool_calls == []

    @respx.mock
    async def test_maps_an_unknown_model_to_a_typed_error(
        self, caller: Caller, config_kwargs: dict[str, str]
    ) -> None:
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(400, json=problem("unknown-model", 400))
        )

        with pytest.raises(UnknownModelError):
            await caller.chat(
                config_kwargs, model="nope", messages=[{"role": "user", "content": "hi"}]
            )


class TestEmbeddings:
    @respx.mock
    async def test_embeds_a_list_of_texts(
        self, caller: Caller, config_kwargs: dict[str, str]
    ) -> None:
        body = {
            "object": "list",
            "data": [
                {"object": "embedding", "index": 0, "embedding": [0.1, 0.2]},
                {"object": "embedding", "index": 1, "embedding": [0.3, 0.4]},
            ],
            "model": "embed-model",
            "usage": {"prompt_tokens": 2, "total_tokens": 2},
        }
        respx.post(EMBED_URL).mock(return_value=httpx.Response(200, json=body))

        result = await caller.embed(config_kwargs, model="embed-model", input=["a", "b"])

        assert len(result) == 2
        assert result.vectors == [[0.1, 0.2], [0.3, 0.4]]
        assert result.usage is not None
        assert result.usage.completion_tokens is None, "embedding has no generation phase"

    @respx.mock
    async def test_a_non_embedding_model_reports_a_modality_mismatch(
        self, caller: Caller, config_kwargs: dict[str, str]
    ) -> None:
        respx.post(EMBED_URL).mock(
            return_value=httpx.Response(400, json=problem("modality-mismatch", 400))
        )

        with pytest.raises(ModalityMismatchError):
            await caller.embed(config_kwargs, model="llama3-8b-q4", input="a")


class TestImages:
    @respx.mock
    async def test_returns_decodable_base64_images(
        self, caller: Caller, config_kwargs: dict[str, str], tmp_path: Path
    ) -> None:
        # Images arrive inline rather than as URLs, so decoding and saving is the caller's job.
        payload = base64.b64encode(b"\x89PNG fake").decode()
        respx.post(IMAGE_URL).mock(
            return_value=httpx.Response(
                200, json={"created": 1, "data": [{"b64_json": payload}], "output_format": "png"}
            )
        )

        result = await caller.image(config_kwargs, model="sd-turbo", prompt="a cat", size="512x512")
        saved = result.data[0].save(tmp_path / "cat.png")

        assert len(result) == 1
        assert result.output_format == "png"
        assert saved.read_bytes() == b"\x89PNG fake"

    @respx.mock
    async def test_an_undecodable_payload_reports_clearly(
        self, caller: Caller, config_kwargs: dict[str, str]
    ) -> None:
        respx.post(IMAGE_URL).mock(
            return_value=httpx.Response(200, json={"data": [{"b64_json": "not!base64"}]})
        )

        result = await caller.image(config_kwargs, model="sd", prompt="a cat")

        with pytest.raises(ValueError, match="not valid base64"):
            result.data[0].to_bytes()

    @respx.mock
    async def test_an_empty_image_reports_clearly(
        self, caller: Caller, config_kwargs: dict[str, str]
    ) -> None:
        respx.post(IMAGE_URL).mock(return_value=httpx.Response(200, json={"data": [{}]}))

        result = await caller.image(config_kwargs, model="sd", prompt="a cat")

        with pytest.raises(ValueError, match="no b64_json"):
            result.data[0].to_bytes()


class TestRetryBehavior:
    @respx.mock
    async def test_retries_a_rate_limit_and_succeeds(
        self, caller: Caller, config_kwargs: dict[str, str], no_wait: RetryPolicy
    ) -> None:
        route = respx.post(CHAT_URL).mock(
            side_effect=[
                httpx.Response(
                    429,
                    headers={"Retry-After": "0"},
                    json=problem("rate-limit-exceeded-requests", 429),
                ),
                httpx.Response(200, json=COMPLETION),
            ]
        )

        completion = await caller.chat(
            config_kwargs,
            model="m",
            messages=[{"role": "user", "content": "hi"}],
            retry=no_wait,
        )

        assert completion.content == "Hello! How can I help?"
        assert route.call_count == 2

    @respx.mock
    async def test_gives_up_and_raises_once_the_budget_is_spent(
        self, caller: Caller, config_kwargs: dict[str, str], no_wait: RetryPolicy
    ) -> None:
        route = respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                429, headers={"Retry-After": "0"}, json=problem("rate-limit-exceeded-requests", 429)
            )
        )

        with pytest.raises(RateLimitError):
            await caller.chat(
                config_kwargs,
                model="m",
                messages=[{"role": "user", "content": "hi"}],
                retry=no_wait,
            )

        assert route.call_count == 3

    @respx.mock
    async def test_does_not_retry_a_generation_that_may_have_reached_a_model(
        self, caller: Caller, config_kwargs: dict[str, str], no_wait: RetryPolicy
    ) -> None:
        # A 502 means the gateway's own three attempts failed; retrying risks a second billable
        # generation, so it is opt-in rather than automatic.
        route = respx.post(CHAT_URL).mock(
            return_value=httpx.Response(502, json=problem("upstream-error", 502))
        )

        with pytest.raises(UpstreamError):
            await caller.chat(
                config_kwargs,
                model="m",
                messages=[{"role": "user", "content": "hi"}],
                retry=no_wait,
            )

        assert route.call_count == 1

    @respx.mock
    async def test_never_retries_a_client_error(
        self, caller: Caller, config_kwargs: dict[str, str], no_wait: RetryPolicy
    ) -> None:
        route = respx.post(CHAT_URL).mock(
            return_value=httpx.Response(400, json=problem("unknown-model", 400))
        )

        with pytest.raises(UnknownModelError):
            await caller.chat(
                config_kwargs,
                model="nope",
                messages=[{"role": "user", "content": "hi"}],
                retry=no_wait,
            )

        assert route.call_count == 1


def test_the_retry_policy_is_visible_to_callers(config_kwargs: dict[str, str]) -> None:
    # Retrying a generation is billable, so a caller must be able to see what the client will do.
    policy = RetryPolicy(max_attempts=1)

    with Axonium(retry=policy, **config_kwargs) as client:
        assert client.retry_policy is policy

    with Axonium(**config_kwargs) as default_client:
        assert default_client.retry_policy.retry_upstream_errors is False


class TestCooldown:
    @respx.mock
    def test_a_known_open_circuit_fails_locally_without_a_request(
        self, config_kwargs: dict[str, str], no_wait: RetryPolicy
    ) -> None:
        # The gateway said when to come back; sending anyway just buys another 503.
        route = respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                503, headers={"Retry-After": "60"}, json=problem("backend-unavailable", 503)
            )
        )

        with Axonium(retry=no_wait, **config_kwargs) as client:
            with pytest.raises(BackendUnavailableError):
                client.chat.completions.create(
                    model="m", messages=[{"role": "user", "content": "hi"}]
                )
            calls_after_first = route.call_count

            with pytest.raises(BackendUnavailableError, match="Failing locally"):
                client.chat.completions.create(
                    model="m", messages=[{"role": "user", "content": "hi"}]
                )

        assert route.call_count == calls_after_first, "the second call must not reach the network"

    @respx.mock
    def test_a_cooldown_is_scoped_to_the_model_that_was_unavailable(
        self, config_kwargs: dict[str, str], no_wait: RetryPolicy
    ) -> None:
        respx.post(CHAT_URL).mock(
            side_effect=[
                httpx.Response(
                    503, headers={"Retry-After": "60"}, json=problem("backend-unavailable", 503)
                ),
                httpx.Response(200, json=COMPLETION),
            ]
        )

        with Axonium(retry=no_wait, **config_kwargs) as client:
            with pytest.raises(BackendUnavailableError):
                client.chat.completions.create(
                    model="unhealthy", messages=[{"role": "user", "content": "hi"}]
                )

            completion = client.chat.completions.create(
                model="healthy", messages=[{"role": "user", "content": "hi"}]
            )

        assert completion.content == "Hello! How can I help?"

    @respx.mock
    def test_a_cooldown_lapses_once_the_requested_wait_has_passed(
        self, config_kwargs: dict[str, str], no_wait: RetryPolicy
    ) -> None:
        respx.post(CHAT_URL).mock(
            side_effect=[
                httpx.Response(
                    503,
                    headers={"Retry-After": "0.05"},
                    json=problem("backend-unavailable", 503),
                ),
                httpx.Response(200, json=COMPLETION),
            ]
        )

        with Axonium(retry=no_wait, **config_kwargs) as client:
            with pytest.raises(BackendUnavailableError):
                client.chat.completions.create(
                    model="m", messages=[{"role": "user", "content": "hi"}]
                )

            time.sleep(0.06)
            completion = client.chat.completions.create(
                model="m", messages=[{"role": "user", "content": "hi"}]
            )

        assert completion.content == "Hello! How can I help?"

    @respx.mock
    def test_a_long_wait_is_surfaced_rather_than_slept_through(
        self, config_kwargs: dict[str, str]
    ) -> None:
        # Blocking a caller inside one call for however long the platform asks is worse than
        # telling them; the error carries retry_after so they can schedule it themselves.
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                503, headers={"Retry-After": "3600"}, json=problem("backend-unavailable", 503)
            )
        )

        started = time.monotonic()
        with (
            Axonium(retry=RetryPolicy(max_backoff=5.0), **config_kwargs) as client,
            pytest.raises(BackendUnavailableError) as caught,
        ):
            client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}])

        assert time.monotonic() - started < 5.0
        assert caught.value.retry_after == 3600.0
