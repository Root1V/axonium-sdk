"""Replays every case in ``spec/cases/manifest.json`` against the Python SDK.

The manifest is language-neutral and the Go and Rust SDKs will replay the same file, so a
behavioral difference between the SDKs shows up as a failing case here rather than as a surprise
in production. Adding a case is how a new behavior becomes binding on all three.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

import httpx
import pytest
import respx

from axonium import AsyncAxonium, Axonium, RetryPolicy
from axonium.errors import APIError, AuthTransportError, OAuthError, StreamInterruptedError

REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC = REPO_ROOT / "spec"
MANIFEST = json.loads((SPEC / "cases" / "manifest.json").read_text())

AUTH_URL = "https://gateway.test.invalid/oauth2/token"
GATEWAY = "https://gateway.test.invalid"

ENDPOINTS = {
    "chat.completions.create": f"{GATEWAY}/v1/chat/completions",
    "chat.completions.stream": f"{GATEWAY}/v1/chat/completions",
    "embeddings.create": f"{GATEWAY}/v1/embeddings",
    "images.generate": f"{GATEWAY}/v1/images/generations",
    "models.list": f"{GATEWAY}/v1/models",
    "models.mine": f"{GATEWAY}/v1/models/mine",
    "rerank.create": f"{GATEWAY}/v1/rerank",
    "usage.retrieve": f"{GATEWAY}/v1/usage/{{request_id}}",
    # The token exchange is scaffolding for every other case, so it is routed and dispatched
    # apart -- but it is listed here so the routability guard covers it too.
    "token.fetch": AUTH_URL,
}

CASES = MANIFEST["cases"]
CASE_IDS = [case["id"] for case in CASES]


def resolve(target: Any, path: str) -> Any:
    """Walk a dotted path, using attributes where they exist and indices for list segments."""
    current = target
    for segment in path.split("."):
        if segment.isdigit():
            current = current[int(segment)]
        elif hasattr(current, segment):
            current = getattr(current, segment)
        else:
            current = current[segment]
    return current


def canonical(value: Any) -> Any:
    """Reduce a parsed value to something comparable with plain JSON from the manifest."""
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, list):
        return [canonical(item) for item in value]
    return value


def responses_for(case: dict[str, Any]) -> list[dict[str, Any]]:
    """The responses a case serves, in order.

    Most cases describe one. A case that pins a retry describes several: the rejection, then what
    the reopened request gets. Normalised here so every runner and every integrity check sees one
    shape rather than two.
    """
    if "responses" in case:
        sequence: list[dict[str, Any]] = case["responses"]
        return sequence
    return [case["response"]]


def mock_response(spec: dict[str, Any]) -> httpx.Response:
    headers = dict(spec.get("headers", {}))

    if "sse_file" in spec:
        headers.setdefault("Content-Type", "text/event-stream")
        body = (SPEC / "fixtures" / spec["sse_file"]).read_bytes()
        return httpx.Response(spec["status"], content=body, headers=headers)

    body_text = (SPEC / "fixtures" / spec["body_file"]).read_text()
    headers.setdefault("Content-Type", "application/json")
    return httpx.Response(spec["status"], content=body_text.encode(), headers=headers)


def contract_client(config_kwargs: dict[str, str]) -> Axonium:
    """The client every case is replayed through.

    One constructor rather than six, because ``expect.requests`` counts requests and the number of
    requests a retryable failure produces IS the retry policy. Leaving the policy at the SDK's
    default is therefore part of the corpus rather than a detail of this harness -- see the
    manifest's ``$request_counts`` -- and a single place to build the client is what lets a test say
    so. Disabling retries here to make the error cases fast, which is a reasonable thing to want,
    would make a counted case fail on this file instead of on the SDK.
    """
    return Axonium(**config_kwargs)


def async_contract_client(config_kwargs: dict[str, str]) -> AsyncAxonium:
    """See :func:`contract_client`."""
    return AsyncAxonium(**config_kwargs)


@pytest.fixture(autouse=True)
def _token() -> None:
    respx.post(AUTH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "t",
                "token_type": "bearer",
                "expires_in": 300,
                "scope": "inference:read inference:stream",
            },
        )
    )


def route_for(case: dict[str, Any]) -> Any:
    operation = case["operation"]
    url = ENDPOINTS[operation]
    if operation == "usage.retrieve":
        # The id is part of the path here, not the body, so the route has to be built per case.
        url = url.format(request_id=case["request"]["request_id"])
    method = respx.get if operation.startswith(("models.", "usage.")) else respx.post

    sequence = [mock_response(spec) for spec in responses_for(case)]

    def serve(request: httpx.Request) -> httpx.Response:
        # The last response repeats rather than running out. An SDK that retries once too often
        # should fail on the request count, which says what it did, instead of on an exhausted
        # side_effect, which says only that the fixture list was too short.
        return sequence[min(len(route.calls), len(sequence) - 1)]

    route = method(url).mock(side_effect=serve)
    return route


def call_sync(client: Axonium, case: dict[str, Any]) -> Any:
    operation = case["operation"]
    request = case.get("request", {})

    if operation == "chat.completions.create":
        return client.chat.completions.create(**request)
    if operation == "chat.completions.stream":
        # Opening the stream is what sends the request, so a rejection that precedes the stream is
        # raised by __enter__ rather than by iteration -- which is exactly the difference between an
        # `error` case and a `stream_error` one, and the reason this branch has to exist. Without it
        # a streaming error case reached "unhandled operation", so the corpus could not express a
        # rejection at all. Opened *and* iterated, because an SDK that hands back a stream where a
        # status belongs must not pass by reading the refusal as an empty body.
        with client.chat.completions.stream(**request) as stream:
            return list(stream)
    if operation == "embeddings.create":
        return client.embeddings.create(**request)
    if operation == "images.generate":
        return client.images.generate(**request)
    if operation == "models.list":
        return client.models.list()
    if operation == "models.mine":
        return client.models.mine()
    if operation == "rerank.create":
        return client.rerank.create(**request)
    if operation == "usage.retrieve":
        return client.usage.retrieve(request["request_id"])
    raise AssertionError(f"unhandled operation {operation}")


async def call_async(client: AsyncAxonium, case: dict[str, Any]) -> Any:
    operation = case["operation"]
    request = case.get("request", {})

    if operation == "chat.completions.create":
        return await client.chat.completions.create(**request)
    if operation == "chat.completions.stream":
        # See call_sync.
        async with client.chat.completions.stream(**request) as stream:
            return [chunk async for chunk in stream]
    if operation == "embeddings.create":
        return await client.embeddings.create(**request)
    if operation == "images.generate":
        return await client.images.generate(**request)
    if operation == "models.list":
        return await client.models.list()
    if operation == "models.mine":
        return await client.models.mine()
    if operation == "rerank.create":
        return await client.rerank.create(**request)
    if operation == "usage.retrieve":
        return await client.usage.retrieve(request["request_id"])
    raise AssertionError(f"unhandled operation {operation}")


def assert_fields(result: Any, expected: dict[str, Any], case_id: str) -> None:
    for path, want in expected.items():
        got = canonical(resolve(result, path))
        assert got == want, f"{case_id}: {path} was {got!r}, expected {want!r}"


def assert_error(error: APIError, case: dict[str, Any]) -> None:
    expect = case["expect"]
    cid = case["id"]
    assert error.status == responses_for(case)[-1]["status"], cid
    assert error.type_suffix == expect["error_type_suffix"], cid
    assert error.retryable is expect["retryable"], cid
    if expect.get("has_request_id"):
        assert error.request_id, f"{cid}: no request_id, so a caller cannot correlate this"
    if expect.get("has_trace_id"):
        assert error.trace_id, f"{cid}: no trace_id, so a caller cannot correlate this"
    # Errors carry fields worth pinning too -- which budget a 429 exhausted, for one. Until this
    # existed, twelve error cases could assert a suffix and nothing about the envelope's contents.
    if "fields" in expect:
        assert_fields(error, expect["fields"], cid)


def assert_traffic(route: Any, stream: Any, case: dict[str, Any]) -> None:
    """How many requests reached the server, and whether the SDK admits to it.

    ``requests`` is the assertion that matters and the only one that would have caught the
    divergence: ``attempts`` is what the SDK reports, ``requests`` is what the server saw, and an
    SDK can be wrong about the first while the second is the fact.
    """
    expect = case["expect"]
    if "requests" in expect:
        assert route.call_count == expect["requests"], (
            f"{case['id']}: {route.call_count} requests reached the server, "
            f"expected {expect['requests']}"
        )
    if "attempts" in expect:
        assert stream.meta is not None, case["id"]
        assert stream.meta.attempts == expect["attempts"], case["id"]


def assert_usage(usage: Any, expected: dict[str, Any] | None, case_id: str) -> None:
    if expected is None:
        assert usage is None, f"{case_id}: expected no usage, got {usage!r}"
        return

    assert usage is not None, f"{case_id}: expected usage, got none"
    for field, want in expected.items():
        got = getattr(usage, field)
        assert got == want, f"{case_id}: usage.{field} was {got!r}, expected {want!r}"


TOKEN = [case for case in CASES if case["operation"] == "token.fetch"]
_RESOURCE = [case for case in CASES if case["operation"] != "token.fetch"]

NON_STREAMING = [case for case in _RESOURCE if case["expect"]["kind"] == "ok"]
STREAMING = [case for case in _RESOURCE if case["expect"]["kind"].startswith("stream")]
ERRORS = [case for case in _RESOURCE if case["expect"]["kind"] == "error"]


class TestNonStreamingCases:
    @respx.mock
    @pytest.mark.parametrize("case", NON_STREAMING, ids=[c["id"] for c in NON_STREAMING])
    def test_sync(self, case: dict[str, Any], config_kwargs: dict[str, str]) -> None:
        route_for(case)

        with contract_client(config_kwargs) as client:
            result = call_sync(client, case)

        assert_fields(result, case["expect"]["fields"], case["id"])

    @respx.mock
    @pytest.mark.parametrize("case", NON_STREAMING, ids=[c["id"] for c in NON_STREAMING])
    async def test_async(self, case: dict[str, Any], config_kwargs: dict[str, str]) -> None:
        route_for(case)

        async with async_contract_client(config_kwargs) as client:
            result = await call_async(client, case)

        assert_fields(result, case["expect"]["fields"], case["id"])


class TestStreamingCases:
    @respx.mock
    @pytest.mark.parametrize("case", STREAMING, ids=[c["id"] for c in STREAMING])
    def test_sync(self, case: dict[str, Any], config_kwargs: dict[str, str]) -> None:
        route = route_for(case)
        expect = case["expect"]

        with (
            contract_client(config_kwargs) as client,
            client.chat.completions.stream(**case["request"]) as stream,
        ):
            if expect["kind"] == "stream_error":
                with pytest.raises(StreamInterruptedError) as caught:
                    list(stream)
                assert caught.value.partial_content == expect["partial_content"]
                assert_traffic(route, stream, case)
                return

            chunks = list(stream)
            assert stream.content == expect["content"], case["id"]
            assert len(chunks) == expect["chunks"], case["id"]
            assert_usage(stream.usage(), expect["usage"], case["id"])
            # Compared as dicts because the manifest is the shared source of truth across three
            # languages; the typed surface is Python's own and is asserted separately. Absent on
            # every case but the two tool-call ones, and asserting the empty list elsewhere is what
            # stops a reassembler from inventing calls out of a stream that carried none.
            assembled = [call.model_dump() for call in stream.tool_calls]
            assert assembled == expect.get("tool_calls", []), case["id"]
            assert_traffic(route, stream, case)

    @respx.mock
    @pytest.mark.parametrize("case", STREAMING, ids=[c["id"] for c in STREAMING])
    async def test_async(self, case: dict[str, Any], config_kwargs: dict[str, str]) -> None:
        route = route_for(case)
        expect = case["expect"]

        async with (
            async_contract_client(config_kwargs) as client,
            client.chat.completions.stream(**case["request"]) as stream,
        ):
            if expect["kind"] == "stream_error":
                with pytest.raises(StreamInterruptedError) as caught:
                    [chunk async for chunk in stream]
                assert caught.value.partial_content == expect["partial_content"]
                assert_traffic(route, stream, case)
                return

            chunks = [chunk async for chunk in stream]
            assert stream.content == expect["content"], case["id"]
            assert len(chunks) == expect["chunks"], case["id"]
            assert_usage(stream.usage(), expect["usage"], case["id"])
            # Compared as dicts because the manifest is the shared source of truth across three
            # languages; the typed surface is Python's own and is asserted separately. Absent on
            # every case but the two tool-call ones, and asserting the empty list elsewhere is what
            # stops a reassembler from inventing calls out of a stream that carried none.
            assembled = [call.model_dump() for call in stream.tool_calls]
            assert assembled == expect.get("tool_calls", []), case["id"]
            assert_traffic(route, stream, case)


class TestErrorCases:
    """Recorded failures must map to the taxonomy the shared catalog declares.

    The correlation IDs are asserted too: an error a caller cannot take to the platform team is
    half an error.
    """

    @respx.mock
    @pytest.mark.parametrize("case", ERRORS, ids=[c["id"] for c in ERRORS])
    def test_sync(self, case: dict[str, Any], config_kwargs: dict[str, str]) -> None:
        route_for(case)

        with contract_client(config_kwargs) as client, pytest.raises(APIError) as caught:
            call_sync(client, case)

        assert_error(caught.value, case)

    @respx.mock
    @pytest.mark.parametrize("case", ERRORS, ids=[c["id"] for c in ERRORS])
    async def test_async(self, case: dict[str, Any], config_kwargs: dict[str, str]) -> None:
        route_for(case)

        async with async_contract_client(config_kwargs) as client:
            with pytest.raises(APIError) as caught:
                await call_async(client, case)

        assert_error(caught.value, case)


class TestManifestIntegrity:
    """The manifest is only useful if it stays well-formed and actually gets exercised."""

    def test_every_case_has_a_unique_id(self) -> None:
        assert len(CASE_IDS) == len(set(CASE_IDS))

    def test_every_case_is_executed_by_one_of_the_runners(self) -> None:
        # A case with an unrecognized kind would otherwise be silently skipped, leaving the
        # behavior it describes unverified while looking covered.
        executed = {case["id"] for case in NON_STREAMING + STREAMING + ERRORS + TOKEN}
        assert executed == set(CASE_IDS)

    def test_every_operation_is_routable(self) -> None:
        for case in CASES:
            assert case["operation"] in ENDPOINTS, case["id"]

    def test_every_referenced_fixture_exists(self) -> None:
        for case in CASES:
            for spec in responses_for(case):
                name = spec.get("body_file") or spec["sse_file"]
                assert (SPEC / "fixtures" / name).exists(), (
                    f"{case['id']} references missing {name}"
                )

    def test_no_fixture_is_orphaned(self) -> None:
        # An unreferenced fixture is either a missing case or dead weight; both are worth knowing.
        referenced = {
            spec.get("body_file") or spec["sse_file"]
            for case in CASES
            for spec in responses_for(case)
        }
        on_disk = {path.name for path in (SPEC / "fixtures").iterdir() if path.is_file()}
        assert on_disk == referenced

    def test_every_case_declares_where_its_bytes_came_from(self) -> None:
        """A case with no provenance is a case nobody can weigh.

        ``$recording`` claims every case says whether its bytes were recorded from a deployment or
        authored, and that claim went unheld: it used to carry the tally "21 of 25 cases", which
        nothing enforced and every bump was supposed to update. By v20 the file held 42. The rule is
        checked here instead, so a case added without saying where it came from fails rather than
        quietly weakening what the corpus is evidence *of*.
        """
        silent = [case["id"] for case in CASES if not case.get("$comment", "").strip()]
        assert not silent, f"cases that do not say where their bytes came from: {silent}"

    def test_a_counted_case_is_replayed_under_the_default_retry_policy(
        self, config_kwargs: dict[str, str]
    ) -> None:
        """``expect.requests`` counts requests, and what turns one failure into three is the policy.

        So the policy is part of the corpus, not a detail of this harness, and the manifest says so
        in ``$request_counts``. This is that sentence as a test, because a sentence is what the last
        several of these turned out to be: a fourth SDK's runner disables retries to keep its error
        cases fast -- a reasonable thing to want -- which sends one request and fails a counted case
        on its own configuration rather than on the SDK. Asserted only while a counted case exists,
        so it states a dependency rather than a preference.
        """
        counted = [case["id"] for case in CASES if "requests" in case["expect"]]
        if not counted:
            return

        default = RetryPolicy()
        with contract_client(config_kwargs) as client:
            assert client.retry_policy == default, (
                f"the contract client overrides the retry policy, so the request counts in "
                f"{counted} no longer measure the SDK"
            )
        async_client = async_contract_client(config_kwargs)
        assert async_client.retry_policy == default, counted

    def test_a_case_declaring_a_sequence_is_asserting_the_request_count(self) -> None:
        # A sequence exists to pin how many requests reach the server. Without that count the extra
        # responses are decoration: an SDK could serve the first, stop, and still pass.
        for case in CASES:
            if "responses" in case:
                assert "requests" in case["expect"], (
                    f"{case['id']} serves a sequence but asserts no request count"
                )


class TestTokenCases:
    """The token exchange itself, which every other case only uses as scaffolding.

    Deliberately a subset of what ``test_auth.py`` covers. Only what is observable by replaying
    recorded bytes belongs in a shared corpus: the shape that goes out and the two envelopes that
    come back. The timing and concurrency rules — refresh-ahead, single-flight, lifetime clamping —
    cannot be expressed against a recorded exchange and stay per-language, in all three SDKs.
    """

    @staticmethod
    def _route(case: dict[str, Any]) -> Any:
        # Registered before the autouse happy-path mock so it wins: respx matches in registration
        # order, and this case's whole point is that the token exchange is not the happy one.
        route = respx.post(AUTH_URL).mock(return_value=mock_response(responses_for(case)[0]))
        respx.get(f"{GATEWAY}/v1/models/mine").mock(
            return_value=httpx.Response(200, json={"object": "list", "data": []})
        )
        return route

    @respx.mock
    @pytest.mark.parametrize("case", TOKEN, ids=[c["id"] for c in TOKEN])
    def test_token_exchange(self, case: dict[str, Any], config_kwargs: dict[str, str]) -> None:
        expect = case["expect"]
        route = self._route(case)
        kwargs = {**config_kwargs}
        if "scope" in case["request"]:
            kwargs["scope"] = case["request"]["scope"]

        with Axonium(**kwargs) as client:
            if expect["kind"] == "ok":
                client.models.mine()
            elif expect["kind"] == "oauth_error":
                with pytest.raises(OAuthError) as caught:
                    client.models.mine()
                assert caught.value.error == expect["oauth_code"], case["id"]
            elif expect["kind"] == "error":
                with pytest.raises(APIError) as api:
                    client.models.mine()
                assert not isinstance(api.value, OAuthError), (
                    f"{case['id']}: a gateway failure typed as an OAuth2 outcome"
                )
                assert api.value.type_suffix == expect["error_type_suffix"], case["id"]
                assert api.value.retryable is expect["retryable"], case["id"]
                if expect.get("has_request_id"):
                    assert api.value.request_id, case["id"]
                if expect.get("has_trace_id"):
                    assert api.value.trace_id, case["id"]
            elif expect["kind"] == "auth_transport_error":
                with pytest.raises(AuthTransportError):
                    client.models.mine()
            else:
                raise AssertionError(f"unhandled token expectation {expect['kind']}")

        self._assert_request_shape(case, route)

    @staticmethod
    def _assert_request_shape(case: dict[str, Any], route: Any) -> None:
        """What the SDK sent, which is as much of the contract as what it received."""
        expect = case["expect"]
        shape_keys = ("request_form", "request_form_absent", "request_content_type")
        if not any(key in expect for key in shape_keys):
            return

        request = route.calls.last.request
        sent = dict(parse_qsl(request.read().decode()))

        for key, value in expect.get("request_form", {}).items():
            assert sent.get(key) == value, f"{case['id']}: form field {key}"
        for key in expect.get("request_form_absent", []):
            # Absent, not empty: some authorisation servers read an empty scope as "grant nothing".
            assert key not in sent, f"{case['id']}: {key} should not be sent at all"
        if "request_content_type" in expect:
            assert request.headers["Content-Type"].startswith(expect["request_content_type"])
