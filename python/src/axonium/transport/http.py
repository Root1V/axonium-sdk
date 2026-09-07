"""Construction of the underlying HTTP clients.

TLS trust is ordinary client configuration, not a platform mechanism: a deployment fronted by a
self-signed development certificate points ``ca_bundle`` at that certificate, and a production
deployment with a CA-signed certificate needs nothing. There is no mTLS anywhere in this platform;
authentication is the Bearer token alone.
"""

from __future__ import annotations

import ssl
from typing import Any

import httpx

from axonium.config import AxoniumConfig

__all__ = ["build_async_client", "build_sync_client", "default_headers", "user_agent"]


def user_agent() -> str:
    from axonium import __version__

    return f"axonium-python/{__version__}"


def default_headers() -> dict[str, str]:
    return {"Accept": "application/json", "User-Agent": user_agent()}


def _timeout(config: AxoniumConfig, *, read: float | None = None) -> httpx.Timeout:
    timeouts = config.timeouts
    return httpx.Timeout(
        connect=timeouts.connect,
        read=timeouts.read if read is None else read,
        write=timeouts.write,
        pool=timeouts.pool,
    )


def _verify(config: AxoniumConfig) -> ssl.SSLContext | bool:
    """Resolve TLS verification.

    ``True`` uses the runtime's default trust store, which is what a CA-signed production
    certificate needs. A configured bundle is loaded into an explicit context rather than passed
    to httpx as a path, since httpx deprecated the string form.
    """
    if not config.ca_bundle:
        return True
    return ssl.create_default_context(cafile=config.ca_bundle)


def build_sync_client(
    config: AxoniumConfig,
    *,
    base_url: str = "",
    read_timeout: float | None = None,
    auth: httpx.Auth | None = None,
    event_hooks: dict[str, list[Any]] | None = None,
) -> httpx.Client:
    """Build a configured sync client.

    Redirects are deliberately not followed: a redirect to another host would carry the
    ``Authorization`` header with it.
    """
    return httpx.Client(
        base_url=base_url,
        timeout=_timeout(config, read=read_timeout),
        verify=_verify(config),
        headers=default_headers(),
        auth=auth,
        follow_redirects=False,
        event_hooks=event_hooks or {},
    )


def build_async_client(
    config: AxoniumConfig,
    *,
    base_url: str = "",
    read_timeout: float | None = None,
    auth: httpx.Auth | None = None,
    event_hooks: dict[str, list[Any]] | None = None,
) -> httpx.AsyncClient:
    """Build a configured async client. See :func:`build_sync_client`."""
    return httpx.AsyncClient(
        base_url=base_url,
        timeout=_timeout(config, read=read_timeout),
        verify=_verify(config),
        headers=default_headers(),
        auth=auth,
        follow_redirects=False,
        event_hooks=event_hooks or {},
    )
