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


class TestTheSecondWitness:
    """``gen_ai.response.model``: the attribute Argus could not get from the gateway.

    `ARG-AXO-001`, 2026-10-13. On the gateway's own span that attribute and
    ``gen_ai.request.model`` come from the **same already-resolved variable**, so the pair *cannot*
    differ — Prometheus measured it and said so in their `P-21` rather than let Argus look at
    thirty minutes of data and conclude the case never happens. From a client the two come from
    different places: the request model is the string the caller wrote, which may be an alias, and
    the response model is read out of the body. So a client span is the one place the discrepancy
    is observable, and Argus asked for it because *"half the inference incidents we have seen are
    explained by the model that answered not being the one that was asked for"*.
    """

    @respx.mock
    def test_an_alias_and_the_served_name_are_both_on_the_span(
        self, config_kwargs: dict[str, str]
    ) -> None:
        """The pair that the gateway's own span cannot show.

        The request asks for an alias and the body answers with the public name — the exact shape
        Prometheus measured when they found their own span reporting both as the resolved name.
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
                    "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
                },
            )
        )

        recorded: dict[str, object] = {}

        from axonium.observability import otel

        class Collecting:
            def set_attribute(self, key: str, value: object) -> None:
                recorded[key] = value

        @contextmanager
        def capture(name: str, attributes: dict[str, object]) -> Iterator[Collecting]:
            recorded.update(attributes)
            yield Collecting()

        with (
            mock.patch.object(otel, "_record", capture),
            Axonium(otel_enabled=True, **config_kwargs) as client,
        ):
            client.chat.completions.create(
                model="qwen3-0-6b-iq4-nl-local-1",
                messages=[{"role": "user", "content": "hi"}],
            )

        assert recorded["gen_ai.request.model"] == "qwen3-0-6b-iq4-nl-local-1"
        assert recorded["gen_ai.response.model"] == "qwen3-0.6b"
        # The pair differing is the whole point. Asserted as a pair rather than as two values,
        # because two correct-looking values that happen to be equal is exactly the state
        # Prometheus's gateway was in while reporting both.
        assert recorded["gen_ai.request.model"] != recorded["gen_ai.response.model"]
        assert recorded["gen_ai.usage.input_tokens"] == 11
        assert recorded["gen_ai.usage.output_tokens"] == 3

    @respx.mock
    def test_usage_is_absent_rather_than_zero_when_the_platform_reports_none(
        self, config_kwargs: dict[str, str]
    ) -> None:
        """A fabricated 0 reads exactly like a measured 0.

        In a trace — which is where somebody goes to ask what a call cost — it sums into a total
        that looks exact. Argus raised this and confirmed their own ``usage()`` takes ``int | None``
        and skips ``None`` for the same reason, so the two sides agree instead of each guessing.
        """
        respx.post(AUTH_URL).mock(return_value=token())
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "c-1",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "m",
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

        recorded: dict[str, object] = {}

        from axonium.observability import otel

        class Collecting:
            def set_attribute(self, key: str, value: object) -> None:
                recorded[key] = value

        @contextmanager
        def capture(name: str, attributes: dict[str, object]) -> Iterator[Collecting]:
            recorded.update(attributes)
            yield Collecting()

        with (
            mock.patch.object(otel, "_record", capture),
            Axonium(otel_enabled=True, **config_kwargs) as client,
        ):
            client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}])

        assert "gen_ai.usage.input_tokens" not in recorded, recorded
        assert "gen_ai.usage.output_tokens" not in recorded, recorded
        # The served model is still there: the body named it, only the usage was missing.
        assert recorded["gen_ai.response.model"] == "m"

    def test_a_body_that_is_not_what_we_expect_never_breaks_a_good_response(self) -> None:
        """This runs on the success path of every non-streaming call.

        A span attribute must never be the reason a request that worked reports a failure, so every
        shape below yields nothing rather than raising. ``True`` is in the list because
        ``isinstance(True, int)`` is true in Python, and ``gen_ai.usage.input_tokens = True`` would
        be a number nobody could explain.
        """
        from axonium.observability.otel import outcome_of

        assert outcome_of(None) == (None, None, None)
        assert outcome_of("a string") == (None, None, None)
        assert outcome_of([1, 2]) == (None, None, None)
        assert outcome_of({"model": 7}) == (None, None, None)
        assert outcome_of({"model": ""}) == (None, None, None)
        assert outcome_of({"model": "m", "usage": "not an object"}) == ("m", None, None)
        assert outcome_of({"model": "m", "usage": {"prompt_tokens": "11"}}) == ("m", None, None)
        assert outcome_of({"model": "m", "usage": {"prompt_tokens": True}}) == ("m", None, None)


class TestAStreamedCallIsObservedToo:
    """A streamed call used to emit **no span at all** in this SDK. Go and Rust emitted one.

    Found while answering `ARG-AXO-002`, where Argus took my own sentence — *"in none of the three
    is it recorded on a streamed call"* — as a known limitation and said why it is the worst place
    to have one: **the long generations are the ones done with streaming**, so the witness is
    missing precisely where the model is slow, which is where somebody looks when something is
    wrong. Measuring it to answer them showed my sentence had concealed something worse for Python:
    not the response attributes were missing, the span was.

    Argus also named the datum a stream *can* give, and it is the one recorded here: the model from
    the first chunk. A stream's body never arrives as an object this layer parses, so it comes from
    a chunk or not at all.
    """

    SSE = (
        b'data: {"id":"c","model":"qwen3-0.6b","choices":[{"index":0,'
        b'"delta":{"content":"hi"},"finish_reason":null}]}\n\n'
        b'data: {"id":"c","model":"qwen3-0.6b","choices":[{"index":0,'
        b'"delta":{"content":"!"},"finish_reason":"stop"}]}\n\n'
        b"data: [DONE]\n\n"
    )

    @contextmanager
    def _capture(
        self, opened: list[tuple[str, dict[str, object]]], recorded: dict[str, object]
    ) -> Iterator[None]:
        from axonium.observability import otel

        class Collecting:
            def set_attribute(self, key: str, value: object) -> None:
                recorded[key] = value

        @contextmanager
        def capture(name: str, attributes: dict[str, object]) -> Iterator[Collecting]:
            opened.append((name, attributes))
            yield Collecting()

        with mock.patch.object(otel, "_record", capture):
            yield

    @respx.mock
    def test_the_span_exists_and_names_the_model_from_the_first_chunk(
        self, config_kwargs: dict[str, str]
    ) -> None:
        respx.post(AUTH_URL).mock(return_value=token(scope="inference:stream"))
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                200, content=self.SSE, headers={"Content-Type": "text/event-stream"}
            )
        )

        opened: list[tuple[str, dict[str, object]]] = []
        recorded: dict[str, object] = {}

        with (
            self._capture(opened, recorded),
            Axonium(otel_enabled=True, **config_kwargs) as client,
            client.chat.completions.stream(
                model="qwen3-0-6b-iq4-nl-local-1",
                messages=[{"role": "user", "content": "hi"}],
            ) as stream,
        ):
            for _ in stream:
                pass

        assert opened, "a streamed call opened no span, which is where it started"
        name, attributes = opened[-1]
        assert name == "chat qwen3-0-6b-iq4-nl-local-1"
        assert attributes["gen_ai.request.model"] == "qwen3-0-6b-iq4-nl-local-1"
        # The alias asked for, the public name served: the pair, from a stream.
        assert recorded["gen_ai.response.model"] == "qwen3-0.6b"

    @respx.mock
    def test_the_span_is_still_open_while_the_stream_is_consumed(
        self, config_kwargs: dict[str, str]
    ) -> None:
        """The span covers the generation, not the connection.

        Ending it when the headers arrive would time the handshake of a call whose whole cost is
        what comes after — and a span whose duration excludes the slow part is worse than none,
        because it reads as a fast call. Asserted by recording *inside* the iteration: the attribute
        can only land if the span is open at that moment.
        """
        respx.post(AUTH_URL).mock(return_value=token(scope="inference:stream"))
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                200, content=self.SSE, headers={"Content-Type": "text/event-stream"}
            )
        )

        opened: list[tuple[str, dict[str, object]]] = []
        recorded: dict[str, object] = {}
        seen_mid_stream: list[bool] = []

        with (
            self._capture(opened, recorded),
            Axonium(otel_enabled=True, **config_kwargs) as client,
            client.chat.completions.stream(
                model="m", messages=[{"role": "user", "content": "hi"}]
            ) as stream,
        ):
            for _ in stream:
                seen_mid_stream.append("gen_ai.response.model" in recorded)

        assert seen_mid_stream, "the stream yielded nothing, so nothing was measured"
        assert seen_mid_stream[0], (
            "the first chunk's model was not on the span while the stream was still being read, "
            "so the span was not open during the generation"
        )

    @respx.mock
    def test_the_model_is_recorded_once_and_not_per_chunk(
        self, config_kwargs: dict[str, str]
    ) -> None:
        """Every chunk names the model; a long generation would write it hundreds of times."""
        respx.post(AUTH_URL).mock(return_value=token(scope="inference:stream"))
        respx.post(CHAT_URL).mock(
            return_value=httpx.Response(
                200, content=self.SSE, headers={"Content-Type": "text/event-stream"}
            )
        )

        writes: list[str] = []

        from axonium.observability import otel

        class Counting:
            def set_attribute(self, key: str, value: object) -> None:
                writes.append(key)

        @contextmanager
        def capture(name: str, attributes: dict[str, object]) -> Iterator[Counting]:
            yield Counting()

        with (
            mock.patch.object(otel, "_record", capture),
            Axonium(otel_enabled=True, **config_kwargs) as client,
            client.chat.completions.stream(
                model="m", messages=[{"role": "user", "content": "hi"}]
            ) as stream,
        ):
            for _ in stream:
                pass

        assert writes.count("gen_ai.response.model") == 1, writes
