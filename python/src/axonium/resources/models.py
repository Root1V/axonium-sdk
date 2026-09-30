"""The model catalog endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from axonium.models.catalog import ModelList
from axonium.transport import dispatch

if TYPE_CHECKING:
    from axonium.client import AsyncAxonium, Axonium

__all__ = ["AsyncModels", "Models"]

CATALOG = "/v1/models"
MINE = "/v1/models/mine"


class Models:
    """Read the model catalog."""

    def __init__(self, client: Axonium) -> None:
        self._client = client

    def list(self) -> ModelList:
        """The models this token may call.

        **Not "every deployed model", and not since PRM-167.** This endpoint was the platform's
        one public route and returned the whole catalog; it now requires a token and answers
        exactly what :meth:`mine` answers — the two are aliases, and there is no reason to prefer
        either.

        **An empty list means this token holds no ``model:<id>`` grants, not that the platform
        has no models.** Those are different facts and only an operator can tell them apart: ask
        for the grant rather than concluding the deployment is empty. Model access has been
        deny-by-default all along; what changed is that discovery stopped being allow-all.

        This SDK used to skip the token here, which is what the guide described, so
        ``models.list()`` returned ``MissingCredentialsError`` from a released version the day the
        platform closed it. The other three survived by accident: identical claim in their
        comments, token sent anyway.
        """
        response = self._client._send("GET", CATALOG)
        return self._client._remember_catalog(dispatch.parse(response, ModelList))

    def mine(self) -> ModelList:
        """The models this token may call. An alias of :meth:`list` since PRM-167.

        Kept because it is documented and callers use it. It exists because :meth:`list` used to
        be the full public catalog and a token had no other way to find out what it could call.

        A token with ``inference:read`` but no model grants gets an empty list rather than an
        error, since holding an inference scope conveys no model access by itself.
        """
        return dispatch.parse(self._client._send("GET", MINE), ModelList)


class AsyncModels:
    """Read the model catalog. See :class:`Models`."""

    def __init__(self, client: AsyncAxonium) -> None:
        self._client = client

    async def list(self) -> ModelList:
        response = await self._client._send("GET", CATALOG)
        return self._client._remember_catalog(dispatch.parse(response, ModelList))

    async def mine(self) -> ModelList:
        return dispatch.parse(await self._client._send("GET", MINE), ModelList)
