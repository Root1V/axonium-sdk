"""Per-request usage lookup.

Answers the one question a caller cannot otherwise answer about their own bill: *this request was
charged — why did it stop?* Both aggregate usage endpoints require ``admin:read``, which a normal
client neither has nor should have; this one reads exactly the caller's own row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from axonium.models.usage import RequestUsage
from axonium.transport import dispatch

if TYPE_CHECKING:
    from axonium.client import AsyncAxonium, Axonium

__all__ = ["AsyncUsage", "Usage"]


def _path(request_id: str) -> str:
    from urllib.parse import quote

    # Quoted because the id comes from a caller who may have stored or mistyped it, and an
    # unescaped separator would silently address a different route.
    return f"/v1/usage/{quote(str(request_id), safe='')}"


class Usage:
    """Read what one of your own requests was charged."""

    def __init__(self, client: Axonium) -> None:
        self._client = client

    def retrieve(self, request_id: str) -> RequestUsage:
        """The usage row for ``request_id``, which comes from any response's ``meta.request_id``.

        Raises :class:`~axonium.errors.NotFoundError` when no row exists. That covers three cases
        the gateway deliberately does not distinguish — the id is not yours, the id never existed,
        and **the id belongs to a replay**. A replay reaches no model and is not billed, so it has
        no row of its own; use ``meta.idempotent_replay_of`` to get the id of the generation that
        *was* charged, and look that up instead.
        """
        return dispatch.parse(self._client._send("GET", _path(request_id)), RequestUsage)


class AsyncUsage:
    """Read what one of your own requests was charged. See :class:`Usage`."""

    def __init__(self, client: AsyncAxonium) -> None:
        self._client = client

    async def retrieve(self, request_id: str) -> RequestUsage:
        return dispatch.parse(await self._client._send("GET", _path(request_id)), RequestUsage)
