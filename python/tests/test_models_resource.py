from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from axonium import AsyncAxonium, Axonium
from axonium.errors import (
    APIError,
    ForbiddenError,
    TimeoutError,
    TransportError,
    UnknownModelError,
)

AUTH_URL = "https://auth.test.invalid/oauth2/token"
CATALOG_URL = "https://gateway.test.invalid/v1/models"
MINE_URL = "https://gateway.test.invalid/v1/models/mine"

CATALOG_BODY = {
    "object": "list",
    "data": [
        {
            "id": "llama3-8b-q4",
            "object": "model",
            "owned_by": "prometheus",
            "context_length": 8192,
            "family": "llama3",
            "quantization": "Q4_0",
            "modality": "text",
        },
        {"id": "embed-model", "object": "model", "modality": "embedding"},
    ],
}


@pytest.fixture(autouse=True)
def _token() -> Any:
    respx.post(AUTH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"access_token": "t", "token_type": "bearer", "expires_in": 300, "scope": ""},
        )
    )


class Caller:
    """Drives whichever client kind is under test through one identical surface."""

    def __init__(self, is_async: bool) -> None:
        self.is_async = is_async

    async def models_list(self, **settings: str) -> Any:
        if self.is_async:
            async with AsyncAxonium(**settings) as client:
                return await client.models.list(), client
        with Axonium(**settings) as client:
            return client.models.list(), client

    async def models_mine(self, **settings: str) -> Any:
        if self.is_async:
            async with AsyncAxonium(**settings) as client:
                return await client.models.mine(), client
        with Axonium(**settings) as client:
            return client.models.mine(), client


@pytest.fixture(params=[False, True], ids=["sync", "async"])
def caller(request: pytest.FixtureRequest) -> Caller:
    return Caller(bool(request.param))


class TestCatalog:
    @respx.mock
    async def test_lists_every_deployed_model(
        self, caller: Caller, config_kwargs: dict[str, str]
    ) -> None:
        respx.get(CATALOG_URL).mock(return_value=httpx.Response(200, json=CATALOG_BODY))

        models, _ = await caller.models_list(**config_kwargs)

        assert models.ids == ("llama3-8b-q4", "embed-model")
        assert len(models) == 2
        assert [model.id for model in models] == ["llama3-8b-q4", "embed-model"]

    @respx.mock
    async def test_exposes_the_fields_that_decide_how_a_model_can_be_called(
        self, caller: Caller, config_kwargs: dict[str, str]
    ) -> None:
        respx.get(CATALOG_URL).mock(return_value=httpx.Response(200, json=CATALOG_BODY))

        models, _ = await caller.models_list(**config_kwargs)
        model = models.get("llama3-8b-q4")

        assert model is not None
        assert model.context_length == 8192
        assert model.modality == "text"
        assert model.quantization == "Q4_0"

    @respx.mock
    async def test_lookup_is_case_sensitive_and_absent_ids_return_none(
        self, caller: Caller, config_kwargs: dict[str, str]
    ) -> None:
        # Model IDs must match exactly, both here and in the model:<id> scope they map to.
        respx.get(CATALOG_URL).mock(return_value=httpx.Response(200, json=CATALOG_BODY))

        models, _ = await caller.models_list(**config_kwargs)

        assert models.get("LLAMA3-8B-Q4") is None
        assert models.get("nonexistent") is None

    @respx.mock
    async def test_preserves_fields_this_sdk_does_not_model(
        self, caller: Caller, config_kwargs: dict[str, str]
    ) -> None:
        # A gateway that starts reporting more about a model must not have it silently dropped.
        body = {"object": "list", "data": [{"id": "m", "object": "model", "max_batch_size": 4}]}
        respx.get(CATALOG_URL).mock(return_value=httpx.Response(200, json=body))

        models, _ = await caller.models_list(**config_kwargs)

        assert models.data[0].model_dump()["max_batch_size"] == 4

    @respx.mock
    async def test_listing_the_public_catalog_sends_no_token(
        self, caller: Caller, config_kwargs: dict[str, str]
    ) -> None:
        # The endpoint is public, so listing it must not cost an authentication round trip.
        token_route = respx.post(AUTH_URL)
        catalog = respx.get(CATALOG_URL).mock(return_value=httpx.Response(200, json=CATALOG_BODY))

        await caller.models_list(**config_kwargs)

        assert "Authorization" not in catalog.calls.last.request.headers
        assert not token_route.called


class TestMine:
    @respx.mock
    async def test_returns_only_what_this_token_can_call(
        self, caller: Caller, config_kwargs: dict[str, str]
    ) -> None:
        body = {"object": "list", "data": [CATALOG_BODY["data"][0]]}
        mine = respx.get(MINE_URL).mock(return_value=httpx.Response(200, json=body))

        models, _ = await caller.models_mine(**config_kwargs)

        assert models.ids == ("llama3-8b-q4",)
        assert mine.calls.last.request.headers["Authorization"] == "Bearer t"

    @respx.mock
    async def test_a_token_with_no_model_grants_gets_an_empty_list_not_an_error(
        self, caller: Caller, config_kwargs: dict[str, str]
    ) -> None:
        # Holding inference:read conveys no model access by itself, and that is not an error.
        respx.get(MINE_URL).mock(return_value=httpx.Response(200, json={"object": "list"}))

        models, _ = await caller.models_mine(**config_kwargs)

        assert models.ids == ()
        assert len(models) == 0


class TestResponseMetadata:
    @respx.mock
    async def test_correlation_ids_are_attached_to_successful_responses(
        self, caller: Caller, config_kwargs: dict[str, str]
    ) -> None:
        # Correlating a slow but successful call matters as much as correlating a failed one.
        respx.get(CATALOG_URL).mock(
            return_value=httpx.Response(
                200,
                headers={"X-Request-ID": "req-1", "X-Trace-ID": "trace-1"},
                json=CATALOG_BODY,
            )
        )

        models, _ = await caller.models_list(**config_kwargs)

        assert models.meta is not None
        assert models.meta.request_id == "req-1"
        assert models.meta.trace_id == "trace-1"

    @respx.mock
    async def test_rate_limit_budget_is_visible_before_any_429(
        self, caller: Caller, config_kwargs: dict[str, str]
    ) -> None:
        respx.get(MINE_URL).mock(
            return_value=httpx.Response(
                200,
                headers={
                    "X-RateLimit-Limit-Requests": "60",
                    "X-RateLimit-Remaining-Requests": "42",
                },
                json={"object": "list", "data": []},
            )
        )

        models, client = await caller.models_mine(**config_kwargs)

        assert models.meta is not None
        assert models.meta.rate_limit is not None
        assert models.meta.rate_limit.remaining_requests == 42
        assert client.last_rate_limit is not None
        assert client.last_rate_limit.remaining_requests == 42

    @respx.mock
    async def test_last_rate_limit_is_none_when_no_response_reported_one(
        self, caller: Caller, config_kwargs: dict[str, str]
    ) -> None:
        respx.get(CATALOG_URL).mock(return_value=httpx.Response(200, json=CATALOG_BODY))

        _, client = await caller.models_list(**config_kwargs)

        assert client.last_rate_limit is None


class TestErrorMapping:
    @respx.mock
    async def test_problem_details_become_typed_errors(
        self, caller: Caller, config_kwargs: dict[str, str]
    ) -> None:
        respx.get(MINE_URL).mock(
            return_value=httpx.Response(
                400,
                headers={"X-Request-ID": "req-9"},
                json={
                    "type": "https://prometheus.internal/errors/unknown-model",
                    "title": "Unknown Model",
                    "status": 400,
                    "detail": "Model 'nope' is not registered.",
                    "request_id": "req-9",
                    "trace_id": "trace-9",
                },
            )
        )

        with pytest.raises(UnknownModelError) as caught:
            await caller.models_mine(**config_kwargs)

        assert caught.value.request_id == "req-9"
        assert caught.value.trace_id == "trace-9"
        assert caught.value.retryable is False

    @respx.mock
    async def test_rate_limit_headers_are_attached_to_errors_too(
        self, caller: Caller, config_kwargs: dict[str, str]
    ) -> None:
        respx.get(MINE_URL).mock(
            return_value=httpx.Response(
                403,
                headers={"X-RateLimit-Remaining-Requests": "0"},
                json={"type": "https://prometheus.internal/errors/forbidden", "status": 403},
            )
        )

        with pytest.raises(ForbiddenError) as caught:
            await caller.models_mine(**config_kwargs)

        assert caught.value.rate_limit is not None
        assert caught.value.rate_limit.remaining_requests == 0

    @respx.mock
    async def test_a_non_problem_json_error_body_still_raises_a_typed_error(
        self, caller: Caller, config_kwargs: dict[str, str]
    ) -> None:
        # A proxy returning an HTML 502 must not surface as a JSON parsing crash.
        respx.get(MINE_URL).mock(return_value=httpx.Response(502, text="<html>bad gateway</html>"))

        with pytest.raises(APIError) as caught:
            await caller.models_mine(**config_kwargs)

        assert caught.value.status == 502
        assert caught.value.type_suffix is None

    @respx.mock
    async def test_a_non_json_success_body_is_reported_as_an_api_error(
        self, caller: Caller, config_kwargs: dict[str, str]
    ) -> None:
        respx.get(CATALOG_URL).mock(return_value=httpx.Response(200, text="not json"))

        with pytest.raises(APIError, match="not JSON"):
            await caller.models_list(**config_kwargs)


class TestTransportFailures:
    @respx.mock
    async def test_a_connection_failure_becomes_a_transport_error(
        self, caller: Caller, config_kwargs: dict[str, str]
    ) -> None:
        respx.get(CATALOG_URL).mock(side_effect=httpx.ConnectError("no route to host"))

        with pytest.raises(TransportError):
            await caller.models_list(**config_kwargs)

    @respx.mock
    async def test_a_timeout_warns_about_duplicate_generation(
        self, caller: Caller, config_kwargs: dict[str, str]
    ) -> None:
        # The backend may still be working; a retry starts a second billable generation.
        respx.get(CATALOG_URL).mock(side_effect=httpx.ReadTimeout("too slow"))

        with pytest.raises(TimeoutError, match="second billable generation"):
            await caller.models_list(**config_kwargs)


class TestClientConstruction:
    def test_reads_configuration_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch, config_kwargs: dict[str, str]
    ) -> None:
        for key, value in config_kwargs.items():
            monkeypatch.setenv(f"AXONIUM_{key.upper()}", value)

        with Axonium() as client:
            assert client.config.client_id == "test-client"

    def test_missing_configuration_fails_at_construction(self) -> None:
        from axonium.errors import ConfigurationError

        with pytest.raises(ConfigurationError, match="AXONIUM_CLIENT_ID"):
            Axonium(auth_base_url="https://a.invalid", gateway_base_url="https://g.invalid")

    @respx.mock
    async def test_no_token_is_fetched_until_a_request_needs_one(
        self, config_kwargs: dict[str, str]
    ) -> None:
        token_route = respx.post(AUTH_URL)

        with Axonium(**config_kwargs) as client:
            assert client.token_claims() is None

        assert not token_route.called
