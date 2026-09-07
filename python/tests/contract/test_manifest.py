"""Replays every case in ``spec/cases/manifest.json`` against the Python SDK.

The manifest is language-neutral and the Go and Rust SDKs will replay the same file, so a
behavioral difference between the SDKs shows up as a failing case here rather than as a surprise
in production. Adding a case is how a new behavior becomes binding on all three.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from axonium import AsyncAxonium, Axonium
from axonium.errors import StreamInterruptedError

REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC = REPO_ROOT / "spec"
MANIFEST = json.loads((SPEC / "cases" / "manifest.json").read_text())

AUTH_URL = "https://auth.test.invalid/oauth2/token"
GATEWAY = "https://gateway.test.invalid"

ENDPOINTS = {
    "chat.completions.create": f"{GATEWAY}/v1/chat/completions",
    "chat.completions.stream": f"{GATEWAY}/v1/chat/completions",
    "embeddings.create": f"{GATEWAY}/v1/embeddings",
    "images.generate": f"{GATEWAY}/v1/images/generations",
    "models.list": f"{GATEWAY}/v1/models",
    "models.mine": f"{GATEWAY}/v1/models/mine",
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


def mock_response(case: dict[str, Any]) -> httpx.Response:
    spec = case["response"]
    headers = dict(spec.get("headers", {}))

    if "sse_file" in spec:
        headers.setdefault("Content-Type", "text/event-stream")
        body = (SPEC / "fixtures" / spec["sse_file"]).read_bytes()
        return httpx.Response(spec["status"], content=body, headers=headers)

    body_text = (SPEC / "fixtures" / spec["body_file"]).read_text()
    headers.setdefault("Content-Type", "application/json")
    return httpx.Response(spec["status"], content=body_text.encode(), headers=headers)


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
    url = ENDPOINTS[case["operation"]]
    method = respx.get if case["operation"].startswith("models.") else respx.post
    return method(url).mock(return_value=mock_response(case))


def call_sync(client: Axonium, case: dict[str, Any]) -> Any:
    operation = case["operation"]
    request = case.get("request", {})

    if operation == "chat.completions.create":
        return client.chat.completions.create(**request)
    if operation == "embeddings.create":
        return client.embeddings.create(**request)
    if operation == "images.generate":
        return client.images.generate(**request)
    if operation == "models.list":
        return client.models.list()
    if operation == "models.mine":
        return client.models.mine()
    raise AssertionError(f"unhandled operation {operation}")


async def call_async(client: AsyncAxonium, case: dict[str, Any]) -> Any:
    operation = case["operation"]
    request = case.get("request", {})

    if operation == "chat.completions.create":
        return await client.chat.completions.create(**request)
    if operation == "embeddings.create":
        return await client.embeddings.create(**request)
    if operation == "images.generate":
        return await client.images.generate(**request)
    if operation == "models.list":
        return await client.models.list()
    if operation == "models.mine":
        return await client.models.mine()
    raise AssertionError(f"unhandled operation {operation}")


def assert_fields(result: Any, expected: dict[str, Any], case_id: str) -> None:
    for path, want in expected.items():
        got = canonical(resolve(result, path))
        assert got == want, f"{case_id}: {path} was {got!r}, expected {want!r}"


def assert_usage(usage: Any, expected: dict[str, Any] | None, case_id: str) -> None:
    if expected is None:
        assert usage is None, f"{case_id}: expected no usage, got {usage!r}"
        return

    assert usage is not None, f"{case_id}: expected usage, got none"
    for field, want in expected.items():
        got = getattr(usage, field)
        assert got == want, f"{case_id}: usage.{field} was {got!r}, expected {want!r}"


NON_STREAMING = [case for case in CASES if case["expect"]["kind"] == "ok"]
STREAMING = [case for case in CASES if case["expect"]["kind"].startswith("stream")]


class TestNonStreamingCases:
    @respx.mock
    @pytest.mark.parametrize("case", NON_STREAMING, ids=[c["id"] for c in NON_STREAMING])
    def test_sync(self, case: dict[str, Any], config_kwargs: dict[str, str]) -> None:
        route_for(case)

        with Axonium(**config_kwargs) as client:
            result = call_sync(client, case)

        assert_fields(result, case["expect"]["fields"], case["id"])

    @respx.mock
    @pytest.mark.parametrize("case", NON_STREAMING, ids=[c["id"] for c in NON_STREAMING])
    async def test_async(self, case: dict[str, Any], config_kwargs: dict[str, str]) -> None:
        route_for(case)

        async with AsyncAxonium(**config_kwargs) as client:
            result = await call_async(client, case)

        assert_fields(result, case["expect"]["fields"], case["id"])


class TestStreamingCases:
    @respx.mock
    @pytest.mark.parametrize("case", STREAMING, ids=[c["id"] for c in STREAMING])
    def test_sync(self, case: dict[str, Any], config_kwargs: dict[str, str]) -> None:
        route_for(case)
        expect = case["expect"]

        with (
            Axonium(**config_kwargs) as client,
            client.chat.completions.stream(**case["request"]) as stream,
        ):
            if expect["kind"] == "stream_error":
                with pytest.raises(StreamInterruptedError) as caught:
                    list(stream)
                assert caught.value.partial_content == expect["partial_content"]
                return

            chunks = list(stream)
            assert stream.content == expect["content"], case["id"]
            assert len(chunks) == expect["chunks"], case["id"]
            assert_usage(stream.usage(), expect["usage"], case["id"])

    @respx.mock
    @pytest.mark.parametrize("case", STREAMING, ids=[c["id"] for c in STREAMING])
    async def test_async(self, case: dict[str, Any], config_kwargs: dict[str, str]) -> None:
        route_for(case)
        expect = case["expect"]

        async with (
            AsyncAxonium(**config_kwargs) as client,
            client.chat.completions.stream(**case["request"]) as stream,
        ):
            if expect["kind"] == "stream_error":
                with pytest.raises(StreamInterruptedError) as caught:
                    [chunk async for chunk in stream]
                assert caught.value.partial_content == expect["partial_content"]
                return

            chunks = [chunk async for chunk in stream]
            assert stream.content == expect["content"], case["id"]
            assert len(chunks) == expect["chunks"], case["id"]
            assert_usage(stream.usage(), expect["usage"], case["id"])


class TestManifestIntegrity:
    """The manifest is only useful if it stays well-formed and actually gets exercised."""

    def test_every_case_has_a_unique_id(self) -> None:
        assert len(CASE_IDS) == len(set(CASE_IDS))

    def test_every_case_is_executed_by_one_of_the_runners(self) -> None:
        # A case with an unrecognized kind would otherwise be silently skipped, leaving the
        # behavior it describes unverified while looking covered.
        executed = {case["id"] for case in NON_STREAMING + STREAMING}
        assert executed == set(CASE_IDS)

    def test_every_operation_is_routable(self) -> None:
        for case in CASES:
            assert case["operation"] in ENDPOINTS, case["id"]

    def test_every_referenced_fixture_exists(self) -> None:
        for case in CASES:
            spec = case["response"]
            name = spec.get("body_file") or spec["sse_file"]
            assert (SPEC / "fixtures" / name).exists(), f"{case['id']} references missing {name}"

    def test_no_fixture_is_orphaned(self) -> None:
        # An unreferenced fixture is either a missing case or dead weight; both are worth knowing.
        referenced = {
            case["response"].get("body_file") or case["response"]["sse_file"] for case in CASES
        }
        on_disk = {path.name for path in (SPEC / "fixtures").iterdir() if path.is_file()}
        assert on_disk == referenced
