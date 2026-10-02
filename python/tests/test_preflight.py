from __future__ import annotations

import pathlib

import httpx
import pytest
import respx

import axonium
from axonium import AsyncAxonium, Axonium
from axonium.errors import ModalityMismatchError
from axonium.models.catalog import ModelList
from axonium.preflight import check_model

AUTH_URL = "https://gateway.test.invalid/oauth2/token"
CATALOG_URL = "https://gateway.test.invalid/v1/models"
CHAT_URL = "https://gateway.test.invalid/v1/chat/completions"
EMBED_URL = "https://gateway.test.invalid/v1/embeddings"
IMAGE_URL = "https://gateway.test.invalid/v1/images/generations"

CATALOG_BODY = {
    "object": "list",
    "data": [
        {"id": "chat-model", "object": "model", "modality": "text"},
        {"id": "vision-model", "object": "model", "modality": "vision"},
        {"id": "embed-model", "object": "model", "modality": "embedding"},
        {"id": "image-model", "object": "model", "modality": "image"},
        {"id": "rerank-model", "object": "model", "modality": "rerank"},
        {"id": "clf-model", "object": "model", "modality": "classification"},
        {"id": "zero-shot-model", "object": "model", "modality": "zero_shot"},
        {"id": "decision-model", "object": "model", "modality": "typed_decision"},
        {"id": "mystery-model", "object": "model", "modality": "hologram"},
        {"id": "unreported-model", "object": "model"},
    ],
}

COMPLETION = {"choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}}]}


def catalog() -> ModelList:
    return ModelList.model_validate(CATALOG_BODY)


@pytest.fixture(autouse=True)
def _token() -> None:
    respx.post(AUTH_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": "t", "token_type": "bearer", "expires_in": 300}
        )
    )


class TestCheckModel:
    @pytest.mark.parametrize(
        ("model", "endpoint"),
        [
            ("chat-model", "chat"),
            ("vision-model", "chat"),
            ("embed-model", "embeddings"),
            ("image-model", "images"),
            ("rerank-model", "rerank"),
            # All three pass-through modalities share one endpoint, which is also why they share
            # one rate-limit budget.
            ("clf-model", "predict"),
            ("zero-shot-model", "predict"),
            ("decision-model", "predict"),
        ],
    )
    def test_accepts_a_model_the_endpoint_can_serve(self, model: str, endpoint: str) -> None:
        check_model(catalog(), model, endpoint=endpoint)

    def test_rejects_chat_on_an_embedding_model(self) -> None:
        # The pairing this check was built for. The gateway used to answer 200 with degenerate
        # billable output here; since RM-66 it refuses too, so what this saves is the request and
        # the rate-limit unit rather than a wasted generation.
        with pytest.raises(ModalityMismatchError, match="embedding"):
            check_model(catalog(), "embed-model", endpoint="chat")

    @pytest.mark.parametrize(
        ("model", "endpoint"),
        [
            ("chat-model", "embeddings"),
            ("chat-model", "images"),
            ("image-model", "chat"),
            ("vision-model", "embeddings"),
            # Every one of these was ALLOWED until the known-modality set was derived from the
            # rows instead of kept by hand. The first two are the rerank gap that shipped: the
            # endpoint passed "rerank" as its accepted set, nothing knew "rerank" was a modality,
            # and so the check returned before it could compare anything.
            ("rerank-model", "chat"),
            ("chat-model", "rerank"),
            ("clf-model", "chat"),
            ("chat-model", "predict"),
            ("embed-model", "predict"),
            ("rerank-model", "predict"),
            ("decision-model", "rerank"),
        ],
    )
    def test_rejects_every_other_wrong_pairing(self, model: str, endpoint: str) -> None:
        with pytest.raises(ModalityMismatchError):
            check_model(catalog(), model, endpoint=endpoint)

    def test_a_model_absent_from_the_catalog_is_left_to_the_gateway(self) -> None:
        """Absence is not evidence, so the preflight stays quiet and the request goes.

        This asserted the opposite, and was right when written: the catalog was the platform's
        full public list, so a model missing from it was a typo and refusing locally only saved a
        round trip. Since PRM-167 the catalog holds only the models this token has a grant for,
        which gives absence two causes the SDK cannot tell apart — not registered, which the
        gateway answers as ``400 unknown-model``, and not granted, which it answers as ``403
        forbidden``.

        Raising ``UnknownModelError`` for the second told a caller to check a name that was
        spelled correctly, and pre-empted the ``403`` whose entire job is to name the missing
        scope. A typo now costs one request; a missing grant now gets diagnosed. That is the
        trade, and it is the right way round.
        """
        check_model(catalog(), "chat-modle", endpoint="chat")

    def test_a_case_mismatch_is_also_left_to_the_gateway(self) -> None:
        # Model IDs are case-sensitive and the gateway says so accurately. This SDK can no longer
        # tell a case mismatch from an ungranted model, so it says nothing either way.
        check_model(catalog(), "CHAT-MODEL", endpoint="chat")

    def test_a_visible_model_with_the_wrong_modality_is_still_refused(self) -> None:
        """What the preflight can still prove, and what it is now worth.

        A model the catalog *does* show carries its modality, so a mismatch is a fact rather than an
        inference, and refusing it locally costs nothing and saves a round trip.

        **The premise this test was written on has expired, and the assertion moved with it.** It
        matched on the word ``billable``, because the gateway used to answer
        ``/v1/chat/completions`` with an embedding model and bill for the nonsense. RM-66 closed
        that; all six wrong-modality combinations measured live now answer ``400
        modality-mismatch``. Nothing went red when the premise died --- the code was still correct
        and the message was still produced --- which is why a mutation could never have found this
        and only a live measurement did. What is asserted now is that the message tells the caller
        the gateway refuses it too, so nobody reads a local refusal as the only thing standing
        between them and a bill.
        """
        with pytest.raises(ModalityMismatchError, match="refuses this combination too"):
            check_model(catalog(), "embed-model", endpoint="chat")

    def test_allows_a_modality_this_sdk_does_not_know(self) -> None:
        # A guard rail that rejected valid requests after the platform added a modality would be
        # worse than no guard rail.
        check_model(catalog(), "mystery-model", endpoint="chat")

    def test_allows_a_model_whose_modality_was_not_reported(self) -> None:
        check_model(catalog(), "unreported-model", endpoint="embeddings")

    def test_does_nothing_without_a_catalog(self) -> None:
        check_model(None, "anything", endpoint="chat")

    def test_does_nothing_for_an_unrecognized_endpoint(self) -> None:
        check_model(catalog(), "embed-model", endpoint="future-endpoint")


class TestDisabledByDefault:
    @respx.mock
    def test_no_catalog_request_is_made(self, config_kwargs: dict[str, str]) -> None:
        # The SDK makes no request the caller did not ask for.
        catalog_route = respx.get(CATALOG_URL)
        respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=COMPLETION))

        with Axonium(**config_kwargs) as client:
            client.chat.completions.create(
                model="embed-model", messages=[{"role": "user", "content": "hi"}]
            )

        assert not catalog_route.called

    @respx.mock
    def test_a_wrong_pairing_still_reaches_the_gateway(self, config_kwargs: dict[str, str]) -> None:
        route = respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=COMPLETION))

        with Axonium(**config_kwargs) as client:
            client.chat.completions.create(
                model="embed-model", messages=[{"role": "user", "content": "hi"}]
            )

        assert route.called


class TestEnabled:
    @respx.mock
    async def test_blocks_chat_on_an_embedding_model_before_sending(
        self, config_kwargs: dict[str, str]
    ) -> None:
        respx.get(CATALOG_URL).mock(return_value=httpx.Response(200, json=CATALOG_BODY))
        chat = respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=COMPLETION))

        with (
            Axonium(verify_modality=True, **config_kwargs) as client,
            pytest.raises(ModalityMismatchError),
        ):
            client.chat.completions.create(
                model="embed-model", messages=[{"role": "user", "content": "hi"}]
            )

        assert not chat.called, "the request must not have been billed"

    @respx.mock
    async def test_blocks_the_async_path_too(self, config_kwargs: dict[str, str]) -> None:
        respx.get(CATALOG_URL).mock(return_value=httpx.Response(200, json=CATALOG_BODY))
        chat = respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=COMPLETION))

        async with AsyncAxonium(verify_modality=True, **config_kwargs) as client:
            with pytest.raises(ModalityMismatchError):
                await client.chat.completions.create(
                    model="embed-model", messages=[{"role": "user", "content": "hi"}]
                )

        assert not chat.called

    @respx.mock
    def test_blocks_a_sync_stream(self, config_kwargs: dict[str, str]) -> None:
        respx.get(CATALOG_URL).mock(return_value=httpx.Response(200, json=CATALOG_BODY))
        chat = respx.post(CHAT_URL)

        with (
            Axonium(verify_modality=True, **config_kwargs) as client,
            pytest.raises(ModalityMismatchError),
        ):
            client.chat.completions.stream(
                model="embed-model", messages=[{"role": "user", "content": "hi"}]
            )

        assert not chat.called

    @respx.mock
    async def test_blocks_an_async_stream_when_it_is_entered(
        self, config_kwargs: dict[str, str]
    ) -> None:
        # The method that builds an async stream cannot await, so its check runs at the point the
        # request would actually be sent.
        respx.get(CATALOG_URL).mock(return_value=httpx.Response(200, json=CATALOG_BODY))
        chat = respx.post(CHAT_URL)

        async with AsyncAxonium(verify_modality=True, **config_kwargs) as client:
            stream = client.chat.completions.stream(
                model="embed-model", messages=[{"role": "user", "content": "hi"}]
            )
            with pytest.raises(ModalityMismatchError):
                async with stream:
                    pass

        assert not chat.called

    @respx.mock
    def test_blocks_embeddings_and_images_too(self, config_kwargs: dict[str, str]) -> None:
        respx.get(CATALOG_URL).mock(return_value=httpx.Response(200, json=CATALOG_BODY))
        embed = respx.post(EMBED_URL)
        image = respx.post(IMAGE_URL)

        with Axonium(verify_modality=True, **config_kwargs) as client:
            with pytest.raises(ModalityMismatchError):
                client.embeddings.create(model="chat-model", input="hi")
            with pytest.raises(ModalityMismatchError):
                client.images.generate(model="chat-model", prompt="a cat")

        assert not embed.called
        assert not image.called

    @respx.mock
    def test_lets_a_correct_pairing_through(self, config_kwargs: dict[str, str]) -> None:
        respx.get(CATALOG_URL).mock(return_value=httpx.Response(200, json=CATALOG_BODY))
        chat = respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=COMPLETION))

        with Axonium(verify_modality=True, **config_kwargs) as client:
            result = client.chat.completions.create(
                model="chat-model", messages=[{"role": "user", "content": "hi"}]
            )

        assert result.content == "hi"
        assert chat.called


class TestCatalogCaching:
    @respx.mock
    def test_the_catalog_is_fetched_once_per_client(self, config_kwargs: dict[str, str]) -> None:
        catalog_route = respx.get(CATALOG_URL).mock(
            return_value=httpx.Response(200, json=CATALOG_BODY)
        )
        respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=COMPLETION))

        with Axonium(verify_modality=True, **config_kwargs) as client:
            for _ in range(3):
                client.chat.completions.create(
                    model="chat-model", messages=[{"role": "user", "content": "hi"}]
                )

        assert catalog_route.call_count == 1

    @respx.mock
    def test_an_earlier_listing_is_reused_rather_than_refetched(
        self, config_kwargs: dict[str, str]
    ) -> None:
        # A caller following the documented pattern of listing at startup pays nothing extra.
        catalog_route = respx.get(CATALOG_URL).mock(
            return_value=httpx.Response(200, json=CATALOG_BODY)
        )
        respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=COMPLETION))

        with Axonium(verify_modality=True, **config_kwargs) as client:
            client.models.list()
            client.chat.completions.create(
                model="chat-model", messages=[{"role": "user", "content": "hi"}]
            )

        assert catalog_route.call_count == 1

    @respx.mock
    async def test_the_async_client_caches_the_catalog_too(
        self, config_kwargs: dict[str, str]
    ) -> None:
        catalog_route = respx.get(CATALOG_URL).mock(
            return_value=httpx.Response(200, json=CATALOG_BODY)
        )
        respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=COMPLETION))

        async with AsyncAxonium(verify_modality=True, **config_kwargs) as client:
            for _ in range(3):
                await client.chat.completions.create(
                    model="chat-model", messages=[{"role": "user", "content": "hi"}]
                )

        assert catalog_route.call_count == 1


class TestDegradation:
    @respx.mock
    def test_an_unreachable_catalog_does_not_fail_the_request(
        self, config_kwargs: dict[str, str]
    ) -> None:
        # A guard rail that broke inference whenever the catalog endpoint was unhappy would be a
        # worse trade than the mistake it prevents.
        respx.get(CATALOG_URL).mock(side_effect=httpx.ConnectError("catalog down"))
        chat = respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=COMPLETION))

        with Axonium(verify_modality=True, **config_kwargs) as client:
            result = client.chat.completions.create(
                model="embed-model", messages=[{"role": "user", "content": "hi"}]
            )

        assert result.content == "hi"
        assert chat.called

    @respx.mock
    async def test_the_async_path_degrades_the_same_way(
        self, config_kwargs: dict[str, str]
    ) -> None:
        respx.get(CATALOG_URL).mock(return_value=httpx.Response(500, json={}))
        chat = respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=COMPLETION))

        async with AsyncAxonium(verify_modality=True, **config_kwargs) as client:
            result = await client.chat.completions.create(
                model="embed-model", messages=[{"role": "user", "content": "hi"}]
            )

        assert result.content == "hi"
        assert chat.called

    @respx.mock
    def test_a_retry_is_not_spent_retrying_the_catalog(self, config_kwargs: dict[str, str]) -> None:
        from axonium import RetryPolicy

        catalog_route = respx.get(CATALOG_URL).mock(side_effect=httpx.ConnectError("down"))
        respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=COMPLETION))

        policy = RetryPolicy(initial_backoff=0.0, max_backoff=0.0, jitter=False)
        with Axonium(verify_modality=True, retry=policy, **config_kwargs) as client:
            client.chat.completions.create(
                model="chat-model", messages=[{"role": "user", "content": "hi"}]
            )
            client.chat.completions.create(
                model="chat-model", messages=[{"role": "user", "content": "hi"}]
            )

        # One attempt per call, and no cached failure that would suppress a later recovery.
        assert catalog_route.call_count == 2


def test_config_flag_reads_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, config_kwargs: dict[str, str]
) -> None:
    monkeypatch.setenv("AXONIUM_VERIFY_MODALITY", "true")

    from axonium import AxoniumConfig

    assert AxoniumConfig(**config_kwargs).verify_modality is True


def test_every_endpoint_that_calls_the_preflight_has_a_modality_mapping() -> None:
    """Every ``_preflight(model, "x")`` in the package must have an ``"x"`` row.

    **This test used to assert its own answer** --- ``set(ENDPOINT_MODALITIES) == {"chat",
    "embeddings", "images"}`` --- which is not the invariant its name claims. A literal typed into a
    test cannot notice a new call site, so when :meth:`Rerank.create` shipped with
    ``_preflight(model, "rerank")`` and no ``"rerank"`` row, this test passed. :func:`check_model`
    returns silently for an endpoint it has no row for, so the call refused nothing --- including
    the pairing its own docstring promised to catch --- and the one test standing guard over that
    had been given the wrong answer key.

    It now reads the call sites out of the source. A new endpoint whose row is missing fails here,
    and so does a row that exists for no endpoint, which is how a mapping goes stale in the other
    direction.
    """
    import re

    from axonium.preflight import ENDPOINT_MODALITIES

    package = pathlib.Path(axonium.__file__).parent
    calls = re.compile(r"""_preflight\(\s*[^,()]+,\s*["']([a-z_.]+)["']""")

    called: dict[str, set[str]] = {}
    for source in package.rglob("*.py"):
        for endpoint in calls.findall(source.read_text()):
            called.setdefault(endpoint, set()).add(source.relative_to(package).as_posix())

    assert called, "found no _preflight call sites; the pattern stopped matching, not the code"

    missing = {e: sorted(w) for e, w in called.items() if e not in ENDPOINT_MODALITIES}
    assert not missing, (
        f"these endpoints call the preflight with no row in ENDPOINT_MODALITIES, so the check "
        f"silently refuses nothing for them: {missing}"
    )

    unused = set(ENDPOINT_MODALITIES) - set(called)
    assert not unused, (
        f"these rows exist for no caller, so whatever they claim to guard is unguarded: "
        f"{sorted(unused)}"
    )

    assert all(isinstance(v, frozenset) and v for v in ENDPOINT_MODALITIES.values())


def test_the_known_modality_set_is_derived_from_the_endpoint_rows() -> None:
    """A modality only one endpoint accepts is still *known*, or that endpoint has no check.

    :func:`check_model` ignores a modality outside the union of the rows, which is deliberate --- a
    guard rail that rejected valid requests each time the platform added a modality would be worse
    than none. The consequence is that the union has to be derived rather than kept by hand: a
    second hand-kept list is how ``rerank`` ended up accepted everywhere. Go and Rust had exactly
    two lists and the same bug; both now derive theirs too.
    """
    from axonium.preflight import ENDPOINT_MODALITIES

    for endpoint, accepted in ENDPOINT_MODALITIES.items():
        for modality in accepted:
            catalog_with = ModelList.model_validate(
                {"object": "list", "data": [{"id": "m", "object": "model", "modality": modality}]}
            )
            # Accepted where it belongs...
            check_model(catalog_with, "m", endpoint=endpoint)
            # ...and refused everywhere it does not, which only holds if it is in the known set.
            for other, others_accepted in ENDPOINT_MODALITIES.items():
                if modality in others_accepted:
                    continue
                with pytest.raises(ModalityMismatchError):
                    check_model(catalog_with, "m", endpoint=other)
