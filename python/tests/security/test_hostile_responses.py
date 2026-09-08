"""The SDK must survive a server that misbehaves, without hanging or crashing oddly.

The gateway is trusted, but what arrives at the client is not always what the gateway sent: a
reverse proxy can inject an HTML error page, a load balancer can truncate a body, an outage can cut
a stream mid-token. The property these tests pin is narrow and important — whatever comes back, the
SDK raises one of its own errors or returns a usable object. It never raises a bare KeyError,
IndexError, or JSONDecodeError at the call site, and it never loops forever.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from axonium import AsyncAxonium, Axonium, RetryPolicy
from axonium.errors import APIError, AxoniumError, StreamInterruptedError

AUTH_URL = "https://auth.test.invalid/oauth2/token"
CHAT_URL = "https://gateway.test.invalid/v1/chat/completions"
CATALOG_URL = "https://gateway.test.invalid/v1/models"

MESSAGES = [{"role": "user", "content": "hi"}]


@pytest.fixture(autouse=True)
def _token() -> None:
    respx.post(AUTH_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": "t", "token_type": "bearer", "expires_in": 300}
        )
    )


@pytest.fixture
def client(config_kwargs: dict[str, str]) -> Axonium:
    return Axonium(retry=RetryPolicy(initial_backoff=0.0, max_backoff=0.0), **config_kwargs)


def sse(body: bytes) -> httpx.Response:
    return httpx.Response(200, content=body, headers={"Content-Type": "text/event-stream"})


class TestMalformedBodies:
    @pytest.mark.parametrize(
        ("label", "response"),
        [
            ("html-error-page", httpx.Response(200, text="<html><body>502</body></html>")),
            ("empty-body", httpx.Response(200, content=b"")),
            ("truncated-json", httpx.Response(200, content=b'{"choices": [')),
            ("json-array", httpx.Response(200, json=[1, 2, 3])),
            ("json-string", httpx.Response(200, json="just a string")),
            ("json-null", httpx.Response(200, json=None)),
            ("invalid-utf8", httpx.Response(200, content=b"\xff\xfe\x00garbage")),
        ],
    )
    @respx.mock
    def test_a_nonsense_success_body_raises_an_sdk_error(
        self, client: Axonium, label: str, response: httpx.Response
    ) -> None:
        respx.post(CHAT_URL).mock(return_value=response)

        with client, pytest.raises(AxoniumError):
            client.chat.completions.create(model="m", messages=MESSAGES)

    @respx.mock
    def test_a_response_missing_every_field_still_parses(self, client: Axonium) -> None:
        # Everything past `choices` is backend-dependent, so an empty object is legal, not an
        # error. It must come back as an empty result rather than an exception.
        respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json={}))

        with client:
            completion = client.chat.completions.create(model="m", messages=MESSAGES)

        assert completion.content is None
        assert completion.usage is None

    @respx.mock
    def test_wrongly_typed_fields_are_reported_not_silently_accepted(self, client: Axonium) -> None:
        respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json={"choices": "not-a-list"}))

        with client, pytest.raises(Exception) as caught:
            client.chat.completions.create(model="m", messages=MESSAGES)

        assert not isinstance(caught.value, KeyError | IndexError | AttributeError)

    @respx.mock
    def test_a_deeply_nested_payload_does_not_blow_the_stack(self, client: Axonium) -> None:
        # A hostile or buggy upstream could nest arbitrarily; parsing must fail cleanly rather
        # than raising RecursionError from inside the SDK.
        nested: object = "leaf"
        for _ in range(200):
            nested = {"next": nested}
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                200, json={"choices": [{"message": {"role": "a", "content": "hi"}}], "x": nested}
            )
        )

        with client:
            completion = client.chat.completions.create(model="m", messages=MESSAGES)

        assert completion.content == "hi"

    @respx.mock
    def test_an_error_status_with_a_nonsense_body_still_maps_to_an_api_error(
        self, client: Axonium
    ) -> None:
        respx.post(CHAT_URL).mock(return_value=httpx.Response(503, text="<html>down</html>"))

        with client, pytest.raises(APIError) as caught:
            client.chat.completions.create(model="m", messages=MESSAGES)

        assert caught.value.status == 503
        assert caught.value.type_suffix is None


class TestHostileHeaders:
    @pytest.mark.parametrize(
        "retry_after",
        ["-100", "not-a-number", "999999999999999999999", "", "1e400", "NaN", "Infinity"],
    )
    @respx.mock
    def test_a_malformed_retry_after_cannot_stall_or_crash_the_client(
        self, client: Axonium, retry_after: str
    ) -> None:
        # A negative or absurd value must not translate into a negative sleep, an overflow, or a
        # wait measured in centuries.
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                429,
                headers={"Retry-After": retry_after},
                json={"type": "https://x/errors/rate-limit-exceeded-requests", "status": 429},
            )
        )

        with client, pytest.raises(APIError) as caught:
            client.chat.completions.create(model="m", messages=MESSAGES)

        if caught.value.retry_after is not None:
            assert caught.value.retry_after >= 0

    @respx.mock
    def test_malformed_rate_limit_headers_are_ignored_rather_than_fatal(
        self, client: Axonium
    ) -> None:
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                200,
                headers={
                    "X-RateLimit-Remaining-Requests": "lots",
                    "X-RateLimit-Limit-Tokens": "-1",
                },
                json={"choices": []},
            )
        )

        with client:
            completion = client.chat.completions.create(model="m", messages=MESSAGES)

        assert completion.meta is not None

    @respx.mock
    def test_an_enormous_header_value_does_not_break_parsing(self, client: Axonium) -> None:
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                200, headers={"X-Request-ID": "x" * 8000}, json={"choices": []}
            )
        )

        with client:
            completion = client.chat.completions.create(model="m", messages=MESSAGES)

        assert completion.meta is not None
        assert len(completion.meta.request_id or "") == 8000


class TestHostileStreams:
    @respx.mock
    def test_a_stream_that_never_terminates_ends_with_the_connection(self, client: Axonium) -> None:
        # No [DONE] arrives. Iteration must finish when the body does rather than block.
        respx.post(CHAT_URL).mock(
            return_value=sse(b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n')
        )

        with client, client.chat.completions.stream(model="m", messages=MESSAGES) as stream:
            chunks = list(stream)

        assert len(chunks) == 1
        assert stream.content == "hi"

    @respx.mock
    def test_junk_interleaved_with_valid_chunks_is_skipped(self, client: Axonium) -> None:
        body = (
            b"garbage that is not an sse line\n\n"
            b"data: {not json}\n\n"
            b": a comment\n\n"
            b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            b"data: \n\n"
            b"data: [DONE]\n\n"
        )
        respx.post(CHAT_URL).mock(return_value=sse(body))

        with client, client.chat.completions.stream(model="m", messages=MESSAGES) as stream:
            list(stream)

        assert stream.content == "ok"

    @respx.mock
    def test_an_error_chunk_stops_the_stream_even_if_more_follows(self, client: Axonium) -> None:
        # A stream that kept yielding after a failure would hand the caller a response that looks
        # complete but is not.
        body = (
            b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
            b'data: {"error": "stream interrupted"}\n\n'
            b'data: {"choices":[{"delta":{"content":"MORE"}}]}\n\n'
            b"data: [DONE]\n\n"
        )
        respx.post(CHAT_URL).mock(return_value=sse(body))

        with (
            client,
            client.chat.completions.stream(model="m", messages=MESSAGES) as stream,
            pytest.raises(StreamInterruptedError) as caught,
        ):
            list(stream)

        assert caught.value.partial_content == "partial"
        assert "MORE" not in caught.value.partial_content

    @respx.mock
    def test_an_error_chunk_with_a_structured_value_is_still_detected(
        self, client: Axonium
    ) -> None:
        # Detection keys off the presence of the field, so a future shape change cannot make the
        # SDK silently stop noticing failures.
        body = b'data: {"error": {"code": 500, "detail": "boom"}}\n\ndata: [DONE]\n\n'
        respx.post(CHAT_URL).mock(return_value=sse(body))

        with (
            client,
            client.chat.completions.stream(model="m", messages=MESSAGES) as stream,
            pytest.raises(StreamInterruptedError),
        ):
            list(stream)

    @respx.mock
    def test_a_very_large_chunk_is_handled(self, client: Axonium) -> None:
        payload = json.dumps({"choices": [{"delta": {"content": "x" * 500_000}}]}).encode()
        respx.post(CHAT_URL).mock(return_value=sse(b"data: " + payload + b"\n\ndata: [DONE]\n\n"))

        with client, client.chat.completions.stream(model="m", messages=MESSAGES) as stream:
            list(stream)

        assert len(stream.content) == 500_000

    @respx.mock
    def test_an_error_status_on_a_stream_raises_before_iteration(self, client: Axonium) -> None:
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                403, json={"type": "https://x/errors/forbidden", "status": 403}
            )
        )

        with client:
            opened = client.chat.completions.stream(model="m", messages=MESSAGES)
            with pytest.raises(APIError), opened:
                pass


class TestHostileTokenEndpoint:
    @pytest.mark.parametrize(
        ("label", "response"),
        [
            ("html", httpx.Response(200, text="<html>login</html>")),
            ("empty", httpx.Response(200, content=b"")),
            ("array", httpx.Response(200, json=[])),
            ("no-token", httpx.Response(200, json={"expires_in": 300})),
            ("negative-expiry", httpx.Response(200, json={"access_token": "t", "expires_in": -1})),
            ("token-not-a-string", httpx.Response(200, json={"access_token": 42, "expires_in": 1})),
        ],
    )
    @respx.mock
    def test_an_unusable_token_response_fails_loudly(
        self, config_kwargs: dict[str, str], label: str, response: httpx.Response
    ) -> None:
        # Caching nonsense here would turn one bad auth response into every later request failing
        # for an unrelated-looking reason.
        respx.post(AUTH_URL).mock(return_value=response)

        with Axonium(**config_kwargs) as client, pytest.raises(AxoniumError):
            client.models.mine()

    @respx.mock
    def test_an_absurd_ttl_is_clamped_rather_than_believed(
        self, config_kwargs: dict[str, str]
    ) -> None:
        # Believing it would mean never refreshing proactively. Rejecting it would mean no request
        # works at all. Clamping to the platform's documented ceiling keeps the client usable.
        from axonium.auth import MAX_PLAUSIBLE_TTL

        respx.post(AUTH_URL).mock(
            return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 1e30})
        )
        respx.get("https://gateway.test.invalid/v1/models/mine").mock(
            return_value=httpx.Response(200, json={"object": "list", "data": []})
        )

        with Axonium(**config_kwargs) as client:
            client.models.mine()
            token = client._auth.cached_token

        assert token is not None
        assert token.expires_in == MAX_PLAUSIBLE_TTL

    @respx.mock
    def test_a_non_jwt_token_is_still_usable(self, config_kwargs: dict[str, str]) -> None:
        # Introspection is a convenience. An opaque token must not stop the SDK from sending it.
        respx.post(AUTH_URL).mock(
            return_value=httpx.Response(
                200, json={"access_token": "opaque-not-a-jwt", "expires_in": 300}
            )
        )
        route = respx.get("https://gateway.test.invalid/v1/models/mine").mock(
            return_value=httpx.Response(200, json={"object": "list", "data": []})
        )

        with Axonium(**config_kwargs) as client:
            client.models.mine()
            assert client.token_claims() is not None

        assert route.calls.last.request.headers["Authorization"] == "Bearer opaque-not-a-jwt"

    @respx.mock
    def test_a_401_loop_terminates(self, config_kwargs: dict[str, str]) -> None:
        # An auth-service that always says no must not put the SDK in an endless refresh cycle.
        token = respx.post(AUTH_URL).mock(
            return_value=httpx.Response(
                200, json={"access_token": "t", "token_type": "bearer", "expires_in": 300}
            )
        )
        gateway = respx.get("https://gateway.test.invalid/v1/models/mine").mock(
            return_value=httpx.Response(
                401, json={"type": "https://x/errors/token-expired", "status": 401}
            )
        )

        with Axonium(**config_kwargs) as client, pytest.raises(APIError):
            client.models.mine()

        assert gateway.call_count == 2, "one attempt plus exactly one retry"
        assert token.call_count == 2


class TestConcurrencySafety:
    @respx.mock
    async def test_many_concurrent_failures_do_not_corrupt_shared_state(
        self, config_kwargs: dict[str, str]
    ) -> None:
        import asyncio

        respx.post(AUTH_URL).mock(
            return_value=httpx.Response(
                200, json={"access_token": "t", "token_type": "bearer", "expires_in": 300}
            )
        )
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                503,
                headers={"Retry-After": "60"},
                json={"type": "https://x/errors/backend-unavailable", "status": 503},
            )
        )

        policy = RetryPolicy(initial_backoff=0.0, max_backoff=0.0)
        async with AsyncAxonium(retry=policy, **config_kwargs) as client:
            results = await asyncio.gather(
                *(client.chat.completions.create(model="m", messages=MESSAGES) for _ in range(10)),
                return_exceptions=True,
            )

        # Every caller gets a typed error, and the shared cooldown registry survives the burst.
        assert all(isinstance(r, APIError) for r in results)
        assert client.last_rate_limit is None
