from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import httpx
import pytest
import respx

from axonium import AsyncAxonium, Axonium, RetryPolicy
from axonium.errors import ForbiddenError
from axonium.observability.logging import request_fields
from axonium.observability.scopes import explain_forbidden

AUTH_URL = "https://gateway.test.invalid/oauth2/token"
CHAT_URL = "https://gateway.test.invalid/v1/chat/completions"
CATALOG_URL = "https://gateway.test.invalid/v1/models"
MINE_URL = "https://gateway.test.invalid/v1/models/mine"

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
    def test_says_nothing_when_the_token_reports_no_scopes(
        self, config_kwargs: dict[str, str]
    ) -> None:
        """A token response with an empty ``scope`` leaves nothing to compare a 403 against.

        **This test used to describe a route that no longer exists.** It said *"the public catalog
        is fetched without a token"* and called ``models.list()`` to reach a 403 with no scopes
        cached. ``PRM-167`` closed that route — the catalog requires a token and is an alias of
        ``mine()`` — so the premise died, and this test went on passing for eighteen days because
        another file's fixture registered the token endpoint on respx's **global** router and never
        removed it. The leak is closed in eight fixtures now; this was the only test standing on
        it, and it was standing on it while asserting something unreachable.

        What remains true is narrower and is the real guard: the diagnosis is built from the scopes
        the token response reports, so a token that reports none produces no hint. Guessing from an
        empty set is how a hint names a scope the operator never withheld.
        """
        respx.post(AUTH_URL).mock(return_value=token(scope=""))
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


class TestARetryWaitIsExplained:
    """A wait a caller would notice has to say so, or it arrives as a latency bug.

    The platform warned us about this shape directly: they were sent a report of "requests hanging
    30-60 seconds with no clean load threshold". They were not hangs. Their ``Retry-After`` on a
    ``429`` is seconds until the window resets, so it runs 0-60, and an SDK that respects it — as
    it should — looks from outside like one slow call among fast ones.
    """

    @respx.mock
    async def test_a_long_wait_is_reported_at_info(
        self, caplog: pytest.LogCaptureFixture, config_kwargs: dict[str, str]
    ) -> None:
        respx.post(AUTH_URL).mock(return_value=token())
        route = respx.get(MINE_URL)
        route.side_effect = [
            httpx.Response(
                429,
                headers={"Retry-After": "2"},
                json={"type": "x/rate-limit-exceeded-requests", "status": 429},
            ),
            httpx.Response(200, json={"object": "list", "data": []}),
        ]

        policy = RetryPolicy(max_backoff=5.0, jitter=False)
        with (
            caplog.at_level(logging.INFO, logger="axonium.client"),
            Axonium(**config_kwargs, retry=policy) as client,
        ):
            client.models.mine()

        waits = [r for r in caplog.records if "waiting before a retry" in r.message.lower()]
        assert waits, "a two-second wait left nothing at INFO to explain it"
        assert waits[0].delay_s >= 1.0  # type: ignore[attr-defined]

    @respx.mock
    async def test_a_short_backoff_stays_at_debug(
        self, caplog: pytest.LogCaptureFixture, config_kwargs: dict[str, str]
    ) -> None:
        # The noise worry is frequent small retries, not the rare long one. A sub-second backoff
        # that shouted at INFO would train people to filter the level that matters.
        respx.post(AUTH_URL).mock(return_value=token())
        route = respx.get(MINE_URL)
        route.side_effect = [
            httpx.Response(503, json={"type": "x/backend-unavailable", "status": 503}),
            httpx.Response(200, json={"object": "list", "data": []}),
        ]

        with (
            caplog.at_level(logging.INFO, logger="axonium.client"),
            Axonium(
                **config_kwargs,
                retry=RetryPolicy(initial_backoff=0.01, max_backoff=0.01, jitter=False),
            ) as c,
        ):
            c.models.mine()

        assert not [r for r in caplog.records if "waiting before a retry" in r.message.lower()]

    @respx.mock
    async def test_the_wait_is_never_counted_as_request_latency(
        self, caplog: pytest.LogCaptureFixture, config_kwargs: dict[str, str]
    ) -> None:
        # The distinction the platform asked about: time spent sleeping is not time the gateway
        # took. duration_ms is measured per attempt and must exclude the wait entirely.
        respx.post(AUTH_URL).mock(return_value=token())
        route = respx.get(MINE_URL)
        route.side_effect = [
            httpx.Response(
                429,
                headers={"Retry-After": "2"},
                json={"type": "x/rate-limit-exceeded-requests", "status": 429},
            ),
            httpx.Response(200, json={"object": "list", "data": []}),
        ]

        policy = RetryPolicy(max_backoff=5.0, jitter=False)
        with (
            caplog.at_level(logging.DEBUG, logger="axonium.client"),
            Axonium(**config_kwargs, retry=policy) as client,
        ):
            client.models.mine()

        durations = [r.duration_ms for r in caplog.records if hasattr(r, "duration_ms")]
        assert durations, "no attempt was timed"
        assert max(durations) < 1000, f"a 2s wait leaked into request latency: {durations}"


class TestTheGenAiSemanticConventions:
    """``VRT-AXO-002``: the attributes a span carries, against the shared table.

    Veritium asked for this and Argus's ``argus-obs-semconv`` suite is the external verifier, which
    is the useful part: these assertions check that we emit what the table says, and somebody
    else's suite checks that the table is the convention.
    """

    def test_the_map_matches_the_shared_table(self, spec_dir: Path) -> None:
        """Three SDKs emit spans, and the table they emit is one file or it is three.

        ``AXO-139`` is why this test exists rather than a comment asking for care: five SDKs kept
        five hand-copied lists of rate-limit scopes, they diverged, the published TypeScript
        documented a value the header never sends, and **nothing could catch it** because the list
        was prose. Go and Rust have the mirror of this test.
        """
        table = json.loads((spec_dir / "otel-genai.json").read_text(encoding="utf-8"))

        from axonium.observability import otel

        assert table["provider_name"] == otel.PROVIDER_NAME

        expected = {entry["path"]: entry["operation"] for entry in table["operations"]}
        measured = {path: otel.operation_name(path) for path in expected}
        assert measured == expected

        # The excluded routes are the half a count would miss: a map that answered every path would
        # satisfy "every operation in the table resolves" and still put a catalog listing in GenAI
        # aggregations as an inference call that used no tokens.
        for entry in table["no_genai_attributes"]:
            assert otel.operation_name(entry["path"]) is None, entry["path"]

    def test_no_well_known_value_is_passed_over_for_a_custom_one(self, spec_dir: Path) -> None:
        """The convention: a well-known value MUST be used where one applies.

        So a custom value is a claim that none applies, and this pins the claim rather than
        trusting it. ``rerank`` and ``image_generation`` are custom on purpose -- ``retrieval`` is
        RAG retrieval and carries a data source, and the enumeration has no image value -- and the
        day the convention gains one, this list is where the decision is recorded.
        """
        well_known = {
            "chat",
            "create_agent",
            "embeddings",
            "execute_tool",
            "generate_content",
            "invoke_agent",
            "invoke_workflow",
            "retrieval",
            "text_completion",
        }
        table = json.loads((spec_dir / "otel-genai.json").read_text(encoding="utf-8"))

        for entry in table["operations"]:
            claimed = entry["well_known"]
            actual = entry["operation"] in well_known
            assert claimed == actual, (
                f"{entry['path']} declares well_known={claimed} for {entry['operation']!r}, "
                f"which the convention's enumeration {'does' if actual else 'does not'} contain"
            )

    @respx.mock
    def test_a_chat_span_is_named_operation_then_model(self, config_kwargs: dict[str, str]) -> None:
        """``chat qwen3-0.6b``, which is the convention's rule.

        Argus was offered a lower-cardinality departure from it and refused: *follow the standard,
        the cardinality is our problem*. So the name is asserted, not left to whatever the route
        happened to produce.
        """
        respx.post(AUTH_URL).mock(return_value=token())
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "c-1",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "qwen3-0.6b",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "hi"},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )
        )

        recorded: list[tuple[str, dict[str, object]]] = []

        from axonium.observability import otel

        @contextmanager
        def capture(name: str, attributes: dict[str, object]) -> Iterator[None]:
            recorded.append((name, attributes))
            yield None

        with (
            mock.patch.object(otel, "_record", capture),
            Axonium(otel_enabled=True, **config_kwargs) as client,
        ):
            client.chat.completions.create(
                model="qwen3-0.6b", messages=[{"role": "user", "content": "hi"}]
            )

        assert recorded, "no span was opened"
        name, attributes = recorded[-1]
        assert name == "chat qwen3-0.6b"
        assert attributes["gen_ai.provider.name"] == "prometheus-gateway"
        assert attributes["gen_ai.operation.name"] == "chat"
        assert attributes["gen_ai.request.model"] == "qwen3-0.6b"
        assert attributes["server.address"] == "gateway.test.invalid"
        # Deprecated in favour of `gen_ai.provider.name`. Asserted ABSENT rather than merely
        # un-asserted: an emitter sending both would satisfy every line above while still sending
        # the attribute Veritium asked us to stop sending.
        assert "gen_ai.system" not in attributes

    @respx.mock
    def test_a_catalog_span_carries_no_genai_attributes(
        self, config_kwargs: dict[str, str]
    ) -> None:
        """Reading the token's scopes is not an inference operation.

        It used to carry ``gen_ai.system`` and a null model, so a scope lookup appeared in GenAI
        aggregations as a call that somehow used no tokens.
        """
        respx.post(AUTH_URL).mock(return_value=token())
        respx.get(MINE_URL).mock(
            return_value=httpx.Response(200, json={"object": "list", "data": []})
        )

        recorded: list[tuple[str, dict[str, object]]] = []

        from axonium.observability import otel

        @contextmanager
        def capture(name: str, attributes: dict[str, object]) -> Iterator[None]:
            recorded.append((name, attributes))
            yield None

        with (
            mock.patch.object(otel, "_record", capture),
            Axonium(otel_enabled=True, **config_kwargs) as client,
        ):
            client.models.mine()

        assert recorded, "no span was opened"
        name, attributes = recorded[-1]
        assert not [key for key in attributes if key.startswith("gen_ai.")], attributes
        assert "models/mine" in name
        # Still useful without the GenAI half: this is what correlates the call with the platform.
        assert attributes["server.address"] == "gateway.test.invalid"
