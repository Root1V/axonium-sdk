from __future__ import annotations

import asyncio
import base64
import itertools
import json
import threading
from typing import Any

import httpx
import pytest
import respx

from axonium.auth import TokenManager, TokenSet, decode_claims
from axonium.config import AxoniumConfig
from axonium.errors import AuthTransportError, InvalidClientError, InvalidScopeError, OAuthError
from axonium.transport.http import build_async_client, build_sync_client

AUTH_URL = "https://auth.test.invalid/oauth2/token"
GATEWAY_URL = "https://gateway.test.invalid/v1/models/mine"


@pytest.fixture
def config(config_kwargs: dict[str, str]) -> AxoniumConfig:
    return AxoniumConfig(**config_kwargs)


def token_response(
    *, access_token: str = "token-1", expires_in: int = 300, scope: str = "inference:read"
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": expires_in,
            "scope": scope,
        },
    )


async def call_gateway(manager: TokenManager, config: AxoniumConfig, *, is_async: bool) -> Any:
    """Issue one authenticated gateway request through whichever client kind is under test."""
    if is_async:
        async with build_async_client(config, auth=manager) as client:
            return await client.get(GATEWAY_URL)
    with build_sync_client(config, auth=manager) as client:
        return client.get(GATEWAY_URL)


@pytest.fixture(params=[False, True], ids=["sync", "async"])
def is_async(request: pytest.FixtureRequest) -> bool:
    """Runs every behavioral test against both the sync and the async auth flow.

    The legacy SDK shipped async code paths with zero async tests; parametrizing here makes that
    impossible to repeat.
    """
    return bool(request.param)


class TestTokenAcquisition:
    @respx.mock
    async def test_fetches_a_token_and_attaches_it_as_a_bearer_header(
        self, config: AxoniumConfig, is_async: bool
    ) -> None:
        token_route = respx.post(AUTH_URL).mock(return_value=token_response())
        gateway_route = respx.get(GATEWAY_URL).mock(return_value=httpx.Response(200, json={}))

        await call_gateway(TokenManager(config), config, is_async=is_async)

        assert token_route.called
        assert gateway_route.calls.last.request.headers["Authorization"] == "Bearer token-1"

    @respx.mock
    async def test_token_request_is_form_encoded_not_json(
        self, config: AxoniumConfig, is_async: bool
    ) -> None:
        # The endpoint is declared with form parameters; a JSON body is rejected.
        token_route = respx.post(AUTH_URL).mock(return_value=token_response())
        respx.get(GATEWAY_URL).mock(return_value=httpx.Response(200, json={}))

        await call_gateway(TokenManager(config), config, is_async=is_async)

        request = token_route.calls.last.request
        assert request.headers["content-type"] == "application/x-www-form-urlencoded"
        assert b"grant_type=client_credentials" in request.content
        assert b"client_id=test-client" in request.content

    @respx.mock
    async def test_omits_scope_when_none_was_requested(
        self, config: AxoniumConfig, is_async: bool
    ) -> None:
        # An absent scope means the token receives the account's full allowed scopes.
        token_route = respx.post(AUTH_URL).mock(return_value=token_response())
        respx.get(GATEWAY_URL).mock(return_value=httpx.Response(200, json={}))

        await call_gateway(TokenManager(config), config, is_async=is_async)

        assert b"scope=" not in token_route.calls.last.request.content

    @respx.mock
    async def test_sends_the_requested_scope_when_configured(
        self, config_kwargs: dict[str, str], is_async: bool
    ) -> None:
        config = AxoniumConfig(**config_kwargs, scope="inference:read model:llama3-8b-q4")
        token_route = respx.post(AUTH_URL).mock(return_value=token_response())
        respx.get(GATEWAY_URL).mock(return_value=httpx.Response(200, json={}))

        await call_gateway(TokenManager(config), config, is_async=is_async)

        body = token_route.calls.last.request.content
        assert b"scope=inference%3Aread+model%3Allama3-8b-q4" in body

    @respx.mock
    async def test_records_the_granted_scope_not_the_requested_one(
        self, config_kwargs: dict[str, str], is_async: bool
    ) -> None:
        # The effective scope is the intersection with what the account may hold, so the response
        # is authoritative about what this token can actually do.
        config = AxoniumConfig(**config_kwargs, scope="inference:read inference:stream")
        respx.post(AUTH_URL).mock(return_value=token_response(scope="inference:read"))
        respx.get(GATEWAY_URL).mock(return_value=httpx.Response(200, json={}))
        manager = TokenManager(config)

        await call_gateway(manager, config, is_async=is_async)

        assert manager.cached_token is not None
        assert manager.cached_token.scope == ("inference:read",)

    @respx.mock
    async def test_reuses_a_live_token_across_requests(
        self, config: AxoniumConfig, is_async: bool
    ) -> None:
        token_route = respx.post(AUTH_URL).mock(return_value=token_response())
        respx.get(GATEWAY_URL).mock(return_value=httpx.Response(200, json={}))
        manager = TokenManager(config)

        await call_gateway(manager, config, is_async=is_async)
        await call_gateway(manager, config, is_async=is_async)

        assert token_route.call_count == 1

    @respx.mock
    async def test_never_puts_the_token_in_the_query_string(
        self, config: AxoniumConfig, is_async: bool
    ) -> None:
        # The gateway rejects a query-parameter token outright, to keep credentials out of logs.
        respx.post(AUTH_URL).mock(return_value=token_response())
        gateway_route = respx.get(GATEWAY_URL).mock(return_value=httpx.Response(200, json={}))

        await call_gateway(TokenManager(config), config, is_async=is_async)

        assert "token" not in str(gateway_route.calls.last.request.url.params)


class TestRefreshAhead:
    def test_a_fresh_token_is_not_stale(self) -> None:
        token = TokenSet("t", expires_at=1000.0, expires_in=300.0)

        assert not token.needs_refresh(ratio=0.8, min_seconds=30.0, now=700.0)

    def test_becomes_stale_once_the_ratio_of_its_life_has_elapsed(self) -> None:
        # A 300s token at ratio 0.8 is due for replacement with 60s left. Sampled a second either
        # side of that, since floating-point makes the exact boundary itself arbitrary.
        token = TokenSet("t", expires_at=1000.0, expires_in=300.0)

        assert not token.needs_refresh(ratio=0.8, min_seconds=0.0, now=939.0)
        assert token.needs_refresh(ratio=0.8, min_seconds=0.0, now=941.0)

    def test_the_absolute_floor_fires_before_the_ratio_for_short_lived_tokens(self) -> None:
        # A 60s app-role token at the 0.8 ratio would refresh with 12s left; the floor makes it 30s.
        token = TokenSet("t", expires_at=1000.0, expires_in=60.0)

        assert token.needs_refresh(ratio=0.8, min_seconds=30.0, now=975.0)
        assert not token.needs_refresh(ratio=0.8, min_seconds=0.0, now=975.0)

    @respx.mock
    async def test_refreshes_before_the_request_rather_than_after_a_401(
        self, config: AxoniumConfig, is_async: bool
    ) -> None:
        # A nearly-expired token must be replaced pre-emptively, so the gateway never sees it.
        token_route = respx.post(AUTH_URL).mock(return_value=token_response(access_token="token-2"))
        gateway_route = respx.get(GATEWAY_URL).mock(return_value=httpx.Response(200, json={}))
        manager = TokenManager(config)
        manager._token = TokenSet("about-to-expire", expires_at=0.0, expires_in=300.0)

        await call_gateway(manager, config, is_async=is_async)

        assert token_route.call_count == 1
        assert gateway_route.call_count == 1, "the stale token must not have been sent at all"
        assert gateway_route.calls.last.request.headers["Authorization"] == "Bearer token-2"

    @respx.mock
    async def test_expiry_is_measured_from_before_the_request_was_sent(
        self, config: AxoniumConfig, is_async: bool
    ) -> None:
        # Time spent obtaining the token counts against its life instead of being granted as
        # extra margin, which matters when the auth-service is slow.
        respx.post(AUTH_URL).mock(return_value=token_response(expires_in=300))
        respx.get(GATEWAY_URL).mock(return_value=httpx.Response(200, json={}))
        manager = TokenManager(config)

        await call_gateway(manager, config, is_async=is_async)

        assert manager.cached_token is not None
        assert manager.cached_token.remaining() <= 300.0


class TestServerAnchoredLifetime:
    """The token's lifetime comes from the server, never from comparing clocks across machines."""

    def jwt_expiring_at(self, exp: int) -> str:
        def segment(data: dict[str, Any]) -> str:
            return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

        return f"{segment({'alg': 'RS256'})}.{segment({'exp': exp})}.signature"

    def response_with(
        self, *, expires_in: int, date: str | None, exp: int | None
    ) -> httpx.Response:
        token = self.jwt_expiring_at(exp) if exp is not None else "opaque-token"
        return httpx.Response(
            200,
            headers={"Date": date} if date else {},
            json={"access_token": token, "token_type": "bearer", "expires_in": expires_in},
        )

    @respx.mock
    async def test_prefers_the_server_derived_lifetime_when_it_is_shorter(
        self, config: AxoniumConfig
    ) -> None:
        # Date and exp both come from the server's clock, so their difference is immune to any
        # skew between this machine and the auth-service.
        respx.post(AUTH_URL).mock(
            return_value=self.response_with(
                expires_in=300, date="Tue, 14 Nov 2023 22:13:20 GMT", exp=1700000200
            )
        )
        respx.get(GATEWAY_URL).mock(return_value=httpx.Response(200, json={}))
        manager = TokenManager(config)

        await call_gateway(manager, config, is_async=False)

        # That Date is epoch 1700000000 and exp is 200s past it, so 200 beats the advertised 300.
        assert manager.cached_token is not None
        assert manager.cached_token.expires_in == 200.0

    @respx.mock
    async def test_never_extends_a_lifetime_beyond_what_expires_in_advertised(
        self, config: AxoniumConfig
    ) -> None:
        # Treating a token as living longer than advertised would let a request go out on a dead
        # token; the conservative direction is always the safe one.
        respx.post(AUTH_URL).mock(
            return_value=self.response_with(
                expires_in=300, date="Tue, 14 Nov 2023 22:13:20 GMT", exp=1700009200
            )
        )
        respx.get(GATEWAY_URL).mock(return_value=httpx.Response(200, json={}))
        manager = TokenManager(config)

        await call_gateway(manager, config, is_async=False)

        assert manager.cached_token is not None
        assert manager.cached_token.expires_in == 300.0

    @respx.mock
    @pytest.mark.parametrize(
        ("date", "exp"),
        [
            (None, 1700000200),
            ("Tue, 14 Nov 2023 22:13:20 GMT", None),
            ("not a date", 1700000200),
        ],
        ids=["no-date-header", "opaque-token", "unparseable-date"],
    )
    async def test_falls_back_to_expires_in_when_the_server_reading_is_unavailable(
        self, config: AxoniumConfig, date: str | None, exp: int | None
    ) -> None:
        respx.post(AUTH_URL).mock(
            return_value=self.response_with(expires_in=300, date=date, exp=exp)
        )
        respx.get(GATEWAY_URL).mock(return_value=httpx.Response(200, json={}))
        manager = TokenManager(config)

        await call_gateway(manager, config, is_async=False)

        assert manager.cached_token is not None
        assert manager.cached_token.expires_in == 300.0

    @respx.mock
    async def test_a_token_already_expired_by_the_servers_clock_has_no_life_left(
        self, config: AxoniumConfig
    ) -> None:
        respx.post(AUTH_URL).mock(
            return_value=self.response_with(
                expires_in=300, date="Tue, 14 Nov 2023 22:13:20 GMT", exp=1699999000
            )
        )
        respx.get(GATEWAY_URL).mock(return_value=httpx.Response(200, json={}))
        manager = TokenManager(config)

        await call_gateway(manager, config, is_async=False)

        assert manager.cached_token is not None
        assert manager.cached_token.expires_in == 0.0


class TestReactiveRetry:
    @respx.mock
    async def test_refreshes_once_and_retries_once_on_401(
        self, config: AxoniumConfig, is_async: bool
    ) -> None:
        token_route = respx.post(AUTH_URL).mock(
            side_effect=[token_response(access_token="stale"), token_response(access_token="fresh")]
        )
        gateway_route = respx.get(GATEWAY_URL).mock(
            side_effect=[httpx.Response(401, json={}), httpx.Response(200, json={})]
        )

        response = await call_gateway(TokenManager(config), config, is_async=is_async)

        assert response.status_code == 200
        assert token_route.call_count == 2
        assert gateway_route.call_count == 2
        assert gateway_route.calls[0].request.headers["Authorization"] == "Bearer stale"
        assert gateway_route.calls[1].request.headers["Authorization"] == "Bearer fresh"

    @respx.mock
    async def test_a_second_401_is_surfaced_rather_than_retried_forever(
        self, config: AxoniumConfig, is_async: bool
    ) -> None:
        respx.post(AUTH_URL).mock(return_value=token_response())
        gateway_route = respx.get(GATEWAY_URL).mock(return_value=httpx.Response(401, json={}))

        response = await call_gateway(TokenManager(config), config, is_async=is_async)

        assert response.status_code == 401
        assert gateway_route.call_count == 2

    @respx.mock
    async def test_does_not_refresh_on_a_non_401_failure(
        self, config: AxoniumConfig, is_async: bool
    ) -> None:
        token_route = respx.post(AUTH_URL).mock(return_value=token_response())
        respx.get(GATEWAY_URL).mock(return_value=httpx.Response(403, json={}))

        await call_gateway(TokenManager(config), config, is_async=is_async)

        assert token_route.call_count == 1


class TestConcurrency:
    @respx.mock
    def test_concurrent_threads_trigger_a_single_token_request(self, config: AxoniumConfig) -> None:
        token_route = respx.post(AUTH_URL).mock(return_value=token_response())
        respx.get(GATEWAY_URL).mock(return_value=httpx.Response(200, json={}))
        manager = TokenManager(config)
        barrier = threading.Barrier(8)

        def worker() -> None:
            barrier.wait()
            with build_sync_client(config, auth=manager) as client:
                client.get(GATEWAY_URL)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert token_route.call_count == 1

    @respx.mock
    async def test_concurrent_tasks_trigger_a_single_token_request(
        self, config: AxoniumConfig
    ) -> None:
        token_route = respx.post(AUTH_URL).mock(return_value=token_response())
        respx.get(GATEWAY_URL).mock(return_value=httpx.Response(200, json={}))
        manager = TokenManager(config)

        async with build_async_client(config, auth=manager) as client:
            await asyncio.gather(*(client.get(GATEWAY_URL) for _ in range(8)))

        assert token_route.call_count == 1

    @respx.mock
    async def test_callers_sharing_a_stale_token_refresh_it_only_once(
        self, config: AxoniumConfig
    ) -> None:
        # Whoever loses the race adopts the token the winner just fetched. The legacy SDK
        # re-authenticated unconditionally here, producing one login per concurrent 401.
        token_route = respx.post(AUTH_URL).mock(
            side_effect=[token_response(access_token=f"token-{n}") for n in range(1, 5)]
        )
        manager = TokenManager(config)
        stale = await manager._token_async()

        refreshed = await asyncio.gather(*(manager._refresh_async(stale=stale) for _ in range(4)))

        assert token_route.call_count == 2, "one initial fetch plus one shared refresh"
        assert {token.access_token for token in refreshed} == {"token-2"}

    @respx.mock
    async def test_a_401_burst_does_not_refresh_once_per_request(
        self, config: AxoniumConfig
    ) -> None:
        # Interleaving decides how many distinct tokens end up in flight, so the exact count is
        # not deterministic; what must hold is that concurrent 401s do not each cost a token.
        requests = 6
        token_route = respx.post(AUTH_URL).mock(
            side_effect=lambda _: token_response(access_token=f"token-{token_route.call_count}")
        )
        gateway_calls = itertools.count(1)
        respx.get(GATEWAY_URL).mock(
            side_effect=lambda _: httpx.Response(
                401 if next(gateway_calls) <= requests else 200, json={}
            )
        )
        manager = TokenManager(config)

        async with build_async_client(config, auth=manager) as client:
            await asyncio.gather(*(client.get(GATEWAY_URL) for _ in range(requests)))

        assert token_route.call_count < requests


class TestTokenEndpointErrors:
    @respx.mock
    @pytest.mark.parametrize(
        ("status", "code", "expected"),
        [
            (401, "invalid_client", InvalidClientError),
            (400, "invalid_scope", InvalidScopeError),
            (401, "unrecognized_code", OAuthError),
        ],
    )
    async def test_maps_the_rfc6749_envelope_to_typed_errors(
        self,
        config: AxoniumConfig,
        is_async: bool,
        status: int,
        code: str,
        expected: type[OAuthError],
    ) -> None:
        respx.post(AUTH_URL).mock(
            return_value=httpx.Response(status, json={"error": code, "error_description": "Nope."})
        )

        with pytest.raises(expected):
            await call_gateway(TokenManager(config), config, is_async=is_async)

    @respx.mock
    async def test_unreachable_auth_service_raises_a_transport_error(
        self, config: AxoniumConfig, is_async: bool
    ) -> None:
        respx.post(AUTH_URL).mock(side_effect=httpx.ConnectError("no route to host"))

        with pytest.raises(AuthTransportError, match="Could not reach the auth-service"):
            await call_gateway(TokenManager(config), config, is_async=is_async)

    @respx.mock
    @pytest.mark.parametrize(
        "body",
        [
            {"token_type": "bearer", "expires_in": 300},
            {"access_token": "", "expires_in": 300},
            {"access_token": "t"},
            {"access_token": "t", "expires_in": 0},
            {"access_token": "t", "expires_in": "soon"},
        ],
        ids=["no-token", "empty-token", "no-expiry", "zero-expiry", "non-numeric-expiry"],
    )
    async def test_rejects_an_unusable_success_response(
        self, config: AxoniumConfig, is_async: bool, body: dict[str, Any]
    ) -> None:
        # A 200 that cannot produce a usable token must fail loudly rather than cache nonsense.
        respx.post(AUTH_URL).mock(return_value=httpx.Response(200, json=body))

        with pytest.raises(AuthTransportError):
            await call_gateway(TokenManager(config), config, is_async=is_async)

    @respx.mock
    async def test_rejects_a_non_json_response(self, config: AxoniumConfig, is_async: bool) -> None:
        # A misrouted request landing on an HTML error page must not look like an auth failure.
        respx.post(AUTH_URL).mock(return_value=httpx.Response(200, text="<html>gateway</html>"))

        with pytest.raises(AuthTransportError, match="non-JSON"):
            await call_gateway(TokenManager(config), config, is_async=is_async)


class TestClaims:
    def build_jwt(self, payload: dict[str, Any]) -> str:
        def segment(data: dict[str, Any]) -> str:
            return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

        return f"{segment({'alg': 'RS256'})}.{segment(payload)}.signature"

    def test_reads_role_and_client_name_without_verifying_the_signature(self) -> None:
        token = self.build_jwt(
            {
                "sub": "my-client-id",
                "client_name": "my-integration",
                "role": "app",
                "scope": "inference:read model:llama3-8b-q4",
                "exp": 1700000600,
                "iat": 1700000000,
            }
        )

        claims = decode_claims(token)

        assert claims.subject == "my-client-id"
        assert claims.client_name == "my-integration"
        assert claims.role == "app"
        assert claims.scope == ("inference:read", "model:llama3-8b-q4")
        assert claims.expires_at == 1700000600

    @pytest.mark.parametrize(
        "token",
        [
            "not-a-jwt",
            "a.b",
            "a.!!!not-base64!!!.c",
            # Valid base64 and valid JSON, but a list rather than a claims object.
            "a." + base64.urlsafe_b64encode(b'["not", "an", "object"]').decode().rstrip("=") + ".c",
        ],
    )
    def test_malformed_tokens_yield_empty_claims_instead_of_raising(self, token: str) -> None:
        # Introspection is a convenience; it must never be the thing that breaks a request.
        assert decode_claims(token).subject is None

    @respx.mock
    async def test_claims_are_none_before_any_token_is_obtained(
        self, config: AxoniumConfig
    ) -> None:
        assert TokenManager(config).claims() is None

    @respx.mock
    async def test_claims_expose_the_live_token(self, config: AxoniumConfig) -> None:
        respx.post(AUTH_URL).mock(
            return_value=token_response(access_token=self.build_jwt({"role": "agent"}))
        )
        respx.get(GATEWAY_URL).mock(return_value=httpx.Response(200, json={}))
        manager = TokenManager(config)

        await call_gateway(manager, config, is_async=True)
        claims = manager.claims()

        assert claims is not None
        assert claims.role == "agent"


class TestLifecycle:
    @respx.mock
    async def test_close_is_idempotent_and_safe_before_any_use(self, config: AxoniumConfig) -> None:
        manager = TokenManager(config)

        manager.close()
        await manager.aclose()

        respx.post(AUTH_URL).mock(return_value=token_response())
        respx.get(GATEWAY_URL).mock(return_value=httpx.Response(200, json={}))
        await call_gateway(manager, config, is_async=False)

        manager.close()
        manager.close()

    @respx.mock
    async def test_aclose_releases_the_async_token_client(self, config: AxoniumConfig) -> None:
        respx.post(AUTH_URL).mock(return_value=token_response())
        respx.get(GATEWAY_URL).mock(return_value=httpx.Response(200, json={}))
        manager = TokenManager(config)
        await call_gateway(manager, config, is_async=True)

        await manager.aclose()

        assert manager._async_client is None


class TestRefreshDeduplication:
    """The lock is not just for the first fetch; it also prevents redundant repeat refreshes."""

    @respx.mock
    def test_a_sync_caller_adopts_a_token_another_thread_already_fetched(
        self, config: AxoniumConfig
    ) -> None:
        token_route = respx.post(AUTH_URL).mock(
            side_effect=[token_response(access_token=f"token-{n}") for n in range(1, 4)]
        )
        manager = TokenManager(config)
        first = manager._token_sync()
        second = manager._refresh_sync(stale=first)

        third = manager._refresh_sync(stale=first)

        assert third is second
        assert token_route.call_count == 2

    @respx.mock
    async def test_a_queued_task_reuses_the_token_the_winner_fetched(
        self, config: AxoniumConfig
    ) -> None:
        # Forces the double-checked path deterministically: the second task can only reach the
        # lock while the first still holds it, so it must find a fresh token on the way out.
        release = asyncio.Event()

        async def slow_token(request: httpx.Request) -> httpx.Response:
            await release.wait()
            return token_response()

        token_route = respx.post(AUTH_URL).mock(side_effect=slow_token)
        manager = TokenManager(config)

        winner = asyncio.create_task(manager._token_async())
        await asyncio.sleep(0)
        queued = asyncio.create_task(manager._token_async())
        await asyncio.sleep(0)
        release.set()

        assert await winner is await queued
        assert token_route.call_count == 1
