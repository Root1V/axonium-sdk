from __future__ import annotations

import pytest

from axonium.config import (
    DEFAULT_GATEWAY_BASE_URL,
    GATEWAY_STREAMING_TIMEOUT,
    AxoniumConfig,
)
from axonium.errors import ConfigurationError


class TestRequiredSettings:
    def test_constructs_from_explicit_arguments(self, config_kwargs: dict[str, str]) -> None:
        config = AxoniumConfig(**config_kwargs)

        assert config.gateway_base_url == "https://gateway.test.invalid"
        assert config.client_id == "test-client"

    def test_urls_default_to_the_official_platform(self) -> None:
        # Credentials are the only thing most callers should have to supply: an official SDK
        # points at the official platform, and making everyone repeat a URL is friction for
        # nothing. There is one address now rather than two -- the gateway issues tokens itself.
        config = AxoniumConfig()

        assert config.gateway_base_url == DEFAULT_GATEWAY_BASE_URL

    def test_there_is_no_second_url_to_configure(self) -> None:
        # The platform used to run a separate auth-service that every consumer also had to
        # configure, and forgetting it left a client asking the official platform for a token to
        # use somewhere else -- silently, since nothing errored. The field is gone rather than
        # defaulted, so a caller cannot get that wrong and never has to know it existed.
        assert not hasattr(AxoniumConfig(), "auth_base_url")

    def test_a_malformed_url_still_names_itself_and_its_env_var(self) -> None:
        # Defaulting removes the "you forgot one" error, not the "that is not a URL" one.
        with pytest.raises(ConfigurationError) as caught:
            AxoniumConfig(gateway_base_url="gateway.example")

        message = str(caught.value)
        assert "gateway_base_url" in message
        assert "AXONIUM_GATEWAY_BASE_URL" in message
        assert "auth_base_url" not in message

    def test_credentials_are_optional_at_this_level(self, config_kwargs: dict[str, str]) -> None:
        # Governed callers supply a token provider instead, which is not a settings value, so
        # whether a credential is required depends on how the client is built rather than on the
        # configuration alone. The client enforces that rule; see test_credential_modes.
        del config_kwargs["client_secret"]
        del config_kwargs["client_id"]

        config = AxoniumConfig(**config_kwargs)

        assert config.client_id is None
        assert config.client_secret is None

    def test_one_url_can_be_overridden_without_supplying_the_other(
        self, config_kwargs: dict[str, str]
    ) -> None:
        # A self-hosted gateway fronted by the official auth-service, or the reverse, should not
        # force a caller to restate the half they are happy with.
        del config_kwargs["gateway_base_url"]

        config = AxoniumConfig(**config_kwargs)

        assert config.gateway_base_url == DEFAULT_GATEWAY_BASE_URL


class TestResolutionOrder:
    def test_environment_supplies_values(
        self, monkeypatch: pytest.MonkeyPatch, config_kwargs: dict[str, str]
    ) -> None:
        for key, value in config_kwargs.items():
            monkeypatch.setenv(f"AXONIUM_{key.upper()}", value)

        config = AxoniumConfig()

        assert config.client_id == "test-client"

    def test_explicit_arguments_win_over_the_environment(
        self, monkeypatch: pytest.MonkeyPatch, config_kwargs: dict[str, str]
    ) -> None:
        monkeypatch.setenv("AXONIUM_CLIENT_ID", "from-environment")

        config = AxoniumConfig(**config_kwargs)

        assert config.client_id == "test-client"

    def test_an_explicit_none_does_not_shadow_the_environment(
        self, monkeypatch: pytest.MonkeyPatch, config_kwargs: dict[str, str]
    ) -> None:
        # A caller forwarding an unset optional argument must not blank out a configured value.
        monkeypatch.setenv("AXONIUM_SCOPE", "inference:read model:llama3-8b-q4")

        config = AxoniumConfig(**config_kwargs, scope=None)

        assert config.scope == "inference:read model:llama3-8b-q4"

    def test_settings_resolve_at_construction_not_at_import(
        self, monkeypatch: pytest.MonkeyPatch, config_kwargs: dict[str, str]
    ) -> None:
        # The legacy SDK captured os.getenv() in dataclass field defaults, which are evaluated
        # once at class-definition time: an application that loaded its .env after importing the
        # SDK silently got None forever. Configuration must reflect the environment as it is when
        # the client is built.
        for key, value in config_kwargs.items():
            monkeypatch.setenv(f"AXONIUM_{key.upper()}", value)
        assert AxoniumConfig().client_id == "test-client"

        monkeypatch.setenv("AXONIUM_CLIENT_ID", "changed-later")

        assert AxoniumConfig().client_id == "changed-later"


class TestValidation:
    @pytest.mark.parametrize("url", ["gateway.test.invalid", "ftp://gateway.test.invalid", ""])
    def test_rejects_a_url_without_an_http_scheme(
        self, config_kwargs: dict[str, str], url: str
    ) -> None:
        config_kwargs["gateway_base_url"] = url

        with pytest.raises(ConfigurationError, match="gateway_base_url"):
            AxoniumConfig(**config_kwargs)

    def test_strips_a_trailing_slash_so_paths_never_double_up(
        self, config_kwargs: dict[str, str]
    ) -> None:
        config_kwargs["gateway_base_url"] = "https://gateway.test.invalid/"

        assert AxoniumConfig(**config_kwargs).gateway_base_url == "https://gateway.test.invalid"

    @pytest.mark.parametrize("ratio", [0.0, 1.5, -0.1])
    def test_rejects_an_out_of_range_refresh_ratio(
        self, config_kwargs: dict[str, str], ratio: float
    ) -> None:
        with pytest.raises(ConfigurationError, match="refresh_ahead_ratio"):
            AxoniumConfig(**config_kwargs, refresh_ahead_ratio=ratio)


class TestScopes:
    def test_scope_splits_on_whitespace(self, config_kwargs: dict[str, str]) -> None:
        config = AxoniumConfig(**config_kwargs, scope="inference:read model:llama3-8b-q4")

        assert config.scopes == ("inference:read", "model:llama3-8b-q4")

    def test_absent_scope_is_empty(self, config_kwargs: dict[str, str]) -> None:
        # No scope means the token receives the account's full allowed scopes.
        assert AxoniumConfig(**config_kwargs).scopes == ()


class TestTimeouts:
    def test_defaults_follow_the_gateway_limits(self, config_kwargs: dict[str, str]) -> None:
        timeouts = AxoniumConfig(**config_kwargs).timeouts

        # A client read timeout below the gateway's own 600s would abandon a request the backend
        # is still computing, and a retry would then queue a second billable generation.
        assert timeouts.read >= 600.0
        # The SSE read timeout must exceed the gateway's 120s backend read timeout.
        assert timeouts.stream_read > GATEWAY_STREAMING_TIMEOUT

    def test_reads_overrides_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch, config_kwargs: dict[str, str]
    ) -> None:
        monkeypatch.setenv("AXONIUM_TIMEOUT_CONNECT", "2.5")

        assert AxoniumConfig(**config_kwargs).timeouts.connect == 2.5


def test_secrets_are_not_exposed_in_validation_errors(config_kwargs: dict[str, str]) -> None:
    config_kwargs["gateway_base_url"] = "not-a-url"

    with pytest.raises(ConfigurationError) as caught:
        AxoniumConfig(**config_kwargs)

    assert "test-secret" not in str(caught.value)
