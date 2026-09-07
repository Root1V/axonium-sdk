from __future__ import annotations

import pytest

from axonium.config import GATEWAY_STREAMING_TIMEOUT, AxoniumConfig
from axonium.errors import ConfigurationError


class TestRequiredSettings:
    def test_constructs_from_explicit_arguments(self, config_kwargs: dict[str, str]) -> None:
        config = AxoniumConfig(**config_kwargs)

        assert config.auth_base_url == "https://auth.test.invalid"
        assert config.gateway_base_url == "https://gateway.test.invalid"
        assert config.client_id == "test-client"

    def test_missing_settings_name_themselves_and_their_env_vars(self) -> None:
        with pytest.raises(ConfigurationError) as caught:
            AxoniumConfig()

        message = str(caught.value)
        for field in ("auth_base_url", "gateway_base_url", "client_id", "client_secret"):
            assert field in message
            assert f"AXONIUM_{field.upper()}" in message

    def test_partial_configuration_reports_only_what_is_missing(
        self, config_kwargs: dict[str, str]
    ) -> None:
        del config_kwargs["client_secret"]

        with pytest.raises(ConfigurationError) as caught:
            AxoniumConfig(**config_kwargs)

        message = str(caught.value)
        assert "client_secret" in message
        assert "auth_base_url" not in message


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
