"""The Axonium client.

``Axonium`` and ``AsyncAxonium`` mirror each other exactly: the same resources, the same argument
names and the same behavior, differing only in that one awaits. Anything true of one is true of the
other unless a docstring says otherwise.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx

from axonium.auth import TokenManager
from axonium.config import AxoniumConfig
from axonium.models.common import RateLimitSnapshot
from axonium.resources.models import AsyncModels, Models
from axonium.transport import dispatch
from axonium.transport.http import build_async_client, build_sync_client

__all__ = ["AsyncAxonium", "Axonium"]


class _BaseAxonium:
    def __init__(self, config: AxoniumConfig) -> None:
        self._config = config
        self._auth = TokenManager(config)
        self._last_rate_limit: RateLimitSnapshot | None = None

    @property
    def config(self) -> AxoniumConfig:
        return self._config

    @property
    def last_rate_limit(self) -> RateLimitSnapshot | None:
        """The rate-limit budget reported by the most recent response that carried one.

        Reading this after each call lets a caller slow down before hitting a limit rather than
        only reacting to a ``429``. The token figures are the gateway's post-hoc accounting, so
        treat them as a strong signal rather than a guarantee.
        """
        return self._last_rate_limit

    def token_claims(self) -> Any:
        """Claims of the cached access token, for diagnostics.

        See :class:`~axonium.auth.TokenClaims`.
        """
        return self._auth.claims()

    def _url(self, path: str) -> str:
        return f"{self._config.gateway_base_url}{path}"

    def _record(self, response: httpx.Response) -> httpx.Response:
        snapshot = RateLimitSnapshot.from_headers(response.headers)
        if not snapshot.is_empty:
            self._last_rate_limit = snapshot
        return response


class Axonium(_BaseAxonium):
    """Synchronous client for the Prometheus Gateway.

    Usable as a context manager, which closes the underlying connection pools::

        with Axonium(client_id=..., client_secret=...) as client:
            print(client.models.list().ids)
    """

    def __init__(self, **settings: Any) -> None:
        """Accepts any :class:`~axonium.config.AxoniumConfig` field, or none at all to take every
        value from the environment. A missing required setting raises
        :class:`~axonium.errors.ConfigurationError` naming it.
        """
        config = settings.pop("config", None) or AxoniumConfig(**settings)
        super().__init__(config)
        self._http = build_sync_client(config, auth=self._auth)
        self.models = Models(self)

    def _get(self, path: str, *, authenticate: bool = True) -> httpx.Response:
        try:
            response = self._http.get(
                self._url(path), auth=None if not authenticate else httpx.USE_CLIENT_DEFAULT
            )
        except httpx.HTTPError as exc:
            raise dispatch.translate_transport_error(exc) from exc
        return self._record(response)

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

    def __init__(self, **settings: Any) -> None:
        config = settings.pop("config", None) or AxoniumConfig(**settings)
        super().__init__(config)
        self._http = build_async_client(config, auth=self._auth)
        self.models = AsyncModels(self)

    async def _get(self, path: str, *, authenticate: bool = True) -> httpx.Response:
        try:
            response = await self._http.get(
                self._url(path), auth=None if not authenticate else httpx.USE_CLIENT_DEFAULT
            )
        except httpx.HTTPError as exc:
            raise dispatch.translate_transport_error(exc) from exc
        return self._record(response)

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
