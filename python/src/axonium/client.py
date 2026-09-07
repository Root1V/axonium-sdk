"""The Axonium client.

``Axonium`` and ``AsyncAxonium`` mirror each other exactly: the same resources, the same argument
names and the same behavior, differing only in that one awaits. Anything true of one is true of the
other unless a docstring says otherwise.
"""

from __future__ import annotations

import asyncio
import logging
import time
from types import TracebackType
from typing import Any

import httpx

from axonium.auth import TokenClaims, TokenManager
from axonium.config import AxoniumConfig
from axonium.errors import APIError, BackendUnavailableError
from axonium.models.common import RateLimitSnapshot
from axonium.resources.chat import AsyncChat, Chat
from axonium.resources.embeddings import AsyncEmbeddings, Embeddings
from axonium.resources.images import AsyncImages, Images
from axonium.resources.models import AsyncModels, Models
from axonium.transport import dispatch
from axonium.transport.http import build_async_client, build_sync_client
from axonium.transport.retry import CooldownRegistry, RetryPolicy

__all__ = ["AsyncAxonium", "Axonium"]

logger = logging.getLogger("axonium.client")


class _BaseAxonium:
    def __init__(self, config: AxoniumConfig, retry: RetryPolicy | None) -> None:
        self._config = config
        self._auth = TokenManager(config)
        self._retry = retry or RetryPolicy()
        self._cooldowns = CooldownRegistry()
        self._last_rate_limit: RateLimitSnapshot | None = None

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

    def _after_failure(self, key: str, error: APIError, attempt: int) -> float | None:
        self._cooldowns.note(key, error)
        delay = self._retry.delay_for(error, attempt=attempt)
        if delay is not None:
            logger.debug(
                "Retrying after a retryable error",
                extra={
                    "type": error.type_suffix,
                    "status": error.status,
                    "attempt": attempt,
                    "delay_s": round(delay, 3),
                    "request_id": error.request_id,
                },
            )
        return delay


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

    def __init__(self, *, retry: RetryPolicy | None = None, **settings: Any) -> None:
        """Accepts any :class:`~axonium.config.AxoniumConfig` field, or none at all to take every
        value from the environment. A missing required setting raises
        :class:`~axonium.errors.ConfigurationError` naming it.
        """
        config = settings.pop("config", None) or AxoniumConfig(**settings)
        super().__init__(config, retry)
        self._http = build_sync_client(config, auth=self._auth)

        self.models = Models(self)
        self.chat = Chat(self)
        self.embeddings = Embeddings(self)
        self.images = Images(self)

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
        while True:
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
                delay = self._after_failure(key, error, attempt)
                if delay is None:
                    raise
                time.sleep(delay)
                attempt += 1
                continue

            self._cooldowns.clear(key)
            return response

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

    def __init__(self, *, retry: RetryPolicy | None = None, **settings: Any) -> None:
        config = settings.pop("config", None) or AxoniumConfig(**settings)
        super().__init__(config, retry)
        self._http = build_async_client(config, auth=self._auth)

        self.models = AsyncModels(self)
        self.chat = AsyncChat(self)
        self.embeddings = AsyncEmbeddings(self)
        self.images = AsyncImages(self)

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
        while True:
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
                delay = self._after_failure(key, error, attempt)
                if delay is None:
                    raise
                await asyncio.sleep(delay)
                attempt += 1
                continue

            self._cooldowns.clear(key)
            return response

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
