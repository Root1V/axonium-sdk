from __future__ import annotations

import httpx

from axonium.errors import StreamInterruptedError
from axonium.models.common import RateLimitSnapshot, ResponseMeta, Usage

RATE_LIMIT_HEADERS = {
    "X-RateLimit-Limit-Requests": "60",
    "X-RateLimit-Remaining-Requests": "59",
    "X-RateLimit-Reset-Requests": "1700000060",
    "X-RateLimit-Limit-Tokens": "100000",
    "X-RateLimit-Remaining-Tokens": "99986",
    "X-RateLimit-Reset-Tokens": "1700000060",
}


class TestRateLimitSnapshot:
    def test_parses_all_six_headers(self) -> None:
        snapshot = RateLimitSnapshot.from_headers(httpx.Headers(RATE_LIMIT_HEADERS))

        assert snapshot.limit_requests == 60
        assert snapshot.remaining_requests == 59
        assert snapshot.reset_requests == 1700000060
        assert snapshot.limit_tokens == 100000
        assert snapshot.remaining_tokens == 99986
        assert snapshot.reset_tokens == 1700000060
        assert not snapshot.is_empty

    def test_header_lookup_is_case_insensitive(self) -> None:
        snapshot = RateLimitSnapshot.from_headers(
            httpx.Headers({"x-ratelimit-remaining-requests": "7"})
        )

        assert snapshot.remaining_requests == 7

    def test_absent_headers_yield_an_empty_snapshot(self) -> None:
        # Health, metrics and catalog routes carry no rate-limit headers.
        snapshot = RateLimitSnapshot.from_headers(httpx.Headers({}))

        assert snapshot.is_empty
        assert snapshot.remaining_requests is None

    def test_malformed_values_are_ignored_rather_than_raising(self) -> None:
        # A proxy rewriting a header must not turn a successful call into an exception.
        snapshot = RateLimitSnapshot.from_headers(
            httpx.Headers({"X-RateLimit-Remaining-Requests": "unknown"})
        )

        assert snapshot.remaining_requests is None


class TestResponseMeta:
    def test_captures_correlation_ids_and_budget(self) -> None:
        meta = ResponseMeta.from_headers(
            httpx.Headers(
                {
                    "X-Request-ID": "5c1e2b3a-0000-4000-8000-000000000000",
                    "X-Trace-ID": "b04044d6-0000-4000-8000-000000000000",
                    **RATE_LIMIT_HEADERS,
                }
            )
        )

        assert meta.request_id == "5c1e2b3a-0000-4000-8000-000000000000"
        assert meta.trace_id == "b04044d6-0000-4000-8000-000000000000"
        assert meta.rate_limit is not None
        assert meta.rate_limit.remaining_requests == 59

    def test_rate_limit_is_none_when_no_headers_were_sent(self) -> None:
        # An empty snapshot would imply a budget of zero; absence must stay absent.
        meta = ResponseMeta.from_headers(httpx.Headers({"X-Request-ID": "abc"}))

        assert meta.rate_limit is None
        assert meta.trace_id is None


class TestUsage:
    def test_embedding_usage_has_no_completion_tokens(self) -> None:
        usage = Usage.model_validate({"prompt_tokens": 2, "total_tokens": 2})

        assert usage.completion_tokens is None
        assert usage.estimated is False

    def test_unknown_backend_fields_are_preserved(self) -> None:
        # Inference responses pass through from heterogeneous backends; fields this SDK does not
        # model must remain visible to the caller instead of being dropped.
        usage = Usage.model_validate({"prompt_tokens": 5, "cache_n": 3})

        assert usage.model_dump()["cache_n"] == 3


def test_stream_interrupted_error_retains_partial_output() -> None:
    exc = StreamInterruptedError(
        "stream interrupted",
        partial_content="Hello, wor",
        request_id="req-1",
        trace_id="trace-1",
        raw={"error": "stream interrupted"},
    )

    assert exc.partial_content == "Hello, wor"
    assert exc.request_id == "req-1"
    assert exc.trace_id == "trace-1"
    assert exc.raw == {"error": "stream interrupted"}
