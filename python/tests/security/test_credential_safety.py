"""Credentials must not escape the SDK by any route a developer will actually hit.

Secrets rarely leak through the obvious channel. They leak through a traceback captured by an
error reporter, a ``print(config)`` left in during debugging, a stray log line, or a redirect that
carries the Authorization header to somewhere else. Each test here pins one of those routes shut.
"""

from __future__ import annotations

import logging
import pickle
import traceback

import httpx
import pytest
import respx

from axonium import AsyncAxonium, Axonium, AxoniumConfig
from axonium.errors import ConfigurationError

SECRET = "pmt_live_do_not_leak_me"
AUTH_URL = "https://auth.test.invalid/oauth2/token"
CHAT_URL = "https://gateway.test.invalid/v1/chat/completions"
CATALOG_URL = "https://gateway.test.invalid/v1/models"

TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJjIn0.super-secret-signature"


@pytest.fixture
def secret_config() -> dict[str, str]:
    return {
        "auth_base_url": "https://auth.test.invalid",
        "gateway_base_url": "https://gateway.test.invalid",
        "client_id": "client-id-is-not-secret",
        "client_secret": SECRET,
    }


@pytest.fixture
def token_route() -> object:
    return respx.post(AUTH_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": TOKEN, "token_type": "bearer", "expires_in": 300}
        )
    )


class TestSecretsInRepr:
    def test_config_repr_does_not_print_the_secret(self, secret_config: dict[str, str]) -> None:
        # A traceback prints the locals of every frame, so anything visible here reaches an error
        # reporter the moment a request fails.
        config = AxoniumConfig(**secret_config)

        assert SECRET not in repr(config)
        assert SECRET not in str(config)

    def test_client_repr_does_not_print_the_secret(self, secret_config: dict[str, str]) -> None:
        with Axonium(**secret_config) as client:
            assert SECRET not in repr(client)
            assert SECRET not in repr(client.config)

    def test_the_secret_is_still_readable_deliberately(self, secret_config: dict[str, str]) -> None:
        # Hiding it from repr must not hide it from the code that needs to send it.
        config = AxoniumConfig(**secret_config)

        assert config.client_secret.get_secret_value() == SECRET

    def test_a_serialized_config_does_not_carry_the_secret_in_the_clear(
        self, secret_config: dict[str, str]
    ) -> None:
        config = AxoniumConfig(**secret_config)

        assert SECRET not in str(config.model_dump())
        assert SECRET not in config.model_dump_json()

    def test_client_id_stays_visible(self, secret_config: dict[str, str]) -> None:
        # It identifies rather than authenticates, and is genuinely useful in a log line.
        config = AxoniumConfig(**secret_config)

        assert "client-id-is-not-secret" in repr(config)


class TestSecretsInErrors:
    def test_a_configuration_error_does_not_echo_the_secret(
        self, secret_config: dict[str, str]
    ) -> None:
        secret_config["gateway_base_url"] = "not-a-url"

        with pytest.raises(ConfigurationError) as caught:
            AxoniumConfig(**secret_config)

        assert SECRET not in str(caught.value)
        assert SECRET not in "".join(traceback.format_exception(caught.value))

    @respx.mock
    def test_a_failed_request_traceback_does_not_carry_the_secret(
        self, secret_config: dict[str, str], token_route: object
    ) -> None:
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                500, json={"type": "https://x/errors/upstream-error", "status": 500}
            )
        )

        rendered = ""
        with Axonium(**secret_config) as client:
            try:
                client.chat.completions.create(
                    model="m", messages=[{"role": "user", "content": "hi"}]
                )
            except Exception as exc:
                rendered = "".join(traceback.format_exception(exc))

        assert rendered, "the request was expected to fail"
        assert SECRET not in rendered
        assert TOKEN not in rendered

    @respx.mock
    def test_an_auth_failure_does_not_echo_the_credentials_back(
        self, secret_config: dict[str, str]
    ) -> None:
        # The auth-service is the one place the secret is actually sent, so its error path is the
        # likeliest place for it to come back out.
        respx.post(AUTH_URL).mock(
            return_value=httpx.Response(
                401,
                json={
                    "error": "invalid_client",
                    "error_description": "Invalid client credentials.",
                },
            )
        )

        with Axonium(**secret_config) as client, pytest.raises(Exception) as caught:
            client.models.mine()

        assert SECRET not in str(caught.value)
        assert SECRET not in repr(caught.value)


class TestSecretsInLogs:
    @respx.mock
    def test_nothing_at_debug_level_carries_a_credential(
        self,
        secret_config: dict[str, str],
        token_route: object,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Debug is the level people turn on when something is broken, which is exactly when the
        # logs get pasted into a ticket.
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                200, json={"choices": [{"message": {"role": "assistant", "content": "hi"}}]}
            )
        )

        with caplog.at_level(logging.DEBUG), Axonium(**secret_config) as client:
            client.chat.completions.create(
                model="m", messages=[{"role": "user", "content": "my ssn is 123-45-6789"}]
            )

        emitted = caplog.text
        assert SECRET not in emitted
        assert TOKEN not in emitted
        assert "123-45-6789" not in emitted

    @respx.mock
    def test_no_credential_survives_a_failed_call_at_debug_level(
        self,
        secret_config: dict[str, str],
        token_route: object,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                403, json={"type": "https://x/errors/forbidden", "status": 403}
            )
        )

        with (
            caplog.at_level(logging.DEBUG),
            Axonium(**secret_config) as client,
            pytest.raises(Exception),  # noqa: B017
        ):
            client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}])

        assert SECRET not in caplog.text
        assert TOKEN not in caplog.text


class TestTokenTransport:
    @respx.mock
    async def test_the_token_travels_only_in_the_authorization_header(
        self, secret_config: dict[str, str], token_route: object
    ) -> None:
        # The gateway rejects a query-parameter token outright, to keep credentials out of server,
        # proxy and browser logs. The SDK must never put it there.
        route = respx.get("https://gateway.test.invalid/v1/models/mine").mock(
            return_value=httpx.Response(200, json={"object": "list", "data": []})
        )

        async with AsyncAxonium(**secret_config) as client:
            await client.models.mine()

        request = route.calls.last.request
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert TOKEN not in str(request.url)
        assert SECRET not in str(request.url)

    @respx.mock
    def test_the_client_secret_never_reaches_the_gateway(
        self, secret_config: dict[str, str], token_route: object
    ) -> None:
        # It is for the auth-service alone; the gateway only ever sees the resulting token.
        route = respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                200, json={"choices": [{"message": {"role": "assistant", "content": "hi"}}]}
            )
        )

        with Axonium(**secret_config) as client:
            client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}])

        request = route.calls.last.request
        assert SECRET.encode() not in request.content
        assert SECRET not in str(request.headers)

    @respx.mock
    def test_the_catalog_call_sends_no_credential_at_all(
        self, secret_config: dict[str, str]
    ) -> None:
        token_route = respx.post(AUTH_URL)
        route = respx.get(CATALOG_URL).mock(
            return_value=httpx.Response(200, json={"object": "list", "data": []})
        )

        with Axonium(**secret_config) as client:
            client.models.list()

        assert "Authorization" not in route.calls.last.request.headers
        assert not token_route.called, "a public endpoint must not cost a token"


class TestTransportHardening:
    def test_redirects_are_not_followed(self, secret_config: dict[str, str]) -> None:
        # Following one would hand the Authorization header to whatever host it points at.
        with Axonium(**secret_config) as client:
            assert client._http.follow_redirects is False

    @respx.mock
    def test_a_redirect_is_surfaced_rather_than_chased(
        self, secret_config: dict[str, str], token_route: object
    ) -> None:
        evil = respx.get("https://evil.test.invalid/v1/models/mine")
        respx.get("https://gateway.test.invalid/v1/models/mine").mock(
            return_value=httpx.Response(
                302, headers={"Location": "https://evil.test.invalid/v1/models/mine"}
            )
        )

        with Axonium(**secret_config) as client, pytest.raises(Exception):  # noqa: B017
            client.models.mine()

        assert not evil.called, "the token must not have been sent to the redirect target"

    def test_tls_verification_is_on_by_default(self, secret_config: dict[str, str]) -> None:
        # There is deliberately no setting that turns verification off; a deployment with a
        # self-signed certificate points ca_bundle at it instead.
        assert "verify" not in AxoniumConfig(**secret_config).model_dump()
        assert not any(
            "insecure" in name or "verify_ssl" in name
            for name in AxoniumConfig.model_fields  # type: ignore[attr-defined]
        )

    def test_a_config_cannot_be_pickled_into_a_plaintext_secret(
        self, secret_config: dict[str, str]
    ) -> None:
        # Pickling is how a config ends up in a task queue or a cache. SecretStr survives the round
        # trip, but the pickle payload should not contain the raw value in a greppable form.
        config = AxoniumConfig(**secret_config)
        restored = pickle.loads(pickle.dumps(config))

        assert restored.client_secret.get_secret_value() == SECRET
        assert SECRET not in repr(restored)
