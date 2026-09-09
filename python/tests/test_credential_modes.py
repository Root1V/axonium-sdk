"""The two credential modes, and the concurrency rule that makes the governed one work.

Governed callers inject a token provider so the SDK never holds a long-lived secret. Autonomous
callers hand over credentials so they can work without such a host. The modes are permanent and
mutually exclusive, and which one is in force must never depend on precedence.
"""

from __future__ import annotations

import asyncio
import threading

import httpx
import pytest
import respx

from axonium import AsyncAxonium, Axonium
from axonium.errors import (
    ConfigurationError,
    ForbiddenError,
    InvalidRequestError,
    UnusedCredentialWarning,
)

AUTH_URL = "https://auth.test.invalid/oauth2/token"
MINE_URL = "https://gateway.test.invalid/v1/models/mine"
CHAT_URL = "https://gateway.test.invalid/v1/chat/completions"

EMPTY_LIST = {"object": "list", "data": []}


@pytest.fixture
def urls() -> dict[str, str]:
    return {
        "auth_base_url": "https://auth.test.invalid",
        "gateway_base_url": "https://gateway.test.invalid",
    }


class TestModeSelection:
    def test_supplying_both_modes_is_refused(self, config_kwargs: dict[str, str]) -> None:
        # Accepting both would make the active mode a matter of reading order.
        with pytest.raises(ConfigurationError, match="not both"):
            Axonium(token_provider=lambda _: "t", **config_kwargs)

    def test_supplying_neither_names_what_is_missing(self, urls: dict[str, str]) -> None:
        with pytest.raises(ConfigurationError) as caught:
            Axonium(**urls)

        message = str(caught.value)
        assert "client_id" in message
        assert "token_provider" in message

    def test_a_partial_credential_is_refused(self, urls: dict[str, str]) -> None:
        with pytest.raises(ConfigurationError, match="client_secret"):
            Axonium(client_id="only-the-id", **urls)

    def test_a_provider_alone_is_enough(self, urls: dict[str, str]) -> None:
        with Axonium(token_provider=lambda _: "t", **urls) as client:
            assert client.config.client_secret is None

    def test_credentials_alone_are_enough(self, config_kwargs: dict[str, str]) -> None:
        with Axonium(**config_kwargs) as client:
            assert client.config.client_id == "test-client"


class TestGovernedMode:
    def test_environment_credentials_are_discarded_not_merely_unused(
        self, monkeypatch: pytest.MonkeyPatch, urls: dict[str, str]
    ) -> None:
        # A governed host often has these set for other reasons. Refusing to start would be
        # fragile without being safer, so the explicit provider wins — but the secret is dropped,
        # so the guarantee is a fact about the object rather than a claim about code paths.
        monkeypatch.setenv("AXONIUM_CLIENT_ID", "from-env")
        monkeypatch.setenv("AXONIUM_CLIENT_SECRET", "secret-from-env")

        with pytest.warns(UnusedCredentialWarning, match="discarded"):
            client = Axonium(token_provider=lambda _: "t", **urls)

        with client:
            assert client.config.client_id is None
            assert client.config.client_secret is None

    def test_a_deliberately_built_config_counts_as_asking(
        self, config_kwargs: dict[str, str]
    ) -> None:
        # Constructing the config by hand and passing a provider is the same contradiction as
        # naming the credentials inline, so it gets the same refusal.
        from axonium import AxoniumConfig

        with pytest.raises(ConfigurationError, match="not both"):
            Axonium(config=AxoniumConfig(**config_kwargs), token_provider=lambda _: "t")

    def test_a_config_without_credentials_pairs_with_a_provider(self, urls: dict[str, str]) -> None:
        from axonium import AxoniumConfig

        with Axonium(config=AxoniumConfig(**urls), token_provider=lambda _: "t") as client:
            assert client.config.client_id is None

    def test_explicit_credentials_alongside_a_provider_are_still_refused(
        self, config_kwargs: dict[str, str]
    ) -> None:
        # Asking for both by name is a contradiction, not an accident of the environment.
        with pytest.raises(ConfigurationError, match="not both"):
            Axonium(token_provider=lambda _: "t", **config_kwargs)

    @respx.mock
    def test_the_provider_supplies_the_token_and_auth_is_never_called(
        self, urls: dict[str, str]
    ) -> None:
        token_endpoint = respx.post(AUTH_URL)
        route = respx.get(MINE_URL).mock(return_value=httpx.Response(200, json=EMPTY_LIST))

        with Axonium(token_provider=lambda _: "provided-token", **urls) as client:
            client.models.mine()

        assert route.calls.last.request.headers["Authorization"] == "Bearer provided-token"
        assert not token_endpoint.called, "the SDK must not mint its own token in this mode"

    @respx.mock
    def test_the_provider_is_asked_on_every_request(self, urls: dict[str, str]) -> None:
        # The provider is the authority, so it owns caching. A second cache here is how a client
        # ends up sending a token its owner already retired.
        asked: list[str | None] = []
        respx.get(MINE_URL).mock(return_value=httpx.Response(200, json=EMPTY_LIST))

        def provider(rejected: str | None) -> str:
            asked.append(rejected)
            return "t"

        with Axonium(token_provider=provider, **urls) as client:
            client.models.mine()
            client.models.mine()

        assert asked == [None, None]

    @respx.mock
    def test_a_401_hands_the_rejected_token_back(self, urls: dict[str, str]) -> None:
        # A boolean would leave the provider unable to tell which token was rejected.
        asked: list[str | None] = []
        respx.get(MINE_URL).mock(
            side_effect=[
                httpx.Response(401, json={"type": "https://x/errors/token-expired", "status": 401}),
                httpx.Response(200, json=EMPTY_LIST),
            ]
        )

        def provider(rejected: str | None) -> str:
            asked.append(rejected)
            return "second" if rejected else "first"

        with Axonium(token_provider=provider, **urls) as client:
            client.models.mine()

        assert asked == [None, "first"], "the rejected token must be identified, not just flagged"

    @respx.mock
    def test_a_second_401_is_surfaced_rather_than_retried_forever(
        self, urls: dict[str, str]
    ) -> None:
        route = respx.get(MINE_URL).mock(
            return_value=httpx.Response(
                401, json={"type": "https://x/errors/token-expired", "status": 401}
            )
        )

        with Axonium(token_provider=lambda _: "t", **urls), pytest.raises(Exception):  # noqa: B017
            Axonium(token_provider=lambda _: "t", **urls).models.mine()

        assert route.call_count == 2

    @respx.mock
    async def test_the_async_client_awaits_its_provider(self, urls: dict[str, str]) -> None:
        route = respx.get(MINE_URL).mock(return_value=httpx.Response(200, json=EMPTY_LIST))

        async def provider(rejected: str | None) -> str:
            await asyncio.sleep(0)
            return "async-token"

        async with AsyncAxonium(token_provider=provider, **urls) as client:
            await client.models.mine()

        assert route.calls.last.request.headers["Authorization"] == "Bearer async-token"

    @respx.mock
    async def test_a_sync_provider_on_the_async_client_says_so(self, urls: dict[str, str]) -> None:
        # Awaiting a plain string raises an opaque TypeError from inside httpx; the mismatch is
        # named instead.
        respx.get(MINE_URL).mock(return_value=httpx.Response(200, json=EMPTY_LIST))

        async with AsyncAxonium(token_provider=lambda _: "t", **urls) as client:  # type: ignore[arg-type]
            with pytest.raises(InvalidRequestError, match="async provider"):
                await client.models.mine()

    @respx.mock
    def test_an_async_provider_on_the_sync_client_says_so(self, urls: dict[str, str]) -> None:
        # Unawaited, the coroutine would be formatted into the Authorization header as its repr,
        # producing a 401 whose cause is invisible.
        respx.get(MINE_URL).mock(return_value=httpx.Response(200, json=EMPTY_LIST))

        async def provider(rejected: str | None) -> str:
            return "t"

        with (
            Axonium(token_provider=provider, **urls) as client,  # type: ignore[arg-type]
            pytest.raises(InvalidRequestError, match="AsyncAxonium"),
        ):
            client.models.mine()


class TestProviderConcurrency:
    """One rejected token must produce one refresh, however many callers noticed."""

    @respx.mock
    async def test_concurrent_401s_identify_the_same_rejected_token(
        self, urls: dict[str, str]
    ) -> None:
        # This is what passing the token rather than a flag buys: the provider can see that all
        # these requests concern one dead token, and mint once.
        rejected_seen: list[str | None] = []
        minted = 0
        lock = asyncio.Lock()
        current = "v1"
        requests = 6

        async def provider(rejected: str | None) -> str:
            nonlocal minted, current
            rejected_seen.append(rejected)
            if rejected is None or rejected != current:
                return current
            async with lock:
                if rejected != current:  # someone already rotated it
                    return current
                minted += 1
                current = f"v{minted + 1}"
                return current

        # Modeled on what a rotated token actually looks like: the dead one is refused, anything
        # newer is accepted. A fixed response list would be consumed in call order, letting one
        # request's retry take the rejection meant for another's first attempt.
        def gateway(request: httpx.Request) -> httpx.Response:
            presented = request.headers["Authorization"].removeprefix("Bearer ")
            if presented == "v1":
                return httpx.Response(
                    401, json={"type": "https://x/errors/token-expired", "status": 401}
                )
            return httpx.Response(200, json=EMPTY_LIST)

        respx.get(MINE_URL).mock(side_effect=gateway)

        async with AsyncAxonium(token_provider=provider, **urls) as client:
            await asyncio.gather(*(client.models.mine() for _ in range(requests)))

        assert minted == 1, "one dead token must cost one mint, not one per caller"
        assert "v1" in rejected_seen, "the rejection must have been reported at least once"
        # Callers that arrive after the rotation never see a 401 at all: they ask for a token and
        # get the fresh one. Fewer rejections than requests is the good outcome, not a gap.
        assert rejected_seen.count("v1") <= requests

    async def test_a_provider_can_deduplicate_exactly_because_it_gets_the_token(self) -> None:
        """The contract the agreement left open, stated as a test.

        With a boolean the provider cannot distinguish concurrent refreshes of one dead token from
        refreshes of different ones, and must either mint per caller or guess with a time window.
        Given the token, comparing it against what it holds answers the question exactly.
        """
        minted = 0
        current = "v1"
        lock = asyncio.Lock()
        release = asyncio.Event()

        async def provider(rejected: str | None) -> str:
            nonlocal minted, current
            await release.wait()  # hold every caller until they are genuinely concurrent
            if rejected is None or rejected != current:
                return current
            async with lock:
                if rejected != current:
                    return current
                minted += 1
                current = f"v{minted + 1}"
                return current

        callers = [asyncio.create_task(provider("v1")) for _ in range(10)]
        await asyncio.sleep(0)
        release.set()
        tokens = await asyncio.gather(*callers)

        assert minted == 1
        assert set(tokens) == {"v2"}, "every caller must end up on the one replacement"

    @respx.mock
    def test_threads_sharing_a_provider_are_safe(self, urls: dict[str, str]) -> None:
        respx.get(MINE_URL).mock(return_value=httpx.Response(200, json=EMPTY_LIST))
        calls: list[str | None] = []
        guard = threading.Lock()

        def provider(rejected: str | None) -> str:
            with guard:
                calls.append(rejected)
            return "t"

        with Axonium(token_provider=provider, **urls) as client:
            threads = [threading.Thread(target=client.models.mine) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        assert len(calls) == 8
        assert all(seen is None for seen in calls)


def test_a_manager_without_credentials_reports_it(urls: dict[str, str]) -> None:
    # Unreachable through the clients, which validate the mode first, but TokenManager is public
    # and a caller could build one from a credential-less config.
    from axonium.auth import TokenManager
    from axonium.config import AxoniumConfig
    from axonium.errors import AuthTransportError

    manager = TokenManager(AxoniumConfig(**urls))

    with pytest.raises(AuthTransportError, match="no credentials"):
        manager._fetch_sync()


class TestDiagnosticsInGovernedMode:
    @respx.mock
    def test_a_403_is_still_explained_by_scope(self, urls: dict[str, str]) -> None:
        # The SDK holds no token here, but it saw the scopes the token carried. Losing the
        # diagnosis in the governed path would remove it exactly where production 403s happen.
        import base64
        import json

        def segment(data: dict[str, object]) -> str:
            return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

        jwt = f"{segment({'alg': 'RS256'})}.{segment({'scope': 'inference:read'})}.sig"

        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                403, json={"type": "https://x/errors/forbidden", "status": 403}
            )
        )

        with (
            Axonium(token_provider=lambda _: jwt, **urls) as client,
            pytest.raises(ForbiddenError) as caught,
        ):
            client.chat.completions.create(
                model="llama3-8b-q4", messages=[{"role": "user", "content": "hi"}]
            )

        assert "model:llama3-8b-q4" in str(caught.value)

    @respx.mock
    def test_no_token_is_retained(self, urls: dict[str, str]) -> None:
        # Only the scopes it carried are kept; retaining the token would recreate the second cache
        # this mode exists to avoid.
        respx.get(MINE_URL).mock(return_value=httpx.Response(200, json=EMPTY_LIST))

        with Axonium(token_provider=lambda _: "super-secret-token", **urls) as client:
            client.models.mine()
            retained = client._auth.cached_token

        assert retained is not None
        assert "super-secret-token" not in repr(retained)
        assert not hasattr(retained, "access_token")

    @respx.mock
    def test_token_claims_are_not_reported_in_this_mode(self, urls: dict[str, str]) -> None:
        # Whatever the SDK saw a moment ago may already have been replaced by the provider.
        respx.get(MINE_URL).mock(return_value=httpx.Response(200, json=EMPTY_LIST))

        with Axonium(token_provider=lambda _: "t", **urls) as client:
            client.models.mine()

            assert client.token_claims() is None
