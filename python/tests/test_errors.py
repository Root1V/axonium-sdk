from __future__ import annotations

from typing import Any

import pytest

from axonium import errors
from axonium.errors import (
    APIError,
    BackendUnavailableError,
    BadRequestError,
    ForbiddenError,
    InvalidClientError,
    ModelNotLoadedError,
    OAuthError,
    RateLimitError,
    ServerError,
    TokenExpiredError,
    UnauthorizedError,
    UnknownModelError,
    error_from_response,
    oauth_error_from_response,
)
from axonium.models.common import RateLimitSnapshot


def problem(suffix: str, status: int, **extra: Any) -> dict[str, Any]:
    return {
        "type": f"https://prometheus.internal/errors/{suffix}",
        "title": suffix.replace("-", " ").title(),
        "status": status,
        "detail": f"Something about {suffix}.",
        "instance": "/v1/chat/completions",
        "request_id": "5c1e2b3a-0000-4000-8000-000000000000",
        "trace_id": "b04044d6-0000-4000-8000-000000000000",
        **extra,
    }


class TestCatalogParity:
    """The Python hierarchy must not drift from the shared cross-language catalog."""

    def test_every_catalog_error_maps_to_a_specific_class(
        self, error_catalog: dict[str, Any]
    ) -> None:
        for entry in error_catalog["gateway_errors"]:
            suffix = entry["suffix"]
            exc = error_from_response(status=entry["status"], body=problem(suffix, entry["status"]))

            assert exc.type_suffix == suffix
            assert type(exc) not in (BadRequestError, UnauthorizedError, ServerError, APIError), (
                f"{suffix} fell back to a generic class instead of a dedicated one"
            )

    def test_retryability_matches_the_catalog(self, error_catalog: dict[str, Any]) -> None:
        for entry in error_catalog["gateway_errors"]:
            exc = error_from_response(
                status=entry["status"], body=problem(entry["suffix"], entry["status"])
            )
            assert exc.retryable is entry["retryable"], (
                f"{entry['suffix']} retryability disagrees with spec/errors.json"
            )

    def test_every_catalog_oauth_error_maps_to_a_specific_class(
        self, error_catalog: dict[str, Any]
    ) -> None:
        for entry in error_catalog["oauth_errors"]:
            exc = oauth_error_from_response(
                status=entry["status"],
                body={"error": entry["error"], "error_description": "..."},
            )
            assert exc.error == entry["error"]
            assert type(exc) is not OAuthError, (
                f"{entry['error']} fell back to the generic OAuthError"
            )

    def test_no_class_claims_a_suffix_absent_from_the_catalog(
        self, error_catalog: dict[str, Any]
    ) -> None:
        catalog_suffixes = {entry["suffix"] for entry in error_catalog["gateway_errors"]}
        assert set(errors._BY_SUFFIX) == catalog_suffixes


class TestProblemDetailsParsing:
    def test_extracts_every_envelope_field(self) -> None:
        exc = error_from_response(status=400, body=problem("unknown-model", 400))

        assert isinstance(exc, UnknownModelError)
        assert exc.status == 400
        assert exc.title == "Unknown Model"
        assert exc.detail == "Something about unknown-model."
        assert exc.instance == "/v1/chat/completions"
        assert exc.request_id == "5c1e2b3a-0000-4000-8000-000000000000"
        assert exc.trace_id == "b04044d6-0000-4000-8000-000000000000"
        assert exc.raw["type"].endswith("unknown-model")

    def test_message_prefers_detail_and_carries_correlation_ids(self) -> None:
        exc = error_from_response(status=403, body=problem("forbidden", 403))
        rendered = str(exc)

        assert "Something about forbidden." in rendered
        assert "request_id=5c1e2b3a-0000-4000-8000-000000000000" in rendered
        assert "trace_id=b04044d6-0000-4000-8000-000000000000" in rendered

    def test_falls_back_to_title_then_status_when_detail_is_absent(self) -> None:
        assert "Forbidden" in str(
            error_from_response(status=403, body={"title": "Forbidden", "status": 403})
        )
        assert "HTTP 403" in str(error_from_response(status=403, body={}))

    @pytest.mark.parametrize(
        ("status", "expected"),
        [(400, BadRequestError), (401, UnauthorizedError), (503, ServerError)],
    )
    def test_unknown_suffix_falls_back_by_status_without_raising(
        self, status: int, expected: type[APIError]
    ) -> None:
        # The catalog is expected to grow; an unfamiliar error code must not break the SDK.
        exc = error_from_response(status=status, body=problem("some-future-error", status))

        assert type(exc) is expected
        assert exc.type_suffix == "some-future-error"

    def test_handles_a_missing_or_malformed_body(self) -> None:
        assert isinstance(error_from_response(status=500, body=None), ServerError)
        assert error_from_response(status=500, body={"type": 42}).type_suffix is None


class TestRetryAfter:
    def test_reads_retry_after_from_the_body(self) -> None:
        # The rate-limiting envelope omits trace_id and adds retry_after.
        body = problem("rate-limit-exceeded-requests", 429, retry_after=30)
        del body["trace_id"]

        exc = error_from_response(status=429, body=body)

        assert isinstance(exc, RateLimitError)
        assert exc.retry_after == 30.0
        assert exc.trace_id is None

    def test_reads_retry_after_from_the_header(self) -> None:
        # The gateway writes the Retry-After header and the body's retry_after from one variable,
        # so they cannot disagree; reading either is equivalent. Both paths are supported anyway,
        # since a proxy could strip one of them.
        exc = error_from_response(
            status=429,
            body=problem("rate-limit-exceeded-requests", 429),
            retry_after=30.0,
        )

        assert exc.retry_after == 30.0

    def test_backend_unavailable_without_retry_after_leaves_it_unset(self) -> None:
        # Retry-After is only set for the circuit-breaker case; absent it there is no wait hint.
        exc = error_from_response(status=503, body=problem("backend-unavailable", 503))

        assert isinstance(exc, BackendUnavailableError)
        assert exc.retry_after is None
        assert exc.retryable is True


class TestSemantics:
    def test_model_not_loaded_is_not_retryable_despite_being_5xx(self) -> None:
        # It needs operator action, so retrying only burns time.
        exc = error_from_response(status=503, body=problem("model-not-loaded", 503))

        assert isinstance(exc, ModelNotLoadedError)
        assert exc.retryable is False

    def test_token_expired_is_the_only_retryable_401(self, error_catalog: dict[str, Any]) -> None:
        retryable = {
            entry["suffix"]
            for entry in error_catalog["gateway_errors"]
            if entry["status"] == 401 and entry["retryable"]
        }
        assert retryable == {"token-expired"}
        assert TokenExpiredError.retryable is True

    def test_rate_limit_snapshot_can_be_attached(self) -> None:
        snapshot = RateLimitSnapshot(remaining_requests=0, limit_requests=60)
        exc = error_from_response(
            status=429,
            body=problem("rate-limit-exceeded-requests", 429),
            rate_limit=snapshot,
        )

        assert exc.rate_limit is snapshot

    def test_oauth_errors_are_not_api_errors(self) -> None:
        # The envelopes differ in shape and meaning, so `except APIError` must not swallow an
        # authentication failure.
        exc = oauth_error_from_response(
            status=401,
            body={"error": "invalid_client", "error_description": "Invalid client credentials."},
        )

        assert isinstance(exc, InvalidClientError)
        assert not isinstance(exc, APIError)
        assert str(exc) == "Invalid client credentials."

    def test_unknown_oauth_code_falls_back_without_raising(self) -> None:
        exc = oauth_error_from_response(status=400, body={"error": "some_future_code"})

        assert type(exc) is OAuthError
        assert exc.error == "some_future_code"

    def test_oauth_body_without_an_error_code_still_produces_an_error(self) -> None:
        # A proxy or gateway can return a non-conforming body; authentication must still fail
        # with something a caller can catch rather than a parse crash.
        exc = oauth_error_from_response(status=502, body={})

        assert type(exc) is OAuthError
        assert exc.error is None
        assert str(exc) == "HTTP 502"

    def test_forbidden_is_never_retryable(self) -> None:
        assert ForbiddenError.retryable is False
