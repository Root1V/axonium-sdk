"""Retry policy and the cooldown registry.

The gateway already retries against the backend up to three times with exponential backoff before
it returns anything to a client, and runs its own per-backend circuit breaker. Retrying
symmetrically on top of that would multiply load on a struggling backend, so this layer is
deliberately narrow.

**There is no idempotency-key mechanism in this API.** A retried chat, embeddings or image request
is a genuinely new generation: billable again, and not a replay of the first. So the default policy
retries only where the platform tells us *no generation happened* — a rate limit, or a circuit
breaker that fast-failed without ever calling the backend. Everything that might have reached a
model is left to the caller to decide about.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field

from axonium.errors import APIError, BackendUnavailableError, UpstreamError

__all__ = ["CooldownRegistry", "RetryPolicy"]

logger = logging.getLogger("axonium.retry")

#: Errors where the platform fast-failed without reaching a model, so retrying cannot duplicate a
#: generation or double-bill.
_NO_GENERATION_OCCURRED = frozenset(
    {
        "rate-limit-exceeded-requests",
        "backend-unavailable",
        "rate-limiting-unavailable",
        "usage-store-unavailable",
    }
)


@dataclass(frozen=True)
class RetryPolicy:
    """When to retry, and how long to wait.

    ``retry_upstream_errors`` is off by default. A ``502 upstream-error`` means the gateway's own
    three attempts already failed, so an immediate fourth is unlikely to help — and unlike the
    fast-fail errors, the request may have reached a model, making a retry a second billable
    generation. Enable it only where that trade is acceptable.
    """

    max_attempts: int = 3
    #: Base delay when the platform supplies no ``Retry-After``. Doubles per attempt.
    initial_backoff: float = 1.0
    #: The longest this SDK will block inside a single call. Also caps a server-supplied
    #: ``Retry-After``: a longer wait is surfaced to the caller instead of slept through.
    max_backoff: float = 60.0
    #: Spread retries so concurrent callers recovering from the same outage do not resynchronize.
    jitter: bool = True
    retry_upstream_errors: bool = False

    def delay_for(self, error: APIError, *, attempt: int) -> float | None:
        """Seconds to wait before attempt ``attempt + 1``, or ``None`` to give up and raise.

        ``attempt`` is 1-based and counts the request that just failed.
        """
        if attempt >= self.max_attempts or not error.retryable:
            return None

        if isinstance(error, UpstreamError):
            # Capped at a single extra attempt regardless of max_attempts.
            if not self.retry_upstream_errors or attempt > 1:
                return None
        elif error.type_suffix not in _NO_GENERATION_OCCURRED:
            return None

        if error.retry_after is not None:
            # Server-supplied and authoritative: for an open circuit breaker it is the real
            # expected recovery time, which no local heuristic can improve on. But a wait longer
            # than max_backoff is not something to sit through inside a single call — blocking a
            # caller for minutes is worse than telling them. The error carries retry_after, so
            # they can schedule the work themselves.
            wait = max(0.0, error.retry_after)
            return wait if wait <= self.max_backoff else None

        return self._backoff(attempt)

    def _backoff(self, attempt: int) -> float:
        delay: float = min(self.initial_backoff * (2 ** (attempt - 1)), self.max_backoff)
        if self.jitter:
            delay *= 0.5 + random.random() / 2
        return delay


@dataclass
class CooldownRegistry:
    """Remembers server-supplied waits so a known-open circuit is not hammered.

    When the gateway reports an open circuit breaker it also says when the backend is expected to
    recover. Ignoring that and sending the next request anyway just buys another ``503``, so the
    wait is recorded and later calls for the same backend fail locally until it elapses.

    Only ever populated from a wait the platform supplied; this never invents a cooldown of its
    own, and never guesses that a backend is unhealthy from local failure counts.
    """

    _until: dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, key: str, seconds: float) -> None:
        if seconds <= 0:
            return
        with self._lock:
            self._until[key] = time.monotonic() + seconds

    def remaining(self, key: str) -> float:
        """Seconds still to wait for ``key``, or ``0.0`` if it is clear."""
        with self._lock:
            until = self._until.get(key)
            if until is None:
                return 0.0
            remaining = until - time.monotonic()
            if remaining <= 0:
                del self._until[key]
                return 0.0
            return remaining

    def clear(self, key: str) -> None:
        with self._lock:
            self._until.pop(key, None)

    def note(self, key: str, error: APIError) -> None:
        """Record a cooldown if this error came with a server-supplied wait."""
        if isinstance(error, BackendUnavailableError) and error.retry_after:
            logger.debug("Backend cooling down", extra={"key": key, "seconds": error.retry_after})
            self.record(key, error.retry_after)
