from __future__ import annotations

import httpx
import pytest

from axonium.transport.dispatch import retry_after_seconds

SERVER_NOW = "Tue, 14 Nov 2023 22:13:20 GMT"  # epoch 1700000000


def response(headers: dict[str, str]) -> httpx.Response:
    return httpx.Response(429, headers=headers)


class TestRetryAfter:
    def test_reads_delta_seconds(self) -> None:
        assert retry_after_seconds(response({"Retry-After": "30"})) == 30.0

    def test_absent_header_yields_none(self) -> None:
        # A 503 from a genuine connection failure carries no wait hint at all.
        assert retry_after_seconds(response({})) is None

    def test_reads_an_http_date_relative_to_the_servers_own_clock(self) -> None:
        # Both timestamps come from the server, so the wait is unaffected by client clock skew.
        headers = {"Retry-After": "Tue, 14 Nov 2023 22:14:20 GMT", "Date": SERVER_NOW}

        assert retry_after_seconds(response(headers)) == 60.0

    def test_a_date_already_in_the_past_yields_no_wait_rather_than_a_negative_one(self) -> None:
        headers = {"Retry-After": "Tue, 14 Nov 2023 22:12:20 GMT", "Date": SERVER_NOW}

        assert retry_after_seconds(response(headers)) == 0.0

    def test_an_http_date_without_a_server_date_is_unusable(self) -> None:
        # Resolving it against the local clock would reintroduce the skew this avoids.
        headers = {"Retry-After": "Tue, 14 Nov 2023 22:14:20 GMT"}

        assert retry_after_seconds(response(headers)) is None

    @pytest.mark.parametrize(
        "headers",
        [
            {"Retry-After": "soon"},
            {"Retry-After": "soon", "Date": SERVER_NOW},
            {"Retry-After": "Tue, 14 Nov 2023 22:14:20 GMT", "Date": "whenever"},
        ],
        ids=["unparseable", "unparseable-with-date", "unparseable-server-date"],
    )
    def test_malformed_values_yield_none_instead_of_raising(self, headers: dict[str, str]) -> None:
        # A bad header must not turn a retryable error into a crash.
        assert retry_after_seconds(response(headers)) is None
