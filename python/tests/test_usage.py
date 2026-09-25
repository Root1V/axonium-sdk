"""The per-request usage lookup.

The recorded contract cases cover the shapes the gateway returns. What they cannot cover is the id
going *out*: a manifest id is always a well-formed UUID, so no recorded case can tell an escaped
path from an unescaped one. That is covered here with constructed input, said plainly.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from axonium import Axonium, NotFoundError, RequestUsage

GATEWAY = "https://gateway.test.invalid"
AUTH_URL = "https://gateway.test.invalid/oauth2/token"

ROW = {
    "request_id": "a0f3ec1b",
    "model": "qwen3-0.6b",
    "usage": {"prompt_tokens": 10, "prompt_tokens_details": {"cached_tokens": 9}},
    "interrupted": False,
    "termination_reason": "complete",
}


@pytest.fixture(autouse=True)
def _token() -> None:
    respx.post(AUTH_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": "t", "token_type": "bearer", "expires_in": 300}
        )
    )


class TestTheIdOnTheWire:
    @respx.mock
    async def test_a_separator_in_the_id_cannot_address_another_route(
        self, config_kwargs: dict[str, str]
    ) -> None:
        # An id a caller stored, mistyped, or built by concatenation can contain a slash. Left
        # unescaped it would silently address a different path -- the aggregate usage route, say,
        # which needs admin:read and would come back as a confusing 403 rather than a 404.
        route = respx.get(url__regex=rf"{GATEWAY}/v1/usage/.*").mock(
            return_value=httpx.Response(404, json={"type": "x/not-found", "status": 404})
        )

        with Axonium(**config_kwargs) as client, pytest.raises(NotFoundError):
            client.usage.retrieve("../usage?limit=1000")

        # What matters is that no *separator* survives unescaped. A literal ".." is harmless on
        # its own -- it only traverses when followed by a slash, and the slash is what gets
        # escaped -- so asserting its absence would be asserting the wrong thing.
        # raw_path, not path: httpx decodes the latter, so an escaped and an unescaped id look
        # identical there and the assertion would pass either way.
        raw = route.calls.last.request.url.raw_path.decode()
        assert raw.startswith("/v1/usage/")
        assert raw.count("/") == 3, f"a separator survived onto the wire: {raw}"
        assert "?" not in raw, f"a query separator survived onto the wire: {raw}"

    @respx.mock
    async def test_it_reads_the_lifted_cache_count(self, config_kwargs: dict[str, str]) -> None:
        # The row nests the cached count exactly as an inference response does, so the same lift
        # has to happen here. Reporting None would tell a caller nobody measured the cache on a
        # row where somebody did.
        respx.get(f"{GATEWAY}/v1/usage/a0f3ec1b").mock(return_value=httpx.Response(200, json=ROW))

        with Axonium(**config_kwargs) as client:
            row = client.usage.retrieve("a0f3ec1b")

        assert row.usage is not None
        assert row.usage.cache_read_tokens == 9

    @respx.mock
    async def test_an_unknown_termination_reason_is_not_a_parse_failure(
        self, config_kwargs: dict[str, str]
    ) -> None:
        # The platform proposed a fourth value this week and withdrew it. The next one may not be
        # withdrawn, and a closed enum would turn it into an exception for a caller who only
        # wanted the token counts.
        body = {**ROW, "termination_reason": "something_invented_next_quarter"}
        respx.get(f"{GATEWAY}/v1/usage/a0f3ec1b").mock(return_value=httpx.Response(200, json=body))

        with Axonium(**config_kwargs) as client:
            row = client.usage.retrieve("a0f3ec1b")

        assert row.termination_reason == "something_invented_next_quarter"


class TestRequestKindStaysOpen:
    """The field must not become an enum, and the doc must not enumerate a stale set.

    The platform warned us that `predict` was arriving and asked whether our types enumerate the
    kinds, because three consumers break the day one does. They do not -- but all three docstrings
    listed `"chat", "embeddings", "images"`, two of them pluralised wrongly and none mentioning
    `rerank`, which this SDK shipped itself. Measured values as of 2026-09-25: chat, embedding,
    rerank, image, predict.
    """

    def test_an_unseen_kind_parses_rather_than_failing(self) -> None:
        row = RequestUsage.model_validate(
            {"request_id": "r", "model": "m", "request_kind": "a-kind-invented-tomorrow"}
        )

        assert row.request_kind == "a-kind-invented-tomorrow"

    def test_the_field_is_not_constrained_to_a_set(self) -> None:
        # A Literal or Enum annotation here would make every new endpoint a parse error for every
        # SDK that predates it. This asserts the absence of that, which is the whole design.
        annotation = str(RequestUsage.model_fields["request_kind"].annotation)

        assert "Literal" not in annotation and "Enum" not in annotation, annotation
