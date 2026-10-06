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
from axonium.models.chat import ToolCall

__all__ = [
    "ChatCompletionRequest",
    "ContentPart",
    "EmbeddingsRequest",
    "ImageGenerationRequest",
    "Message",
    "RerankRequest",
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
            # The old wording said these were withheld "as the gateway would discard them
            # silently", and that premise expired with ``PRM-127``: the gateway now names every
            # field it accepted and ignored in ``X-Prometheus-Ignored-Parameters``, so forwarding
            # one is discoverable rather than silent. Dropping them here is therefore a decision
            # this SDK is still making, not a consequence of the platform -- and it is the reason an
            # engine-specific field such as ``chat_template_kwargs`` cannot reach llama.cpp from
            # Python while it can from the TypeScript SDK. Recorded as AXO-154; the message says
            # what is true today rather than what was true when it was written.
            warnings.warn(
                f"Unrecognized request {'fields' if len(unknown) > 1 else 'field'} "
                f"{', '.join(unknown)}; sent anyway, because the gateway names what it ignored in "
                f"X-Prometheus-Ignored-Parameters and reading that header is the only answer that "
                f"cannot be stale. Check meta.ignored_parameters on the response to find out. "
                f"Engine-specific template variables go in chat_template_kwargs, not at the top "
                f"level -- reasoning_effort there does nothing.",
                UnsupportedFieldWarning,
                stacklevel=3,
            )

        # The DOCUMENTED-unsupported set is dropped: the contract names those, we know they do
        # nothing, and sending them would add noise to a header whose whole value is that it reports
        # something.
        #
        # An UNKNOWN field is forwarded, which is the opposite of what this did until 2026-10-06 and
        # is the platform's own stated intent: "they stay outside the accepted set deliberately, so
        # they keep appearing in X-Prometheus-Ignored-Parameters rather than being quietly accepted
        # and quietly dropped". Dropping it here defeats that -- the caller never sees the field in
        # `meta.ignored_parameters` and cannot tell whether it worked. The warning stays, because a
        # warning at the call site is earlier than a header on the response; what changes is that
        # the gateway now gets to have the last word, and this SDK's allowlist no longer gets to be
        # wrong in silence. It has been wrong before: it warned that `response_format` would be
        # dropped for five days after the platform started honouring it.
        for name in documented:
            self.__pydantic_extra__.pop(name, None)  # type: ignore[union-attr]
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

    #: Typed the same as what a response hands back, so a tool-use loop can feed
    #: ``completion.tool_calls`` straight into the next assistant message. Plain dicts are still
    #: accepted and validated into the model, so code written before this was typed keeps working.
    tool_calls: list[ToolCall] | None = None
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
    #: Structured outputs. Forwarded verbatim, like ``tools``: the grammar is the engine's, and
    #: validating the schema here would be a second copy of its rules that drifts.
    #:
    #: The answer arrives as a JSON **string** in the message content, not as a nested
    #: object -- parse it yourself. This SDK deliberately does not, for the same reason
    #: tool-call ``arguments`` stays a string: a generation stopped by ``max_tokens`` leaves
    #: it truncated, and a response model that raises from the inside is worse than one that
    #: hands you what arrived.
    response_format: dict[str, Any] | None = None
    tool_choice: str | dict[str, Any] | None = None
    #: Return the chosen token's own probability, under ``choices[0].logprobs.content``.
    #:
    #: The point is an agent deciding when to escalate to a person instead of acting on a guess,
    #: which is what Apeiron asked for and why ``PRM-187`` exists.
    #: Variables llama.cpp hands to the model's own chat template, forwarded as an opaque mapping.
    #:
    #: **The keys belong to each model's template, not to the gateway**, so the contents are not
    #: validated here and the useful set differs per model: ``enable_thinking`` for the Qwen3.6
    #: family, ``reasoning_effort`` for gpt-oss. A key the template does not read is ignored by the
    #: template, silently, and nothing can tell you that -- check the model card.
    #:
    #: It is how you turn a reasoning model's thinking off, and it is not a micro-optimisation:
    #: measured on the platform, the same question answered in **215 tokens and 6.91 s** without it
    #: and **16 tokens and 0.71 s** with ``{"enable_thinking": False}``.
    #:
    #: ``reasoning_effort`` goes **inside** this mapping. At the top level it does nothing at all --
    #: measured, byte-identical output with and without -- and the platform keeps it outside the
    #: accepted set on purpose so that it shows up in ``X-Prometheus-Ignored-Parameters`` instead of
    #: being quietly accepted and quietly dropped.
    chat_template_kwargs: dict[str, Any] | None = None
    logprobs: bool | None = None
    #: The ``N`` most likely alternatives at each position, 0 to 20.
    #:
    #: **Requires** ``logprobs=True``; sending it alone is a ``422`` the gateway raises before the
    #: engine sees it. Refused here instead, because the round trip buys nothing.
    top_logprobs: int | None = Field(default=None, ge=0, le=20)

    @model_validator(mode="after")
    def _top_logprobs_needs_logprobs(self) -> ChatCompletionRequest:
        # The rule is the engine's -- llama.cpp answers "top_logprobs requires logprobs to be set
        # to true" -- and the gateway enforces it before forwarding so the refusal arrives in
        # problem+json. Checking it here as well costs nothing and fails at the call site.
        if self.top_logprobs is not None and not self.logprobs:
            raise ValueError("top_logprobs requires logprobs=True")
        return self

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


class RerankRequest(_AllowlistRequest):
    """Body for ``POST /v1/rerank``."""

    #: ``logprobs`` and ``top_logprobs`` are rejected **on this endpoint**; since ``PRM-187`` they
    #: are supported on chat completions, so the warning has to name the endpoint and not the
    #: field. A chat-based reranking workaround used to need them here.
    KNOWN_UNSUPPORTED: ClassVar[frozenset[str]] = frozenset(
        {"logprobs", "top_logprobs", "return_documents", "rank_fields"}
    )

    model: str
    query: str
    #: The whole set is one request, not one per document. Scoring 50 candidates costs one unit of
    #: the rate limit rather than fifty.
    documents: list[str]
    #: Omit to get every document back.
    top_n: int | None = None
    #: Return each ``relevance_score`` as the model's raw **logit** instead of a probability.
    #:
    #: A reranker's probabilities saturate near 1.0 -- Centinela measured 0.99 for a document only
    #: loosely related to the query -- and a saturated probability cannot be calibrated while the
    #: logit behind it can.
    #:
    #: **Not every engine has it**, and that is safe: where it does not, the request still succeeds
    #: and the field comes back named in ``X-Prometheus-Ignored-Parameters``. With
    #: ``require_parameters`` it is a ``400 unknown-parameter`` whose detail names the engines that
    #: do. So send it unconditionally; being dropped is discoverable rather than silent.
    raw_scores: bool | None = None

    @field_validator("documents")
    @classmethod
    def _require_documents(cls, value: list[str]) -> list[str]:
        # The gateway answers 400 validation-error for an empty list. Refusing here saves the round
        # trip and says which field, which the envelope does too but only after the fact.
        if not value:
            raise ValueError("must contain at least one document")
        return value


class ImageGenerationRequest(_AllowlistRequest):
    """Body for ``POST /v1/images/generations``."""

    KNOWN_UNSUPPORTED: ClassVar[frozenset[str]] = frozenset(
        {"cfg_scale", "steps", "negative_prompt", "quality", "style", "response_format"}
    )

    model: str
    prompt: str
    n: int | None = Field(default=None, gt=0)
    size: str | None = None
