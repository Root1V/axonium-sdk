"""Authentication backed by a caller-supplied token provider.

The governed path runs Axonium inside a host that already owns the Prometheus credential. Handing
that host's long-lived secret to the SDK as well would put it in two places in one process for no
gain, so in that mode the SDK never sees a secret: it asks the host for a token and the host
remains the sole authority over how one is minted, cached and rotated.

Because the provider is the authority, Axonium keeps **no cache of its own** here and does no
refresh-ahead. Two caches for one token is how a client ends up sending a token its owner already
retired. What remains is the reactive path: a ``401`` means "this one is dead", and the SDK says so.

**The rejected token is passed back, not a boolean.** With a flag, a provider receiving two
concurrent refresh requests cannot tell whether they concern the same dead token or two different
ones, so it must either mint twice or guess with a time window. Given the token itself the answer
is exact: if what it holds already differs from the rejected one, it refreshed already and returns
what it has; only if they match does it mint, once, under its own lock. That is the same
double-checked pattern this SDK uses in its self-managed mode, where it is covered by a concurrency
test.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable, Generator
from dataclasses import dataclass

import httpx

from axonium.auth import decode_claims
from axonium.errors import InvalidRequestError

__all__ = ["AsyncTokenProvider", "ProvidedTokenAuth", "TokenProvider"]

logger = logging.getLogger("axonium.auth")

#: Called with the token that was just rejected, or ``None`` when none has been obtained yet.
#: Returns a token to use. The provider owns caching, refresh and rotation.
TokenProvider = Callable[[str | None], str]

#: Async counterpart. The async client requires this rather than accepting a sync callable, since a
#: blocking token fetch on the event loop would stall every other request in flight.
AsyncTokenProvider = Callable[[str | None], Awaitable[str]]


@dataclass(frozen=True)
class _AppliedScopes:
    """The scope claim of the last token used, kept so a 403 can still be diagnosed.

    Only the scope strings are retained, never the token. Holding the token would recreate the
    second cache this mode exists to avoid; holding the scopes it carried is metadata, and it is
    what turns a bare "forbidden" into the missing scope's name.
    """

    scope: tuple[str, ...] = ()


class ProvidedTokenAuth(httpx.Auth):
    """Attaches tokens obtained from a caller-supplied provider."""

    def __init__(
        self,
        provider: TokenProvider | None = None,
        async_provider: AsyncTokenProvider | None = None,
    ) -> None:
        self._provider = provider
        self._async_provider = async_provider
        self._scopes: _AppliedScopes | None = None

    @property
    def cached_token(self) -> _AppliedScopes | None:
        """Scopes of the last token applied, or ``None`` before the first request.

        Named to match the self-managed mode so scope diagnosis reads one attribute either way.
        It deliberately exposes no token.
        """
        return self._scopes

    def sync_auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        token = self._call_sync(None)
        self._apply(request, token)

        response = yield request
        if response.status_code != httpx.codes.UNAUTHORIZED:
            return

        logger.debug("Token rejected; asking the provider for a replacement")
        self._apply(request, self._call_sync(token))
        yield request

    def _call_sync(self, rejected: str | None) -> str:
        if self._provider is None:  # pragma: no cover - the client never builds this combination
            raise InvalidRequestError("This client has no synchronous token provider.")

        token = self._provider(rejected)
        if inspect.isawaitable(token):
            # Left unawaited it would be formatted into the Authorization header as a coroutine
            # repr, producing a 401 whose cause is invisible.
            token.close()  # type: ignore[attr-defined]
            raise InvalidRequestError(
                "The token provider returned an awaitable. Axonium is the synchronous client; "
                "pass an async provider to AsyncAxonium instead."
            )
        return token

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        token = await self._call_async(None)
        self._apply(request, token)

        response = yield request
        if response.status_code != httpx.codes.UNAUTHORIZED:
            return

        logger.debug("Token rejected; asking the provider for a replacement")
        self._apply(request, await self._call_async(token))
        yield request

    async def _call_async(self, rejected: str | None) -> str:
        if self._async_provider is None:  # pragma: no cover - never built this way by the client
            raise InvalidRequestError("This client has no asynchronous token provider.")

        result = self._async_provider(rejected)
        if not inspect.isawaitable(result):
            # Awaiting a plain string raises an opaque TypeError from deep inside httpx; saying so
            # here points at the actual mistake.
            raise InvalidRequestError(
                "The token provider returned a value rather than an awaitable. AsyncAxonium needs "
                "an async provider, since a blocking token fetch would stall the event loop."
            )
        return await result

    def close(self) -> None:
        """Nothing to release: the provider owns whatever it holds."""

    async def aclose(self) -> None:
        """Nothing to release: the provider owns whatever it holds."""

    def claims(self) -> None:
        """Not available in this mode.

        The SDK holds no token of its own here, and introspecting one it obtained a moment ago
        would report something the provider may already have replaced.
        """
        return None

    def _apply(self, request: httpx.Request, token: str) -> None:
        request.headers["Authorization"] = f"Bearer {token}"
        self._scopes = _AppliedScopes(scope=decode_claims(token).scope)
