"""Types shared across every response.

Response bodies for the inference endpoints are passed through from heterogeneous backends
verbatim, so models here allow unknown fields rather than discarding them: a field this SDK does
not know about is still visible to the caller instead of silently vanishing.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, PrivateAttr

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

    @classmethod
    def from_headers(cls, headers: Any) -> ResponseMeta:
        rate_limit = RateLimitSnapshot.from_headers(headers)
        return cls(
            request_id=headers.get("X-Request-ID"),
            trace_id=headers.get("X-Trace-ID"),
            rate_limit=None if rate_limit.is_empty else rate_limit,
        )
