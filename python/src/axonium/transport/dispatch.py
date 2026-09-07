"""Turning an HTTP response into either a typed model or a typed error.

Shared by every resource so that error mapping, correlation metadata and rate-limit accounting
happen in exactly one place rather than being re-implemented per endpoint.
"""

from __future__ import annotations

import logging
from email.utils import parsedate_to_datetime
from typing import Any, TypeVar

import httpx

from axonium.errors import APIError, TimeoutError, TransportError, error_from_response
from axonium.models.common import APIObject, RateLimitSnapshot, ResponseMeta

__all__ = ["parse", "raise_for_status", "retry_after_seconds", "translate_transport_error"]

logger = logging.getLogger("axonium.http")

ModelT = TypeVar("ModelT", bound=APIObject)


def retry_after_seconds(response: httpx.Response) -> float | None:
    """Read ``Retry-After``, which may be either delta-seconds or an HTTP date."""
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None

    try:
        return float(raw)
    except ValueError:
        pass

    try:
        target = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None

    server_now = response.headers.get("Date")
    try:
        # Both sides of this subtraction come from the server, so the wait is unaffected by any
        # difference between its clock and ours.
        reference = parsedate_to_datetime(server_now) if server_now else None
    except (TypeError, ValueError):
        reference = None

    if reference is None:
        return None
    return max(0.0, (target - reference).total_seconds())


def raise_for_status(response: httpx.Response) -> None:
    """Raise the most specific :class:`~axonium.errors.APIError` for a failed response."""
    if not response.is_error:
        return

    body: dict[str, Any] | None
    try:
        parsed = response.json()
    except ValueError:
        parsed = None
    body = parsed if isinstance(parsed, dict) else None

    rate_limit = RateLimitSnapshot.from_headers(response.headers)
    error = error_from_response(
        status=response.status_code,
        body=body,
        retry_after=retry_after_seconds(response),
        rate_limit=None if rate_limit.is_empty else rate_limit,
    )

    logger.debug(
        "Request failed",
        extra={
            "status": error.status,
            "type": error.type_suffix,
            "request_id": error.request_id,
            "trace_id": error.trace_id,
        },
    )
    raise error


def parse(response: httpx.Response, model: type[ModelT]) -> ModelT:
    """Validate a successful response body and attach its correlation metadata."""
    raise_for_status(response)

    try:
        payload = response.json()
    except ValueError as exc:
        raise APIError(
            "The gateway returned a response that is not JSON.",
            status=response.status_code,
            request_id=response.headers.get("X-Request-ID"),
            trace_id=response.headers.get("X-Trace-ID"),
        ) from exc

    parsed = model.model_validate(payload)
    parsed._attach(ResponseMeta.from_headers(response.headers))
    return parsed


def translate_transport_error(exc: httpx.HTTPError) -> TransportError:
    """Map an httpx transport failure onto this SDK's error hierarchy."""
    if isinstance(exc, httpx.TimeoutException):
        return TimeoutError(
            f"The request timed out: {exc}. The backend may still be generating; retrying would "
            "start a second billable generation rather than resuming this one."
        )
    return TransportError(str(exc))
