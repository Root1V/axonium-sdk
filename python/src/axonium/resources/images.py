"""The image generation endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from axonium.models.inference import ImagesResponse
from axonium.models.requests import ImageGenerationRequest
from axonium.transport import dispatch

if TYPE_CHECKING:
    from axonium.client import AsyncAxonium, Axonium

__all__ = ["AsyncImages", "Images"]

ENDPOINT = "/v1/images/generations"


class Images:
    def __init__(self, client: Axonium) -> None:
        self._client = client

    def generate(self, *, timeout: float | None = None, **kwargs: Any) -> ImagesResponse:
        """Generate images.

        Results come back as base64 rather than URLs, so decoding and persisting them is the
        caller's job — see :meth:`~axonium.models.inference.GeneratedImage.save`.

        Image backends can legitimately take minutes per request, which is why the default read
        timeout is long. Shortening it and retrying risks paying for two generations at once.
        """
        request = ImageGenerationRequest(**kwargs)
        response = self._client._send(
            "POST", ENDPOINT, json=request.to_payload(), model=request.model, timeout=timeout
        )
        return dispatch.parse(response, ImagesResponse)


class AsyncImages:
    def __init__(self, client: AsyncAxonium) -> None:
        self._client = client

    async def generate(self, *, timeout: float | None = None, **kwargs: Any) -> ImagesResponse:
        """Generate images. See :meth:`Images.generate`."""
        request = ImageGenerationRequest(**kwargs)
        response = await self._client._send(
            "POST", ENDPOINT, json=request.to_payload(), model=request.model, timeout=timeout
        )
        return dispatch.parse(response, ImagesResponse)
