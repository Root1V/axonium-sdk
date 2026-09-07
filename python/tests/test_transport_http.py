from __future__ import annotations

import ssl

import httpx
import pytest

from axonium import __version__
from axonium.config import AxoniumConfig
from axonium.transport.http import build_async_client, build_sync_client, default_headers


@pytest.fixture
def config(config_kwargs: dict[str, str]) -> AxoniumConfig:
    return AxoniumConfig(**config_kwargs)


class TestHeaders:
    def test_identifies_the_sdk_and_its_version(self) -> None:
        assert default_headers()["User-Agent"] == f"axonium-python/{__version__}"

    @pytest.mark.parametrize("builder", [build_sync_client, build_async_client])
    def test_clients_carry_the_default_headers(
        self, config: AxoniumConfig, builder: object
    ) -> None:
        client = builder(config)  # type: ignore[operator]

        assert client.headers["User-Agent"].startswith("axonium-python/")
        assert client.headers["Accept"] == "application/json"


class TestTimeouts:
    def test_defaults_come_from_the_configuration(self, config: AxoniumConfig) -> None:
        client = build_sync_client(config)

        assert client.timeout.connect == config.timeouts.connect
        assert client.timeout.read == config.timeouts.read

    def test_read_timeout_can_be_overridden_per_client(self, config: AxoniumConfig) -> None:
        # Streaming and token requests need their own read budgets rather than the 600s default.
        client = build_sync_client(config, read_timeout=15.0)

        assert client.timeout.read == 15.0
        assert client.timeout.connect == config.timeouts.connect


class TestTls:
    def test_uses_the_default_trust_store_when_no_bundle_is_configured(
        self, config: AxoniumConfig
    ) -> None:
        # A production deployment with a CA-signed certificate needs no special configuration.
        assert config.ca_bundle is None
        build_sync_client(config).close()

    def test_accepts_a_custom_ca_bundle_for_self_signed_deployments(
        self, config_kwargs: dict[str, str], tmp_path: pytest.TempPathFactory
    ) -> None:
        bundle = ssl.get_default_verify_paths().cafile
        assert bundle is not None, "the test host has no default CA bundle to point at"
        config = AxoniumConfig(**config_kwargs, ca_bundle=bundle)

        # Construction succeeding proves the bundle was loaded; an unreadable path raises here.
        build_sync_client(config).close()

    def test_an_unreadable_ca_bundle_fails_loudly(self, config_kwargs: dict[str, str]) -> None:
        config = AxoniumConfig(**config_kwargs, ca_bundle="/nonexistent/dev-cert.pem")

        with pytest.raises((OSError, ssl.SSLError)):
            build_sync_client(config)


def test_redirects_are_not_followed(config: AxoniumConfig) -> None:
    # Following a redirect would carry the Authorization header to whatever host it points at.
    assert build_sync_client(config).follow_redirects is False
    assert build_async_client(config).follow_redirects is False


def test_event_hooks_are_passed_through_for_caller_instrumentation(
    config: AxoniumConfig,
) -> None:
    # Rather than inventing a bespoke hook system, callers use httpx's own.
    seen: list[httpx.Request] = []
    client = build_sync_client(config, event_hooks={"request": [seen.append]})

    assert client.event_hooks["request"] == [seen.append]
