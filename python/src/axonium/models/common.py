"""Types shared across every response.

Response bodies for the inference endpoints are passed through from heterogeneous backends
verbatim, so models here allow unknown fields rather than discarding them: a field this SDK does
not know about is still visible to the caller instead of silently vanishing.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, PrivateAttr, model_validator

__all__ = ["APIObject", "RateLimitSnapshot", "ResponseMeta", "Usage"]


class _Passthrough(BaseModel):
    """Base for backend-shaped payloads: tolerant on input, preserving unknown fields."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class APIObject(_Passthrough):
    """A top-level response body, carrying the correlation metadata of the response it came from.

    ``meta`` is a private attribute rather than a field so that it cannot collide with a payload
    key of the same name, and so it stays out of ``model_dump()`` — it describes the HTTP exchange,
    not the resource.
    """

    _meta: ResponseMeta | None = PrivateAttr(default=None)

    @property
    def meta(self) -> ResponseMeta | None:
        """Request and trace IDs, and the rate-limit budget, as of this response."""
        return self._meta

    def _attach(self, meta: ResponseMeta) -> None:
        self._meta = meta


class Usage(_Passthrough):
    """Token accounting for a request.

    Embedding responses carry no ``completion_tokens`` because there is no generation phase.
    """

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None

    #: How many of ``prompt_tokens`` were served from cache. A **subset** of ``prompt_tokens``, not
    #: a separate bucket: the input counter includes the cached prefix. That convention was settled
    #: across the three fronts because providers report it that way, so an adapter copies instead
    #: of subtracting -- copying cannot be done wrong, and a forgotten subtraction double-counts
    #: the cache without producing any error.
    #:
    #: ``None`` when the backend did not report it. Only llama.cpp-family timings carry it today,
    #: so a non-streaming response usually leaves this unset rather than zero.
    cache_read_tokens: int | None = None

    #: True when the counts were derived from a backend ``timings`` object rather than reported
    #: directly. llama.cpp-family backends emit no usage chunk when streaming, so token counts can
    #: only be inferred from the final chunk's timings.
    estimated: bool = False

    @model_validator(mode="before")
    @classmethod
    def _lift_cached_tokens(cls, data: Any) -> Any:
        """Read the cached count out of the OpenAI-shaped ``prompt_tokens_details``.

        Non-streaming responses report it there, so ``cache_read_tokens`` is a *measured* figure on
        that path rather than one derived from ``timings``. Leaving it buried would have meant
        telling a caller nobody measured the cache on the one path where somebody did.
        """
        if not isinstance(data, dict) or data.get("cache_read_tokens") is not None:
            return data

        details = data.get("prompt_tokens_details")
        if isinstance(details, dict) and isinstance(details.get("cached_tokens"), int):
            data = {**data, "cache_read_tokens": details["cached_tokens"]}
        return data


class RateLimitSnapshot(BaseModel):
    """Rate-limit budget as of one response, parsed from the ``X-RateLimit-*`` headers.

    Present on the inference and catalog endpoints, absent on health and metrics routes — in which
    case every field is ``None``.

    The token figures reflect the gateway's post-hoc accounting rather than a pre-flight
    reservation, so a burst of large requests can still exceed the token budget between header
    updates. Treat them as a strong signal, not a guarantee against ever seeing a 429.
    """

    model_config = ConfigDict(frozen=True)

    limit_requests: int | None = None
    remaining_requests: int | None = None
    #: Unix timestamp at which the request window resets.
    reset_requests: int | None = None

    limit_tokens: int | None = None
    remaining_tokens: int | None = None
    #: Unix timestamp at which the token window resets.
    reset_tokens: int | None = None

    @property
    def is_empty(self) -> bool:
        """True when the response carried no rate-limit headers at all."""
        return all(
            value is None
            for value in (
                self.limit_requests,
                self.remaining_requests,
                self.reset_requests,
                self.limit_tokens,
                self.remaining_tokens,
                self.reset_tokens,
            )
        )

    @classmethod
    def from_headers(cls, headers: Any) -> RateLimitSnapshot:
        """Parse a snapshot from response headers, ignoring absent or malformed values."""

        def read(name: str) -> int | None:
            raw = headers.get(name)
            if raw is None:
                return None
            try:
                return int(raw)
            except (TypeError, ValueError):
                return None

        return cls(
            limit_requests=read("X-RateLimit-Limit-Requests"),
            remaining_requests=read("X-RateLimit-Remaining-Requests"),
            reset_requests=read("X-RateLimit-Reset-Requests"),
            limit_tokens=read("X-RateLimit-Limit-Tokens"),
            remaining_tokens=read("X-RateLimit-Remaining-Tokens"),
            reset_tokens=read("X-RateLimit-Reset-Tokens"),
        )


#: Keys under which the retry loop leaves its record on an ``httpx.Response``. In ``extensions``
#: because httpx provides that dict for exactly this purpose, and because an attribute set on the
#: response would be a private arrangement between two modules with nothing naming it.
WAITED_EXTENSION = "axonium_waited_s"
ATTEMPTS_EXTENSION = "axonium_attempts"


class ResponseMeta(BaseModel):
    """Correlation and budget information attached to every response.

    Carried on successes as well as failures: correlating a slow but successful call with platform
    traces matters as much as correlating a failed one.
    """

    model_config = ConfigDict(frozen=True)

    #: Server-generated per-request UUID, from the ``X-Request-ID`` header.
    request_id: str | None = None
    #: Log-correlation ID, from the ``X-Trace-ID`` header.
    trace_id: str | None = None
    rate_limit: RateLimitSnapshot | None = None

    #: Short label of the instance that served this response (``"#1"``, ``"#2"``), unique within
    #: the model. Stable for the life of an instance, but a number can be reused after the
    #: highest-numbered instance is deleted — so log it for readability and key on
    #: :attr:`instance_id`.
    instance: str | None = None
    #: Full id of the instance that served this response. This is the value to report when asking
    #: the platform team about a slow or odd response.
    instance_id: str | None = None

    #: True when this response was replayed from an ``Idempotency-Key`` rather than generated. A
    #: replay reached no model, recorded no usage, and counted against no spend cap — so a
    #: ``usage`` on a replay describes the original generation, not a second one.
    idempotent_replay: bool = False

    #: On a replay, the ``request_id`` of the generation that was actually billed.
    #:
    #: A replay carries its own ``request_id``, and that id has no usage row of its own — looking
    #: it up returns ``404``, correctly, because replaying does not reach a model and is not
    #: billed. This is the id that *does* resolve, so it is the only way from the response a
    #: caller received to the charge it corresponds to. ``None`` on anything that is not a replay.
    idempotent_replay_of: str | None = None

    #: Seconds this SDK spent deliberately asleep before the response arrived -- in practice a
    #: ``Retry-After`` it was asked to honour, which the gateway sets anywhere from 0 to 60s.
    #:
    #: Here because a wait that exists only as a log line is invisible by default: the SDK ships
    #: with a ``NullHandler`` and does not configure the host application's logging, so a caller
    #: whose root logger drops INFO sees a 36-second call and nothing explaining it. Three
    #: separate teams reported exactly that as a hang. A latency metric cannot read a log line,
    #: but it can read this.
    #:
    #: Deliberately not folded into any duration the SDK reports: sleeping is not service time.
    #: Subtract it to get what the platform actually spent. ``0.0`` when nothing was retried.
    waited_s: float = 0.0
    #: How many HTTP attempts produced this response, counting the one that succeeded. ``1`` when
    #: it worked first time, so ``attempts > 1`` is the test for "this was retried".
    attempts: int = 1

    @classmethod
    def from_headers(cls, headers: Any) -> ResponseMeta:
        rate_limit = RateLimitSnapshot.from_headers(headers)
        return cls(
            request_id=headers.get("X-Request-ID"),
            trace_id=headers.get("X-Trace-ID"),
            instance=headers.get("X-Prometheus-Instance"),
            instance_id=headers.get("X-Prometheus-Instance-Id"),
            idempotent_replay=headers.get("Idempotent-Replay", "").lower() == "true",
            idempotent_replay_of=headers.get("X-Idempotent-Replay-Of"),
            rate_limit=None if rate_limit.is_empty else rate_limit,
        )

    @classmethod
    def from_response(cls, response: Any) -> ResponseMeta:
        """Build from a response, including what the retry loop recorded on it.

        Prefer this to :meth:`from_headers`, which cannot see the wait: it is client-side state,
        not something the gateway sends. ``from_headers`` remains for a caller holding only
        headers, and reports the honest default of a first attempt that waited for nothing.
        """
        extensions = getattr(response, "extensions", None) or {}
        return cls.from_headers(response.headers).model_copy(
            update={
                "waited_s": float(extensions.get(WAITED_EXTENSION, 0.0)),
                "attempts": int(extensions.get(ATTEMPTS_EXTENSION, 1)),
            }
        )
