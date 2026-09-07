"""The embeddings endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from axonium.models.inference import CreateEmbeddingResponse
from axonium.models.requests import EmbeddingsRequest
from axonium.transport import dispatch

if TYPE_CHECKING:
    from axonium.client import AsyncAxonium, Axonium

__all__ = ["AsyncEmbeddings", "Embeddings"]

ENDPOINT = "/v1/embeddings"


class Embeddings:
    def __init__(self, client: Axonium) -> None:
        self._client = client

    def create(self, *, timeout: float | None = None, **kwargs: Any) -> CreateEmbeddingResponse:
        """Embed one string or a list of them.

        The model must have ``embedding`` modality; calling this with a text-generation model
        returns ``400 modality-mismatch``.
        """
        request = EmbeddingsRequest(**kwargs)
        self._client._preflight(request.model, "embeddings")
        response = self._client._send(
            "POST", ENDPOINT, json=request.to_payload(), model=request.model, timeout=timeout
        )
        return dispatch.parse(response, CreateEmbeddingResponse)


class AsyncEmbeddings:
    def __init__(self, client: AsyncAxonium) -> None:
        self._client = client

    async def create(
        self, *, timeout: float | None = None, **kwargs: Any
    ) -> CreateEmbeddingResponse:
        """Embed one string or a list of them. See :meth:`Embeddings.create`."""
        request = EmbeddingsRequest(**kwargs)
        await self._client._preflight(request.model, "embeddings")
        response = await self._client._send(
            "POST", ENDPOINT, json=request.to_payload(), model=request.model, timeout=timeout
        )
        return dispatch.parse(response, CreateEmbeddingResponse)
