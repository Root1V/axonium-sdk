from __future__ import annotations

import time
from typing import Any

import pytest

from axonium.errors import error_from_response
from axonium.transport.retry import CooldownRegistry, RetryPolicy


def error(suffix: str, status: int, **extra: Any) -> Any:
    return error_from_response(
        status=status,
        body={"type": f"https://prometheus.internal/errors/{suffix}", "status": status},
        **extra,
    )


@pytest.fixture
def policy() -> RetryPolicy:
    return RetryPolicy(jitter=False)


class TestWhatIsRetried:
    @pytest.mark.parametrize(
        ("suffix", "status"),
        [
            ("rate-limit-exceeded-requests", 429),
            ("backend-unavailable", 503),
            ("rate-limiting-unavailable", 503),
            ("usage-store-unavailable", 503),
        ],
    )
    def test_retries_errors_where_no_generation_happened(
        self, policy: RetryPolicy, suffix: str, status: int
    ) -> None:
        # The platform fast-failed without reaching a model, so a retry cannot double-bill.
        assert policy.delay_for(error(suffix, status), attempt=1) is not None

    @pytest.mark.parametrize(
        ("suffix", "status"),
        [
            ("unknown-model", 400),
            ("modality-mismatch", 400),
            ("context-exceeded", 400),
            ("invalid-token", 401),
            ("token-revoked", 401),
            ("spend-cap-exceeded", 402),
            ("forbidden", 403),
            ("model-not-loaded", 503),
        ],
    )
    def test_never_retries_what_retrying_cannot_fix(
        self, policy: RetryPolicy, suffix: str, status: int
    ) -> None:
        assert policy.delay_for(error(suffix, status), attempt=1) is None

    def test_upstream_errors_are_not_retried_by_default(self, policy: RetryPolicy) -> None:
        # The gateway already made three attempts, and unlike the fast-fail errors this request
        # may have reached a model — so a retry is a second billable generation.
        assert policy.delay_for(error("upstream-error", 502), attempt=1) is None

    def test_upstream_errors_can_be_retried_once_when_opted_in(self) -> None:
        opted_in = RetryPolicy(jitter=False, retry_upstream_errors=True, max_attempts=5)

        assert opted_in.delay_for(error("upstream-error", 502), attempt=1) is not None
        # Capped at a single extra attempt even though max_attempts allows more.
        assert opted_in.delay_for(error("upstream-error", 502), attempt=2) is None

    def test_stops_once_the_attempt_budget_is_spent(self, policy: RetryPolicy) -> None:
        assert policy.delay_for(error("rate-limit-exceeded-requests", 429), attempt=2) is not None
        assert policy.delay_for(error("rate-limit-exceeded-requests", 429), attempt=3) is None


class TestHowLongItWaits:
    def test_a_server_supplied_wait_is_used_verbatim(self, policy: RetryPolicy) -> None:
        # For an open circuit breaker this is the real expected recovery time, which no local
        # heuristic can improve on.
        delay = policy.delay_for(error("backend-unavailable", 503, retry_after=7.5), attempt=1)

        assert delay == 7.5

    def test_backoff_doubles_when_no_wait_was_supplied(self, policy: RetryPolicy) -> None:
        # A genuine connection failure carries no hint, so the SDK picks a conservative default.
        first = policy.delay_for(error("backend-unavailable", 503), attempt=1)
        second = policy.delay_for(error("backend-unavailable", 503), attempt=2)

        assert first == 1.0
        assert second == 2.0

    def test_backoff_is_capped(self) -> None:
        capped = RetryPolicy(jitter=False, max_attempts=20, max_backoff=5.0)

        assert capped.delay_for(error("backend-unavailable", 503), attempt=10) == 5.0

    def test_jitter_spreads_retries_without_exceeding_the_backoff(self) -> None:
        # Concurrent clients recovering from one outage must not resynchronize onto the same
        # instant and stampede the backend.
        jittered = RetryPolicy(jitter=True)
        err = error("backend-unavailable", 503)
        delays = {jittered.delay_for(err, attempt=1) for _ in range(20)}

        assert len(delays) > 1
        assert all(0.5 <= delay <= 1.0 for delay in delays if delay is not None)

    def test_a_negative_server_wait_is_clamped(self, policy: RetryPolicy) -> None:
        assert policy.delay_for(error("backend-unavailable", 503, retry_after=-5), attempt=1) == 0.0

    def test_a_wait_longer_than_max_backoff_is_surfaced_instead_of_slept_through(self) -> None:
        # Sitting inside a single call for however long the platform asks would block the caller
        # for minutes or hours. The error still carries retry_after, so they can schedule it.
        policy = RetryPolicy(jitter=False, max_backoff=60.0)

        at_cap = error("backend-unavailable", 503, retry_after=60)
        over_cap = error("backend-unavailable", 503, retry_after=61)

        assert policy.delay_for(at_cap, attempt=1) == 60.0
        assert policy.delay_for(over_cap, attempt=1) is None


class TestCooldownRegistry:
    def test_records_and_expires_a_wait(self) -> None:
        registry = CooldownRegistry()
        registry.record("host:model", 0.05)

        assert registry.remaining("host:model") > 0
        time.sleep(0.06)
        assert registry.remaining("host:model") == 0.0

    def test_an_unknown_key_is_clear(self) -> None:
        assert CooldownRegistry().remaining("never-seen") == 0.0

    def test_a_non_positive_wait_is_not_recorded(self) -> None:
        registry = CooldownRegistry()
        registry.record("k", 0)

        assert registry.remaining("k") == 0.0

    def test_clear_removes_a_cooldown(self) -> None:
        registry = CooldownRegistry()
        registry.record("k", 60)

        registry.clear("k")

        assert registry.remaining("k") == 0.0

    def test_notes_only_server_supplied_circuit_breaker_waits(self) -> None:
        # The registry never invents a cooldown from local failure counts; it only remembers what
        # the platform explicitly asked for.
        registry = CooldownRegistry()

        registry.note("a", error("backend-unavailable", 503, retry_after=30))
        registry.note("b", error("backend-unavailable", 503))
        registry.note("c", error("rate-limit-exceeded-requests", 429, retry_after=30))

        assert registry.remaining("a") > 0
        assert registry.remaining("b") == 0.0
        assert registry.remaining("c") == 0.0
