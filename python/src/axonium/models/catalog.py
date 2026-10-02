"""Model catalog types."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Literal

from pydantic import Field

from axonium.models.common import APIObject, _Passthrough

__all__ = ["Modality", "Model", "ModelList"]

#: What kind of input a model consumes, which determines the endpoint it can be called on.
#: Typed loosely on purpose — a gateway that adds a modality must not break an older SDK. Every
#: value listed here was measured in a live catalog; the ``| str`` is what keeps the next one from
#: being a validation error. The last three route to ``/v1/models/{model}/predict`` and nowhere
#: else.
Modality = (
    Literal[
        "text",
        "vision",
        "embedding",
        "image",
        "rerank",
        "classification",
        "zero_shot",
        "typed_decision",
    ]
    | str
)


class Model(_Passthrough):
    """One deployed model."""

    id: str
    object: str = "model"
    owned_by: str | None = None
    context_length: int | None = None
    family: str | None = None
    quantization: str | None = None
    modality: Modality | None = None

    #: A versioned identifier for the request body's contract, e.g. ``"prometheus.chat.v1"`` or
    #: ``"hf-inference.text-classification.v1"``.
    #:
    #: **This, not :attr:`modality`, is what identifies the shape to send.** It matters most on the
    #: pass-through route, where the body belongs to the engine and two engines serving the same
    #: modality can want different ones: ``sst2-clf`` and ``von-decide`` are both classifiers and
    #: their payloads differ. Dispatch on this.
    payload_schema: str | None = None


class ModelList(APIObject):
    """A catalog response.

    Iterating yields :class:`Model` objects directly, since the ``object``/``data`` envelope is
    rarely what a caller wants to work with.
    """

    object: str = "list"
    data: list[Model] = Field(default_factory=list)

    def __iter__(self) -> Iterator[Model]:  # type: ignore[override]
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(model.id for model in self.data)

    def get(self, model_id: str) -> Model | None:
        """The model with this exact ID, or ``None``. Model IDs are case-sensitive."""
        return next((model for model in self.data if model.id == model_id), None)
