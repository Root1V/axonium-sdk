"""The Axonium client.

``Axonium`` and ``AsyncAxonium`` mirror each other exactly: the same resources, the same argument
names and the same behavior, differing only in that one awaits. Anything true of one is true of the
other unless a docstring says otherwise.
"""

from __future__ import annotations

import asyncio
import logging
import time
import warnings
from collections.abc import Callable
from types import TracebackType
from typing import Any

import httpx

from axonium.auth import TokenClaims, TokenManager
from axonium.config import AxoniumConfig
from axonium.errors import (
    APIError,
    AxoniumError,
    BackendUnavailableError,
    ConfigurationError,
    ForbiddenError,
    UnusedCredentialWarning,
)
from axonium.models.catalog import ModelList
from axonium.models.common import RateLimitSnapshot, ResponseMeta
from axonium.observability.logging import request_fields
from axonium.observability.otel import record_response, span
from axonium.observability.scopes import explain_forbidden
from axonium.preflight import check_model
from axonium.providers import AsyncTokenProvider, ProvidedTokenAuth, TokenProvider
from axonium.resources.chat import AsyncChat, Chat
from axonium.resources.embeddings import AsyncEmbeddings, Embeddings
from axonium.resources.images import AsyncImages, Images
from axonium.resources.models import AsyncModels, Models
from axonium.transport import dispatch
from axonium.transport.http import build_async_client, build_sync_client
from axonium.transport.retry import CooldownRegistry, RetryPolicy

__all__ = ["AsyncAxonium", "Axonium"]

logger = logging.getLogger("axonium.client")


def _credentials_were_explicit(settings: dict[str, Any]) -> bool:
    """Whether the caller asked for credentials, as opposed to the environment carrying them.

    A deliberately built ``config=`` object counts as asking.
    """
    supplied = settings.get("config")
    if supplied is not None:
        return supplied.client_id is not None or supplied.client_secret is not None
    return bool({"client_id", "client_secret"} & settings.keys())


def _select_auth(
    config: AxoniumConfig,
    provider: TokenProvider | AsyncTokenProvider | None,
    *,
    is_async: bool,
    credentials_were_explicit: bool,
) -> TokenManager | ProvidedTokenAuth:
    """Pick the credential mode, refusing anything genuinely ambiguous.

    The two modes are permanent and mirror how the SDK is consumed: a governed host injects a
    provider and keeps its secret; an autonomous caller hands over credentials so it can work
    without that host.

    Asking for both *explicitly* is a contradiction and is refused. Credentials that merely happen
    to be in the environment are not: a governed host will often have them set for other reasons,
    and refusing to start would be fragile without being safer. The explicit provider wins — the
    same precedence every other setting follows — and the unused credentials are reported, because
    an operator who set them probably believes they are in use, and that belief is the thing worth
    correcting.
    """
    has_credentials = config.client_id is not None or config.client_secret is not None

    if provider is not None and has_credentials:
        if credentials_were_explicit:
            raise ConfigurationError(
                "Supply either a token_provider or client_id/client_secret, not both. A provider "
                "means the SDK never holds a secret; credentials mean it mints its own tokens. "
                "Which is in force must not depend on precedence."
            )
        warnings.warn(
            "A token_provider was supplied, so the client_id/client_secret found in the "
            "environment are unused and have been discarded. Unset AXONIUM_CLIENT_ID and "
            "AXONIUM_CLIENT_SECRET to keep the secret out of this process entirely.",
            UnusedCredentialWarning,
            stacklevel=3,
        )
        # Dropped rather than merely ignored, so "the SDK never holds a long-lived secret" is a
        # fact about this object and not a statement about which code path reads it.
        config.client_id = None
        config.client_secret = None

    if provider is not None:
        return (
            ProvidedTokenAuth(async_provider=provider)  # type: ignore[arg-type]
            if is_async
            else ProvidedTokenAuth(provider=provider)  # type: ignore[arg-type]
        )

    missing = [
        name
        for name, value in (
            ("client_id", config.client_id),
            ("client_secret", config.client_secret),
        )
        if value is None
    ]
    if missing:
        raise ConfigurationError(
            f"Missing {' and '.join(missing)}. Set them (or AXONIUM_{missing[0].upper()} and so "
            f"on), or pass token_provider= to let a host supply tokens instead."
        )

    return TokenManager(config)


class _BaseAxonium:
    def __init__(
        self,
        config: AxoniumConfig,
        retry: RetryPolicy | None,
        auth: TokenManager | ProvidedTokenAuth,
    ) -> None:
        self._config = config
        self._auth = auth
        self._retry = retry or RetryPolicy()
        self._cooldowns = CooldownRegistry()
        self._last_rate_limit: RateLimitSnapshot | None = None
        self._catalog: ModelList | None = None

    @property
    def config(self) -> AxoniumConfig:
        return self._config

    @property
    def retry_policy(self) -> RetryPolicy:
        return self._retry

    @property
    def last_rate_limit(self) -> RateLimitSnapshot | None:
        """The rate-limit budget reported by the most recent response that carried one.

        Reading this after each call lets a caller slow down before hitting a limit rather than
        only reacting to a ``429``. The token figures are the gateway's post-hoc accounting, so
        treat them as a strong signal rather than a guarantee.
        """
        return self._last_rate_limit

    def token_claims(self) -> TokenClaims | None:
        """Claims of the cached access token, for diagnostics.

        See :class:`~axonium.auth.TokenClaims`.
        """
        return self._auth.claims()

    def _url(self, path: str) -> str:
        return f"{self._config.gateway_base_url}{path}"

    def _cooldown_key(self, model: str | None) -> str:
        return f"{httpx.URL(self._config.gateway_base_url).host}:{model or '-'}"

    def _check_cooldown(self, key: str) -> None:
        remaining = self._cooldowns.remaining(key)
        if remaining <= 0:
            return
        raise BackendUnavailableError(
            f"The gateway reported this backend as unavailable and asked to be retried in "
            f"{remaining:.1f}s. Failing locally rather than spending a request to be told again.",
            status=503,
            type_suffix="backend-unavailable",
            retry_after=remaining,
        )

    def _record(self, response: httpx.Response) -> None:
        snapshot = RateLimitSnapshot.from_headers(response.headers)
        if not snapshot.is_empty:
            self._last_rate_limit = snapshot

    def _after_failure(
        self,
        key: str,
        error: APIError,
        attempt: int,
        *,
        model: str | None = None,
        streaming: bool = False,
    ) -> float | None:
        self._diagnose(error, model=model, streaming=streaming)
        self._cooldowns.note(key, error)

        delay = self._retry.delay_for(error, attempt=attempt)
        logger.debug(
            "Request failed" if delay is None else "Retrying after a retryable error",
            extra=request_fields(
                model=model,
                status=error.status,
                request_id=error.request_id,
                trace_id=error.trace_id,
                attempt=attempt,
                type=error.type_suffix,
                delay_s=None if delay is None else round(delay, 3),
            ),
        )
        return delay

    def _remember_catalog(self, catalog: ModelList) -> ModelList:
        self._catalog = catalog
        return catalog

    def _diagnose(self, error: APIError, *, model: str | None, streaming: bool) -> None:
        """Explain a denial in terms of the scopes this token actually holds."""
        if not isinstance(error, ForbiddenError):
            return
        token = self._auth.cached_token
        if token is not None:
            error.hint = explain_forbidden(token.scope, model=model, streaming=streaming)

    def _stream_diagnoser(self, model: str | None) -> Callable[[APIError], None]:
        def diagnose(error: APIError) -> None:
            self._diagnose(error, model=model, streaming=True)

        return diagnose

    def _observe(self, method: str, path: str, model: str | None) -> Any:
        return span(
            f"axonium {method} {path}",
            enabled=self._config.otel_enabled,
            **{
                "gen_ai.system": "prometheus-gateway",
                "gen_ai.request.model": model,
                "http.request.method": method,
                "url.path": path,
            },
        )

    def _succeeded(
        self,
        active: Any,
        response: httpx.Response,
        *,
        method: str,
        path: str,
        model: str | None,
        started: float,
    ) -> None:
        meta = ResponseMeta.from_headers(response.headers)
        record_response(active, request_id=meta.request_id, trace_id=meta.trace_id)
        logger.debug(
            "Request completed",
            extra=request_fields(
                method=method,
                path=path,
                model=model,
                status=response.status_code,
                duration_ms=(time.monotonic() - started) * 1000,
                request_id=meta.request_id,
                trace_id=meta.trace_id,
            ),
        )


class Axonium(_BaseAxonium):
    """Synchronous client for the Prometheus Gateway.

    Usable as a context manager, which closes the underlying connection pools::

        with Axonium() as client:
            completion = client.chat.completions.create(
                model="llama3-8b-q4",
                messages=[{"role": "user", "content": "Hello"}],
            )
            print(completion.content)
    """

    def __init__(
        self,
        *,
        retry: RetryPolicy | None = None,
        token_provider: TokenProvider | None = None,
        **settings: Any,
    ) -> None:
        """Accepts any :class:`~axonium.config.AxoniumConfig` field, or none at all to take every
        value from the environment. A missing required setting raises
        :class:`~axonium.errors.ConfigurationError` naming it.

        ``token_provider`` selects the governed mode: the SDK never holds a secret and asks the
        provider for a token, passing back any token the gateway rejected. Supply it *or*
        ``client_id``/``client_secret``, never both.
        """
        explicit = _credentials_were_explicit(settings)
        config = settings.pop("config", None) or AxoniumConfig(**settings)
        super().__init__(
            config,
            retry,
            _select_auth(
                config,
                token_provider,
                is_async=False,
                credentials_were_explicit=explicit,
            ),
        )
        self._http = build_sync_client(config, auth=self._auth)

        self.models = Models(self)
        self.chat = Chat(self)
        self.embeddings = Embeddings(self)
        self.images = Images(self)

    def _preflight(self, model: str, endpoint: str) -> None:
        """Catch a wrong-modality or unknown model before spending a request on it.

        A failure to load the catalog is not allowed to fail the call: this is a guard rail, and
        one that broke inference whenever the catalog endpoint was unhappy would be a worse trade
        than the mistake it prevents.
        """
        if not self._config.verify_modality:
            return
        if self._catalog is None:
            try:
                self.models.list()
            except AxoniumError as exc:
                logger.debug("Skipping preflight; catalog unavailable", extra={"error": str(exc)})
                return
        check_model(self._catalog, model, endpoint=endpoint)

    def _send(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        authenticate: bool = True,
        model: str | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        key = self._cooldown_key(model)
        self._check_cooldown(key)

        attempt = 1
        with self._observe(method, path, model) as active:
            while True:
                started = time.monotonic()
                try:
                    response = self._http.request(
                        method,
                        self._url(path),
                        json=json,
                        auth=None if not authenticate else httpx.USE_CLIENT_DEFAULT,
                        timeout=httpx.USE_CLIENT_DEFAULT if timeout is None else timeout,
                    )
                except httpx.HTTPError as exc:
                    raise dispatch.translate_transport_error(exc) from exc

                self._record(response)
                try:
                    dispatch.raise_for_status(response)
                except APIError as error:
                    delay = self._after_failure(key, error, attempt, model=model)
                    if delay is None:
                        raise
                    time.sleep(delay)
                    attempt += 1
                    continue

                self._cooldowns.clear(key)
                self._succeeded(
                    active, response, method=method, path=path, model=model, started=started
                )
                return response

    def _open_stream(
        self,
        path: str,
        *,
        json: dict[str, Any],
        model: str | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Open a streaming request.

        Not retried: the gateway never retries streams either, and a retry after partial output
        has been delivered is a fresh billable generation rather than a resumption.
        """
        self._check_cooldown(self._cooldown_key(model))
        return self._http.stream(
            "POST",
            self._url(path),
            json=json,
            timeout=self._config.timeouts.stream_read if timeout is None else timeout,
        )

    def close(self) -> None:
        self._http.close()
        self._auth.close()

    def __enter__(self) -> Axonium:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class AsyncAxonium(_BaseAxonium):
    """Asynchronous client for the Prometheus Gateway. See :class:`Axonium`."""

    def __init__(
        self,
        *,
        retry: RetryPolicy | None = None,
        token_provider: AsyncTokenProvider | None = None,
        **settings: Any,
    ) -> None:
        """See :class:`Axonium`. ``token_provider`` must be an async callable here: a blocking
        token fetch on the event loop would stall every other request in flight.
        """
        explicit = _credentials_were_explicit(settings)
        config = settings.pop("config", None) or AxoniumConfig(**settings)
        super().__init__(
            config,
            retry,
            _select_auth(
                config,
                token_provider,
                is_async=True,
                credentials_were_explicit=explicit,
            ),
        )
        self._http = build_async_client(config, auth=self._auth)

        self.models = AsyncModels(self)
        self.chat = AsyncChat(self)
        self.embeddings = AsyncEmbeddings(self)
        self.images = AsyncImages(self)

    async def _preflight(self, model: str, endpoint: str) -> None:
        """See :meth:`Axonium._preflight`."""
        if not self._config.verify_modality:
            return
        if self._catalog is None:
            try:
                await self.models.list()
            except AxoniumError as exc:
                logger.debug("Skipping preflight; catalog unavailable", extra={"error": str(exc)})
                return
        check_model(self._catalog, model, endpoint=endpoint)

    async def _send(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        authenticate: bool = True,
        model: str | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        key = self._cooldown_key(model)
        self._check_cooldown(key)

        attempt = 1
        with self._observe(method, path, model) as active:
            while True:
                started = time.monotonic()
                try:
                    response = await self._http.request(
                        method,
                        self._url(path),
                        json=json,
                        auth=None if not authenticate else httpx.USE_CLIENT_DEFAULT,
                        timeout=httpx.USE_CLIENT_DEFAULT if timeout is None else timeout,
                    )
                except httpx.HTTPError as exc:
                    raise dispatch.translate_transport_error(exc) from exc

                self._record(response)
                try:
                    dispatch.raise_for_status(response)
                except APIError as error:
                    delay = self._after_failure(key, error, attempt, model=model)
                    if delay is None:
                        raise
                    await asyncio.sleep(delay)
                    attempt += 1
                    continue

                self._cooldowns.clear(key)
                self._succeeded(
                    active, response, method=method, path=path, model=model, started=started
                )
                return response

    def _open_stream(
        self,
        path: str,
        *,
        json: dict[str, Any],
        model: str | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Open a streaming request. See :meth:`Axonium._open_stream`."""
        self._check_cooldown(self._cooldown_key(model))
        return self._http.stream(
            "POST",
            self._url(path),
            json=json,
            timeout=self._config.timeouts.stream_read if timeout is None else timeout,
        )

    async def aclose(self) -> None:
        await self._http.aclose()
        await self._auth.aclose()

    async def __aenter__(self) -> AsyncAxonium:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()
