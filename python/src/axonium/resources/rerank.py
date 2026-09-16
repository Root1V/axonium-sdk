"""The rerank endpoint.

A reranker is a cross-encoder: it scores a query against each document and returns them ordered.
It generates nothing, so there are no completion tokens and billing is prompt-only.

Worth knowing if you are coming from a chat-based workaround: the whole document set is **one**
request rather than one per document, which against a 60 RPM budget is the difference between
scoring 50 candidates for 1 unit and for 50.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from axonium.models.inference import RerankResponse
from axonium.models.requests import RerankRequest
from axonium.transport import dispatch

if TYPE_CHECKING:
    from axonium.client import AsyncAxonium, Axonium

__all__ = ["AsyncRerank", "Rerank"]

ENDPOINT = "/v1/rerank"


class Rerank:
    """Score documents against a query."""

    def __init__(self, client: Axonium) -> None:
        self._client = client

    def create(
        self,
        *,
        timeout: float | None = None,
        instance: str | None = None,
        idempotency_key: str | None = None,
        **kwargs: Any,
    ) -> RerankResponse:
        """Score ``documents`` against ``query``, best first.

        The model must have ``rerank`` modality; a rerank model returns ``400 modality-mismatch``
        from the chat endpoint, and a chat model returns it from here.

        Each result's ``index`` points into the ``documents`` list you sent, so a reordered result
        stays attributable to its input.
        """
        request = RerankRequest.build(kwargs)
        self._client._preflight(request.model, "rerank")
        response = self._client._send(
            "POST",
            ENDPOINT,
            json=request.to_payload(),
            model=request.model,
            instance=instance,
            idempotency_key=idempotency_key,
            timeout=timeout,
        )
        return dispatch.parse(response, RerankResponse)


class AsyncRerank:
    """Score documents against a query. See :class:`Rerank`."""

    def __init__(self, client: AsyncAxonium) -> None:
        self._client = client

    async def create(
        self,
        *,
        timeout: float | None = None,
        instance: str | None = None,
        idempotency_key: str | None = None,
        **kwargs: Any,
    ) -> RerankResponse:
        request = RerankRequest.build(kwargs)
        await self._client._preflight(request.model, "rerank")
        response = await self._client._send(
            "POST",
            ENDPOINT,
            json=request.to_payload(),
            model=request.model,
            instance=instance,
            idempotency_key=idempotency_key,
            timeout=timeout,
        )
        return dispatch.parse(response, RerankResponse)
