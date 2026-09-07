from __future__ import annotations

import httpx
import pytest
import respx

from axonium import AsyncAxonium, Axonium
from axonium.errors import ModalityMismatchError, UnknownModelError
from axonium.models.catalog import ModelList
from axonium.preflight import check_model

AUTH_URL = "https://auth.test.invalid/oauth2/token"
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
        ],
    )
    def test_accepts_a_model_the_endpoint_can_serve(self, model: str, endpoint: str) -> None:
        check_model(catalog(), model, endpoint=endpoint)

    def test_rejects_chat_on_an_embedding_model(self) -> None:
        # The gateway does not reject this: it answers 200 with degenerate output the caller pays
        # for. Catching it here is the whole reason this check exists.
        with pytest.raises(ModalityMismatchError, match="embedding"):
            check_model(catalog(), "embed-model", endpoint="chat")

    @pytest.mark.parametrize(
        ("model", "endpoint"),
        [
            ("chat-model", "embeddings"),
            ("chat-model", "images"),
            ("image-model", "chat"),
            ("vision-model", "embeddings"),
        ],
    )
    def test_rejects_every_other_wrong_pairing(self, model: str, endpoint: str) -> None:
        with pytest.raises(ModalityMismatchError):
            check_model(catalog(), model, endpoint=endpoint)

    def test_rejects_a_model_absent_from_the_catalog(self) -> None:
        with pytest.raises(UnknownModelError, match="case-sensitive") as caught:
            check_model(catalog(), "chat-modle", endpoint="chat")

        # Listing the real IDs turns a typo into an obvious fix.
        assert "chat-model" in str(caught.value)

    def test_model_ids_are_matched_exactly(self) -> None:
        with pytest.raises(UnknownModelError):
            check_model(catalog(), "CHAT-MODEL", endpoint="chat")

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


def test_every_endpoint_has_a_modality_mapping() -> None:
    from axonium.preflight import ENDPOINT_MODALITIES

    assert set(ENDPOINT_MODALITIES) == {"chat", "embeddings", "images"}
    assert all(isinstance(v, frozenset) and v for v in ENDPOINT_MODALITIES.values())
