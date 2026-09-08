"""Request bodies.

The gateway validates request bodies against an allowlist and **silently drops** anything it does
not recognize, so a caller who passes an unsupported parameter would otherwise have no way to learn
it had no effect. These models reproduce the allowlist and warn about every field they drop.

Dropping is a warning rather than an error on purpose: a gateway that starts accepting a new field
must not break an SDK that predates it.
"""

from __future__ import annotations

import warnings
from typing import Any, ClassVar, Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from axonium.errors import InvalidRequestError, UnsupportedFieldWarning

__all__ = [
    "ChatCompletionRequest",
    "ContentPart",
    "EmbeddingsRequest",
    "ImageGenerationRequest",
    "Message",
]

Role = Literal["system", "user", "assistant", "tool"]

_RequestT = TypeVar("_RequestT", bound="_AllowlistRequest")


class _AllowlistRequest(BaseModel):
    """A request body that mirrors the gateway's allowlist.

    Unknown fields are captured, reported through :class:`~axonium.errors.UnsupportedFieldWarning`,
    and then removed so they never reach the wire pretending to have had an effect.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    #: Fields the gateway is documented to reject, warned about by name.
    KNOWN_UNSUPPORTED: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="after")
    def _drop_unsupported(self) -> _AllowlistRequest:
        extras = dict(self.__pydantic_extra__ or {})
        if not extras:
            return self

        documented = sorted(name for name in extras if name in self.KNOWN_UNSUPPORTED)
        unknown = sorted(name for name in extras if name not in self.KNOWN_UNSUPPORTED)

        if documented:
            warnings.warn(
                f"The gateway does not support {', '.join(documented)}; "
                f"{'they were' if len(documented) > 1 else 'it was'} not sent.",
                UnsupportedFieldWarning,
                stacklevel=3,
            )
        if unknown:
            warnings.warn(
                f"Unrecognized request {'fields' if len(unknown) > 1 else 'field'} "
                f"{', '.join(unknown)}; not sent, as the gateway would discard "
                f"{'them' if len(unknown) > 1 else 'it'} silently.",
                UnsupportedFieldWarning,
                stacklevel=3,
            )

        self.__pydantic_extra__.clear()  # type: ignore[union-attr]
        return self

    @classmethod
    def build(cls: type[_RequestT], kwargs: dict[str, Any]) -> _RequestT:
        """Validate a request, reporting failures as an SDK error.

        Pydantic's own exception carries the useful detail, so it is preserved as the cause and
        rendered in the message; what changes is that ``except AxoniumError`` now covers request
        validation too, rather than only what comes back over the wire.
        """
        try:
            return cls(**kwargs)
        except ValidationError as exc:
            raise InvalidRequestError(f"Invalid request for {cls.__name__}: {exc}") from exc

    def to_payload(self) -> dict[str, Any]:
        """The JSON body to send, with unset optional fields omitted entirely."""
        return self.model_dump(exclude_none=True)


class ContentPart(BaseModel):
    """One part of a multi-part message, used for vision models."""

    model_config = ConfigDict(extra="allow")

    type: str
    text: str | None = None
    image_url: dict[str, Any] | None = None

    @field_validator("image_url")
    @classmethod
    def _require_data_uri(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return value

        url = value.get("url")
        if isinstance(url, str) and url.lower().startswith(("http://", "https://")):
            raise ValueError(
                "image_url.url must be a base64 data: URI. The gateway rejects remote image URLs "
                "as an SSRF mitigation, so the image has to be fetched and encoded client-side "
                "rather than passed as a link."
            )
        return value


class Message(BaseModel):
    """One chat message."""

    model_config = ConfigDict(extra="allow")

    role: Role
    #: A plain string, a list of parts for vision models, or ``None`` for an assistant message
    #: that carries only ``tool_calls``.
    content: str | list[ContentPart] | None = None
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


class ChatCompletionRequest(_AllowlistRequest):
    """Body for ``POST /v1/chat/completions``."""

    KNOWN_UNSUPPORTED: ClassVar[frozenset[str]] = frozenset(
        {
            "n",
            "presence_penalty",
            "frequency_penalty",
            "logit_bias",
            "user",
            "seed",
            "response_format",
        }
    )

    model: str
    messages: list[Message]
    stream: bool = False
    max_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    stop: str | list[str] | None = None
    #: Forwarded verbatim; the gateway does not validate tool schemas.
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None

    @field_validator("messages")
    @classmethod
    def _require_messages(cls, value: list[Message]) -> list[Message]:
        if not value:
            raise ValueError("messages must not be empty")
        return value


class EmbeddingsRequest(_AllowlistRequest):
    """Body for ``POST /v1/embeddings``."""

    KNOWN_UNSUPPORTED: ClassVar[frozenset[str]] = frozenset({"dimensions", "encoding_format"})

    model: str
    #: A single string or a list of strings.
    input: str | list[str]


class ImageGenerationRequest(_AllowlistRequest):
    """Body for ``POST /v1/images/generations``."""

    KNOWN_UNSUPPORTED: ClassVar[frozenset[str]] = frozenset(
        {"cfg_scale", "steps", "negative_prompt", "quality", "style", "response_format"}
    )

    model: str
    prompt: str
    n: int | None = Field(default=None, gt=0)
    size: str | None = None
