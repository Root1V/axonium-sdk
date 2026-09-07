"""Embedding and image-generation responses."""

from __future__ import annotations

import base64
import binascii
from pathlib import Path

from pydantic import Field

from axonium.models.common import APIObject, Usage, _Passthrough

__all__ = ["CreateEmbeddingResponse", "Embedding", "GeneratedImage", "ImagesResponse"]


class Embedding(_Passthrough):
    object: str | None = None
    index: int | None = None
    embedding: list[float] = Field(default_factory=list)


class CreateEmbeddingResponse(APIObject):
    """An embeddings response.

    ``usage`` carries no ``completion_tokens``: embedding has no generation phase.
    """

    object: str | None = None
    data: list[Embedding] = Field(default_factory=list)
    model: str | None = None
    usage: Usage | None = None

    def __len__(self) -> int:
        return len(self.data)

    @property
    def vectors(self) -> list[list[float]]:
        """Just the vectors, in request order."""
        return [item.embedding for item in self.data]


class GeneratedImage(_Passthrough):
    """One generated image.

    The gateway returns image bytes inline as base64 rather than a URL, so persisting the result is
    the caller's responsibility.
    """

    b64_json: str | None = None

    def to_bytes(self) -> bytes:
        """Decode the image to raw bytes."""
        if not self.b64_json:
            raise ValueError("This image carries no b64_json payload.")
        try:
            return base64.b64decode(self.b64_json, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"The image payload is not valid base64: {exc}") from exc

    def save(self, path: str | Path) -> Path:
        """Write the decoded image to ``path`` and return it."""
        target = Path(path)
        target.write_bytes(self.to_bytes())
        return target


class ImagesResponse(APIObject):
    created: int | None = None
    data: list[GeneratedImage] = Field(default_factory=list)
    #: Format of the returned bytes, e.g. ``"png"``.
    output_format: str | None = None

    def __len__(self) -> int:
        return len(self.data)
