from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest
import respx

from axonium import AsyncAxonium, Axonium
from axonium.errors import ForbiddenError
from axonium.observability.logging import request_fields
from axonium.observability.scopes import explain_forbidden

AUTH_URL = "https://auth.test.invalid/oauth2/token"
CHAT_URL = "https://gateway.test.invalid/v1/chat/completions"
CATALOG_URL = "https://gateway.test.invalid/v1/models"

SECRET_PROMPT = "my patient id is 123-45-6789"
SECRET_REPLY = "Acknowledged, 123-45-6789."


def token(scope: str = "inference:read model:llama3-8b-q4") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": "s3cr3t-token",
            "token_type": "bearer",
            "expires_in": 300,
            "scope": scope,
        },
    )


def forbidden() -> httpx.Response:
    return httpx.Response(
        403,
        json={
            "type": "https://prometheus.internal/errors/forbidden",
            "status": 403,
            "detail": "This client is not authorized.",
        },
    )


class TestSafeLogging:
    def test_request_fields_drops_anything_unset(self) -> None:
        fields = request_fields(method="POST", model="m")

        assert fields == {"method": "POST", "model": "m"}

    def test_duration_is_rounded_rather_than_reported_to_full_float_precision(self) -> None:
        assert request_fields(duration_ms=12.3456789)["duration_ms"] == 12.35

    @respx.mock
    def test_prompts_completions_and_credentials_never_reach_the_logs(
        self, config_kwargs: dict[str, str], caplog: pytest.LogCaptureFixture
    ) -> None:
        # This is the property that matters most in this module: a library that can be made to log
        # prompts is how sensitive content ends up in an unaudited log aggregator.
        respx.post(AUTH_URL).mock(return_value=token())
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                200,
                headers={"X-Request-ID": "req-1"},
                json={"choices": [{"message": {"role": "assistant", "content": SECRET_REPLY}}]},
            )
        )

        with caplog.at_level(logging.DEBUG, logger="axonium"), Axonium(**config_kwargs) as client:
            client.chat.completions.create(
                model="llama3-8b-q4", messages=[{"role": "user", "content": SECRET_PROMPT}]
            )

        emitted = caplog.text
        assert emitted, "the request should have been logged at all"
        assert SECRET_PROMPT not in emitted
        assert SECRET_REPLY not in emitted
        assert "s3cr3t-token" not in emitted
        assert "test-secret" not in emitted

    @respx.mock
    def test_logs_carry_the_fields_needed_to_correlate_with_platform_traces(
        self, config_kwargs: dict[str, str], caplog: pytest.LogCaptureFixture
    ) -> None:
        respx.post(AUTH_URL).mock(return_value=token())
        respx.get(CATALOG_URL).mock(
            return_value=httpx.Response(
                200,
                headers={"X-Request-ID": "req-7", "X-Trace-ID": "trace-7"},
                json={"object": "list", "data": []},
            )
        )

        with caplog.at_level(logging.DEBUG, logger="axonium"), Axonium(**config_kwargs) as client:
            client.models.list()

        record = next(r for r in caplog.records if r.message == "Request completed")
        assert record.request_id == "req-7"  # type: ignore[attr-defined]
        assert record.trace_id == "trace-7"  # type: ignore[attr-defined]
        assert record.status == 200  # type: ignore[attr-defined]
        assert record.duration_ms >= 0  # type: ignore[attr-defined]

    def test_the_library_installs_a_null_handler_and_configures_nothing_else(self) -> None:
        # Handlers, levels and formatting belong to the host application.
        axonium_logger = logging.getLogger("axonium")

        assert any(isinstance(h, logging.NullHandler) for h in axonium_logger.handlers)
        assert axonium_logger.level == logging.NOTSET


class TestScopeDiagnosis:
    def test_says_nothing_when_no_scope_was_reported(self) -> None:
        # Without a granted scope any explanation would be a guess.
        assert explain_forbidden([], model="m", streaming=False) is None

    def test_names_a_missing_model_grant(self) -> None:
        hint = explain_forbidden(["inference:read"], model="llama3-8b-q4", streaming=False)

        assert hint is not None
        assert "model:llama3-8b-q4" in hint
        assert "case-sensitive" in hint

    def test_distinguishes_the_streaming_scope_from_the_read_scope(self) -> None:
        # Holding one does not grant the other, and this is the mistake the platform's own guide
        # calls out as most common.
        hint = explain_forbidden(["inference:read", "model:m"], model="m", streaming=True)

        assert hint is not None
        assert "inference:stream" in hint
        assert "does not grant the other" in hint

    def test_reports_both_problems_at_once(self) -> None:
        hint = explain_forbidden(["ui:chat"], model="m", streaming=False)

        assert hint is not None
        assert "inference:read" in hint
        assert "model:m" in hint

    def test_says_nothing_when_the_token_holds_everything_required(self) -> None:
        # Then the denial has some other cause and inventing an explanation would mislead.
        assert explain_forbidden(["inference:read", "model:m"], model="m", streaming=False) is None

    @respx.mock
    def test_a_denied_request_is_explained_in_terms_of_the_tokens_scopes(
        self, config_kwargs: dict[str, str]
    ) -> None:
        respx.post(AUTH_URL).mock(return_value=token(scope="inference:read"))
        respx.post(CHAT_URL).mock(return_value=forbidden())

        with Axonium(**config_kwargs) as client, pytest.raises(ForbiddenError) as caught:
            client.chat.completions.create(
                model="llama3-8b-q4", messages=[{"role": "user", "content": "hi"}]
            )

        assert caught.value.hint is not None
        assert "model:llama3-8b-q4" in str(caught.value)

    @respx.mock
    def test_a_denied_stream_is_explained_as_a_streaming_scope_problem(
        self, config_kwargs: dict[str, str]
    ) -> None:
        respx.post(AUTH_URL).mock(return_value=token(scope="inference:read model:m"))
        respx.post(CHAT_URL).mock(return_value=forbidden())

        with Axonium(**config_kwargs) as client:
            opened = client.chat.completions.stream(
                model="m", messages=[{"role": "user", "content": "hi"}]
            )

            with pytest.raises(ForbiddenError) as caught, opened:
                pass

        assert "inference:stream" in str(caught.value)

    @respx.mock
    async def test_the_async_stream_is_explained_the_same_way(
        self, config_kwargs: dict[str, str]
    ) -> None:
        respx.post(AUTH_URL).mock(return_value=token(scope="inference:read model:m"))
        respx.post(CHAT_URL).mock(return_value=forbidden())

        async with AsyncAxonium(**config_kwargs) as client:
            opened = client.chat.completions.stream(
                model="m", messages=[{"role": "user", "content": "hi"}]
            )

            with pytest.raises(ForbiddenError) as caught:
                async with opened:
                    pass

        assert "inference:stream" in str(caught.value)

    @respx.mock
    def test_other_errors_are_left_alone(self, config_kwargs: dict[str, str]) -> None:
        from axonium.errors import UnknownModelError

        respx.post(AUTH_URL).mock(return_value=token())
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                400,
                json={"type": "https://prometheus.internal/errors/unknown-model", "status": 400},
            )
        )

        with Axonium(**config_kwargs) as client, pytest.raises(UnknownModelError) as caught:
            client.chat.completions.create(
                model="nope", messages=[{"role": "user", "content": "hi"}]
            )

        assert caught.value.hint is None

    @respx.mock
    def test_says_nothing_when_no_token_has_been_obtained(
        self, config_kwargs: dict[str, str]
    ) -> None:
        # The public catalog is fetched without a token, so a 403 there — a proxy in front of it,
        # say — leaves nothing to diagnose against.
        respx.get(CATALOG_URL).mock(return_value=forbidden())

        with Axonium(**config_kwargs) as client, pytest.raises(ForbiddenError) as caught:
            client.models.list()

        assert caught.value.hint is None


class TestOpenTelemetry:
    def test_tracing_is_off_unless_asked_for(self, config_kwargs: dict[str, str]) -> None:
        # The platform runs its own tracing; this only exists so a caller's traces can join up.
        with Axonium(**config_kwargs) as client:
            assert client.config.otel_enabled is False

    @respx.mock
    def test_requests_succeed_with_tracing_enabled(self, config_kwargs: dict[str, str]) -> None:
        respx.post(AUTH_URL).mock(return_value=token())
        respx.get(CATALOG_URL).mock(
            return_value=httpx.Response(200, json={"object": "list", "data": []})
        )

        with Axonium(otel_enabled=True, **config_kwargs) as client:
            assert client.models.list().ids == ()

    @respx.mock
    def test_no_trace_context_is_sent_outbound(self, config_kwargs: dict[str, str]) -> None:
        # The gateway never reads traceparent, in any mode; sending it would imply a link the
        # platform will not make.
        respx.post(AUTH_URL).mock(return_value=token())
        route = respx.get(CATALOG_URL).mock(
            return_value=httpx.Response(200, json={"object": "list", "data": []})
        )

        with Axonium(otel_enabled=True, **config_kwargs) as client:
            client.models.list()

        headers = route.calls.last.request.headers
        assert "traceparent" not in headers
        assert "X-Trace-ID" not in headers

    @respx.mock
    def test_correlation_ids_are_recorded_onto_the_span(
        self, config_kwargs: dict[str, str]
    ) -> None:
        # Correlation runs inbound: the gateway's IDs land on the caller's span, since the
        # platform will not accept trace context going the other way.
        respx.post(AUTH_URL).mock(return_value=token())
        respx.get(CATALOG_URL).mock(
            return_value=httpx.Response(
                200,
                headers={"X-Request-ID": "req-9", "X-Trace-ID": "trace-9"},
                json={"object": "list", "data": []},
            )
        )

        with Axonium(otel_enabled=True, **config_kwargs) as client:
            result = client.models.list()

        assert result.meta is not None
        assert result.meta.request_id == "req-9"

    def test_spans_degrade_to_no_ops_when_the_extra_is_not_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A base install carries no OpenTelemetry dependency, so enabling tracing there must be
        # inert rather than an ImportError at the first request.
        import builtins

        from axonium.observability import otel

        monkeypatch.setattr(otel, "_tracer", None)
        monkeypatch.setattr(otel, "_unavailable", False)

        real_import = builtins.__import__

        def refuse_opentelemetry(name: str, *args: object, **kwargs: object) -> object:
            if name.startswith("opentelemetry"):
                raise ImportError("no opentelemetry here")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(builtins, "__import__", refuse_opentelemetry)

        with otel.span("test", enabled=True, foo="bar") as active:
            assert active is None

        # The failed import is remembered rather than retried on every call.
        assert otel._unavailable is True

    def test_span_helper_is_a_no_op_when_disabled(self) -> None:
        from axonium.observability.otel import span

        with span("test", enabled=False, foo="bar") as active:
            assert active is None

    def test_recording_onto_no_span_is_harmless(self) -> None:
        from axonium.observability.otel import record_response

        record_response(None, request_id="req", trace_id="trace")


def test_spec_fixtures_are_reachable_from_the_python_suite(spec_dir: Path) -> None:
    # The Go and Rust SDKs will assert against these same files.
    assert (spec_dir / "errors.json").exists()
    assert (spec_dir / "fixtures" / "chat_stream_ok.sse").exists()
