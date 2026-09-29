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
        """Every currently-deployed model.

        **Authenticated, like every other endpoint.** This one used to be public, and this SDK
        used to skip the token for it — which stopped working the day the platform closed it, with
        no announcement and with the vendored guide still saying otherwise. Listing the catalog
        returned ``MissingCredentialsError`` from a released version.

        The other three SDKs survived that change by accident: their comments claimed the endpoint
        needed no authentication while their code sent the token anyway. Only this one did what it
        said.
        """
        response = self._client._send("GET", CATALOG)
        return self._client._remember_catalog(dispatch.parse(response, ModelList))

    def mine(self) -> ModelList:
        """Only the models this token holds a ``model:<id>`` scope for.

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
