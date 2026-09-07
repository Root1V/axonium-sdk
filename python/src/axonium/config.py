"""Configuration for the Axonium client.

No host, port, or certificate is baked into this SDK. Base URLs, credentials and TLS trust are
deployment-specific and always supplied by the caller, either as constructor arguments or through
``AXONIUM_*`` environment variables.

Settings resolve when a client is *constructed*, not when this module is imported, so an
application that loads its ``.env`` after importing the SDK still gets the values it expects.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from axonium.errors import ConfigurationError

__all__ = ["AxoniumConfig", "Timeouts"]

ENV_PREFIX = "AXONIUM_"

#: The gateway's own backend-forwarding timeout for non-streaming requests. A client-side read
#: timeout below this is a known failure mode: the backend keeps computing after the client gives
#: up, and a retry queues a second expensive generation on top of the first.
GATEWAY_NON_STREAMING_TIMEOUT = 600.0

#: The gateway's read timeout on its own connection to the backend when streaming. The client's
#: SSE read timeout sits comfortably above this so the SDK never gives up before the gateway would.
GATEWAY_STREAMING_TIMEOUT = 120.0


class Timeouts(BaseSettings):
    """Per-phase HTTP timeouts, in seconds.

    Defaults follow the gateway's own limits. Image generation legitimately takes minutes, so the
    non-streaming read timeout is deliberately long; override it per call for endpoints where a
    faster failure is preferable.
    """

    model_config = SettingsConfigDict(env_prefix=f"{ENV_PREFIX}TIMEOUT_", extra="ignore")

    connect: float = 10.0
    read: float = GATEWAY_NON_STREAMING_TIMEOUT
    write: float = GATEWAY_NON_STREAMING_TIMEOUT
    pool: float = 10.0
    #: Read timeout for SSE responses, kept above the gateway's own 120s backend read timeout.
    stream_read: float = 180.0


class AxoniumConfig(BaseSettings):
    """Resolved client configuration.

    Values are taken from explicit arguments first and from the environment otherwise. A missing
    required setting raises :class:`~axonium.errors.ConfigurationError` naming both the field and
    the environment variable that can supply it.
    """

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        extra="ignore",
        # Secrets must not leak into logs or tracebacks via repr().
        hide_input_in_errors=True,
    )

    #: Base URL of the auth-service that issues OAuth2 tokens.
    auth_base_url: str
    #: Base URL of the gateway serving the ``/v1/`` inference API.
    gateway_base_url: str

    client_id: str
    client_secret: str

    #: Optional space-separated scope request. When omitted the token receives the account's full
    #: allowed scopes; when supplied the effective scope is the intersection with what the account
    #: is allowed, and requesting a scope the account lacks is an error rather than a downgrade.
    #: Always read the granted scope back from the token response.
    scope: str | None = None

    #: Path to a CA bundle, for deployments fronted by a self-signed certificate. Production
    #: deployments with a CA-signed certificate need nothing here.
    ca_bundle: str | None = None

    #: Refresh the token once this fraction of its lifetime has elapsed.
    refresh_ahead_ratio: float = Field(default=0.8, gt=0.0, le=1.0)
    #: ...or once fewer than this many seconds remain, whichever comes first. Short-lived
    #: ``app``-role tokens can default to a 300s TTL, where a ratio alone cuts it too fine.
    refresh_ahead_min_seconds: float = Field(default=30.0, ge=0.0)

    timeouts: Timeouts = Field(default_factory=Timeouts)

    @field_validator("auth_base_url", "gateway_base_url")
    @classmethod
    def _require_absolute_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("must start with http:// or https://")
        return value.rstrip("/")

    @property
    def scopes(self) -> tuple[str, ...]:
        """The requested scope as a tuple, empty when no scope was requested."""
        return tuple(self.scope.split()) if self.scope else ()

    def __init__(self, **values: Any) -> None:
        # pydantic-settings treats an explicitly passed None as a value rather than as "absent",
        # which would shadow a perfectly good environment variable. Drop them so the documented
        # precedence (argument, then environment, then error) holds.
        supplied = {key: value for key, value in values.items() if value is not None}
        try:
            super().__init__(**supplied)
        except ValidationError as exc:
            raise ConfigurationError(_describe(exc)) from exc


def _describe(exc: ValidationError) -> str:
    """Turn a pydantic validation failure into a message naming the environment variables."""
    problems = []
    for error in exc.errors():
        field = ".".join(str(part) for part in error["loc"]) or "(root)"
        env_var = f"{ENV_PREFIX}{field.upper()}"
        if error["type"] == "missing":
            problems.append(f"{field} is required (set it or export {env_var})")
        else:
            problems.append(f"{field}: {error['msg']} (from argument or {env_var})")

    joined = "; ".join(problems)
    return f"Invalid Axonium configuration: {joined}"
