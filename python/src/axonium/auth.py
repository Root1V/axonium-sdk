"""OAuth2 ``client_credentials`` authentication.

The platform issues short-lived access tokens and has **no refresh-token grant**: a new token is
obtained by repeating the ``client_credentials`` request. Token lifetimes are per-account and an
operator can override them, so the TTL is always read from the token response's ``expires_in`` and
never assumed.

The manager refreshes *ahead* of expiry rather than waiting for a ``401``, so the normal path never
spends a failed request discovering that a token died. The reactive ``401`` path still exists as a
fallback for clock drift or a token revoked mid-flight, but it is not the primary mechanism.

Implemented as an :class:`httpx.Auth` so that token injection and the one-shot retry ride httpx's
own auth hook, which keeps them correct for every request the client makes without each call site
having to remember them.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import threading
import time
from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from axonium.config import AxoniumConfig
from axonium.errors import AuthTransportError, oauth_error_from_response
from axonium.transport.http import build_async_client, build_sync_client

__all__ = ["TokenClaims", "TokenManager", "TokenSet"]

logger = logging.getLogger("axonium.auth")

TOKEN_ENDPOINT = "/oauth2/token"


@dataclass(frozen=True)
class TokenSet:
    """A cached access token and everything needed to decide when to replace it."""

    access_token: str
    #: Monotonic deadline, so a system clock adjustment cannot make a live token look expired.
    expires_at: float
    expires_in: float
    #: The scope the server actually granted, which may be narrower than the scope requested.
    scope: tuple[str, ...] = ()

    def remaining(self, *, now: float | None = None) -> float:
        return self.expires_at - (time.monotonic() if now is None else now)

    def needs_refresh(self, *, ratio: float, min_seconds: float, now: float | None = None) -> bool:
        """Whether this token is close enough to expiry to be replaced pre-emptively.

        Two independent triggers, whichever fires first: a fraction of the lifetime having elapsed,
        and an absolute floor of remaining seconds. The floor matters for short-lived ``app``-role
        tokens, where a ratio alone leaves too little margin for a slow request.
        """
        remaining = self.remaining(now=now)
        return remaining <= self.expires_in * (1.0 - ratio) or remaining < min_seconds


@dataclass(frozen=True)
class TokenClaims:
    """Claims read out of the access token.

    Decoded **without verifying the signature**, which is fine because this is introspection for
    display and diagnostics only. The gateway is the sole authority on what a token may do — never
    make an access-control decision from these values.

    Note that the gateway does not use ``role`` for authorization either; only ``scope`` governs
    what a token can actually call.
    """

    subject: str | None = None
    client_name: str | None = None
    role: str | None = None
    scope: tuple[str, ...] = ()
    expires_at: int | None = None
    issued_at: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def decode_claims(access_token: str) -> TokenClaims:
    """Read a JWT's payload without verifying it. See :class:`TokenClaims`."""
    try:
        payload_segment = access_token.split(".")[1]
        padded = payload_segment + "=" * (-len(payload_segment) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (IndexError, ValueError, binascii.Error, UnicodeDecodeError):
        return TokenClaims()

    if not isinstance(payload, dict):
        return TokenClaims()

    scope = payload.get("scope")
    return TokenClaims(
        subject=payload.get("sub"),
        client_name=payload.get("client_name"),
        role=payload.get("role"),
        scope=tuple(scope.split()) if isinstance(scope, str) else (),
        expires_at=payload.get("exp"),
        issued_at=payload.get("iat"),
        raw=payload,
    )


class TokenManager(httpx.Auth):
    """Fetches, caches and refreshes access tokens, and attaches them to outgoing requests.

    Safe to share across threads and across tasks: refreshes are guarded so that a burst of
    concurrent requests arriving on an expired token produces one token request, not one per
    caller.
    """

    def __init__(self, config: AxoniumConfig) -> None:
        self._config = config
        self._token: TokenSet | None = None
        self._sync_lock = threading.Lock()
        self._async_lock = asyncio.Lock()
        self._sync_client: httpx.Client | None = None
        self._async_client: httpx.AsyncClient | None = None

    # -- public -------------------------------------------------------------------------

    @property
    def cached_token(self) -> TokenSet | None:
        """The current token without triggering a fetch. Mainly useful in tests and diagnostics."""
        return self._token

    def claims(self) -> TokenClaims | None:
        """Claims of the cached token, or ``None`` if no token has been obtained yet."""
        token = self._token
        return decode_claims(token.access_token) if token else None

    def close(self) -> None:
        if self._sync_client is not None:
            self._sync_client.close()
            self._sync_client = None

    async def aclose(self) -> None:
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None

    # -- httpx.Auth ---------------------------------------------------------------------

    def sync_auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        token = self._token_sync()
        _apply(request, token)

        response = yield request

        if response.status_code != httpx.codes.UNAUTHORIZED:
            return

        # Reactive fallback. The response body is not read here on purpose: reading it would
        # require `requires_response_body`, which buffers *every* response before the auth flow
        # resumes and would therefore break streaming. One extra request on a genuinely invalid
        # token is a far cheaper trade than defeating SSE, and refresh-ahead makes this path rare.
        refreshed = self._refresh_sync(stale=token)
        _apply(request, refreshed)
        yield request

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        token = await self._token_async()
        _apply(request, token)

        response = yield request

        if response.status_code != httpx.codes.UNAUTHORIZED:
            return

        refreshed = await self._refresh_async(stale=token)
        _apply(request, refreshed)
        yield request

    # -- token acquisition --------------------------------------------------------------

    def _token_sync(self) -> TokenSet:
        token = self._token
        if token is not None and not self._is_stale(token):
            return token

        with self._sync_lock:
            token = self._token
            if token is not None and not self._is_stale(token):
                return token
            return self._store(self._fetch_sync())

    async def _token_async(self) -> TokenSet:
        token = self._token
        if token is not None and not self._is_stale(token):
            return token

        async with self._async_lock:
            token = self._token
            if token is not None and not self._is_stale(token):
                return token
            return self._store(await self._fetch_async())

    def _refresh_sync(self, *, stale: TokenSet) -> TokenSet:
        with self._sync_lock:
            # Another caller may have replaced the token while this request was in flight; reusing
            # theirs avoids a redundant token request under a concurrent 401 burst.
            current = self._token
            if current is not None and current is not stale:
                return current
            return self._store(self._fetch_sync())

    async def _refresh_async(self, *, stale: TokenSet) -> TokenSet:
        async with self._async_lock:
            current = self._token
            if current is not None and current is not stale:
                return current
            return self._store(await self._fetch_async())

    def _is_stale(self, token: TokenSet) -> bool:
        return token.needs_refresh(
            ratio=self._config.refresh_ahead_ratio,
            min_seconds=self._config.refresh_ahead_min_seconds,
        )

    def _store(self, token: TokenSet) -> TokenSet:
        self._token = token
        return token

    # -- the token request ---------------------------------------------------------------

    def _request_kwargs(self) -> dict[str, Any]:
        # The endpoint is form-encoded, not JSON.
        form = {
            "grant_type": "client_credentials",
            "client_id": self._config.client_id,
            "client_secret": self._config.client_secret,
        }
        if self._config.scope:
            form["scope"] = self._config.scope
        return {"url": f"{self._config.auth_base_url}{TOKEN_ENDPOINT}", "data": form}

    def _fetch_sync(self) -> TokenSet:
        client = self._sync_client
        if client is None:
            client = self._sync_client = build_sync_client(
                self._config, read_timeout=self._config.timeouts.auth_read
            )

        issued_at = time.monotonic()
        try:
            response = client.post(**self._request_kwargs())
        except httpx.HTTPError as exc:
            raise AuthTransportError(f"Could not reach the auth-service: {exc}") from exc

        return _token_from_response(response, issued_at=issued_at)

    async def _fetch_async(self) -> TokenSet:
        client = self._async_client
        if client is None:
            client = self._async_client = build_async_client(
                self._config, read_timeout=self._config.timeouts.auth_read
            )

        issued_at = time.monotonic()
        try:
            response = await client.post(**self._request_kwargs())
        except httpx.HTTPError as exc:
            raise AuthTransportError(f"Could not reach the auth-service: {exc}") from exc

        return _token_from_response(response, issued_at=issued_at)


def _apply(request: httpx.Request, token: TokenSet) -> None:
    """Attach the token as a Bearer header.

    The header is the only accepted transport; the gateway rejects a token passed as a query
    parameter outright, specifically to keep credentials out of server, proxy and browser logs.
    """
    request.headers["Authorization"] = f"Bearer {token.access_token}"


def _effective_lifetime(response: httpx.Response, access_token: str, expires_in: float) -> float:
    """How long this token is really good for, in seconds from now.

    ``expires_in`` is the server's own answer and is normally used as-is. When both the response's
    ``Date`` header and the token's ``exp`` claim are present, the difference between them is a
    second, independent reading of the same lifetime — and a skew-free one, because both values
    come from the server's clock rather than being compared against ours.

    The shorter of the two wins. Disagreement should not happen, but treating a token as expiring
    sooner only causes an early refresh, whereas treating it as living longer causes a request to
    fail on an expired token.
    """
    server_now = response.headers.get("Date")
    exp = decode_claims(access_token).expires_at
    if server_now is None or exp is None:
        return expires_in

    try:
        issued = parsedate_to_datetime(server_now)
    except (TypeError, ValueError):
        return expires_in

    server_remaining = exp - issued.timestamp()
    if server_remaining <= 0:
        # A token already expired by the server's own reckoning; let the caller fail fast rather
        # than spend a request discovering it.
        return 0.0
    return min(expires_in, server_remaining)


def _token_from_response(response: httpx.Response, *, issued_at: float) -> TokenSet:
    """Turn a token-endpoint response into a :class:`TokenSet`.

    ``issued_at`` is a monotonic reading captured before the request was sent, so the round trip
    is charged against the token's life instead of being granted as extra margin, and so the
    deadline cannot be disturbed by a system clock adjustment. The *length* of that life comes
    from the server (see :func:`_effective_lifetime`), never from comparing our clock to theirs.
    """
    body = _json_or_none(response)

    if response.status_code != httpx.codes.OK:
        raise oauth_error_from_response(status=response.status_code, body=body)

    if body is None:
        raise AuthTransportError(
            f"The auth-service returned a non-JSON {response.status_code} response."
        )

    access_token = body.get("access_token")
    expires_in = body.get("expires_in")

    if not isinstance(access_token, str) or not access_token:
        raise AuthTransportError("The auth-service response contained no access_token.")
    if not isinstance(expires_in, (int, float)) or expires_in <= 0:
        raise AuthTransportError(
            f"The auth-service returned an unusable expires_in: {expires_in!r}."
        )

    # Always the granted scope, never the requested one: asking for a subset of an account's
    # allowed scopes is honored, so what came back is what this token can actually do.
    granted = body.get("scope")
    scope = tuple(granted.split()) if isinstance(granted, str) else ()

    lifetime = _effective_lifetime(response, access_token, float(expires_in))

    logger.debug(
        "Obtained access token",
        extra={"expires_in": lifetime, "scope": " ".join(scope)},
    )

    return TokenSet(
        access_token=access_token,
        expires_at=issued_at + lifetime,
        expires_in=lifetime,
        scope=scope,
    )


def _json_or_none(response: httpx.Response) -> dict[str, Any] | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None
