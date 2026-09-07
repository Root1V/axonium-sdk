from __future__ import annotations

import pytest
from pydantic import ValidationError

from axonium.errors import UnsupportedFieldWarning
from axonium.models.requests import (
    ChatCompletionRequest,
    EmbeddingsRequest,
    ImageGenerationRequest,
)

MESSAGES = [{"role": "user", "content": "Hello"}]


class TestAllowlist:
    def test_documented_unsupported_fields_are_named_and_dropped(self) -> None:
        # The gateway discards these silently, so a caller would otherwise never learn that the
        # parameter they set had no effect.
        with pytest.warns(UnsupportedFieldWarning, match="presence_penalty"):
            request = ChatCompletionRequest(model="m", messages=MESSAGES, presence_penalty=0.5)

        assert "presence_penalty" not in request.to_payload()

    @pytest.mark.parametrize(
        "field", ["n", "presence_penalty", "frequency_penalty", "logit_bias", "user", "seed"]
    )
    def test_every_documented_chat_field_is_covered(self, field: str) -> None:
        with pytest.warns(UnsupportedFieldWarning):
            request = ChatCompletionRequest(model="m", messages=MESSAGES, **{field: 1})

        assert field not in request.to_payload()

    def test_unknown_fields_warn_differently_from_documented_ones(self) -> None:
        with pytest.warns(UnsupportedFieldWarning, match="Unrecognized"):
            ChatCompletionRequest(model="m", messages=MESSAGES, wibble=1)

    def test_several_dropped_fields_are_reported_together(self) -> None:
        with pytest.warns(UnsupportedFieldWarning, match="seed, user"):
            ChatCompletionRequest(model="m", messages=MESSAGES, seed=1, user="u")

    def test_supported_fields_pass_through_untouched(self) -> None:
        request = ChatCompletionRequest(
            model="llama3-8b-q4",
            messages=MESSAGES,
            max_tokens=256,
            temperature=0.7,
            top_p=0.9,
            stop=["\n\n"],
        )
        payload = request.to_payload()

        assert payload["model"] == "llama3-8b-q4"
        assert payload["max_tokens"] == 256
        assert payload["stop"] == ["\n\n"]

    def test_unset_optionals_are_omitted_rather_than_sent_as_null(self) -> None:
        payload = ChatCompletionRequest(model="m", messages=MESSAGES).to_payload()

        assert "temperature" not in payload
        assert "max_tokens" not in payload

    def test_embeddings_drops_its_own_unsupported_fields(self) -> None:
        with pytest.warns(UnsupportedFieldWarning, match="dimensions"):
            request = EmbeddingsRequest(model="e", input="text", dimensions=512)

        assert "dimensions" not in request.to_payload()

    def test_images_drops_backend_parameters_the_gateway_does_not_forward(self) -> None:
        # These are accepted by some backends but never reach them through this gateway.
        with pytest.warns(UnsupportedFieldWarning, match="negative_prompt"):
            request = ImageGenerationRequest(model="sd", prompt="a cat", negative_prompt="dogs")

        assert "negative_prompt" not in request.to_payload()


class TestValidation:
    @pytest.mark.parametrize("temperature", [-0.1, 2.1])
    def test_rejects_temperature_outside_the_supported_range(self, temperature: float) -> None:
        with pytest.raises(ValidationError):
            ChatCompletionRequest(model="m", messages=MESSAGES, temperature=temperature)

    @pytest.mark.parametrize("top_p", [0.0, 1.1])
    def test_rejects_top_p_outside_the_supported_range(self, top_p: float) -> None:
        # The range is exclusive of zero and inclusive of one.
        with pytest.raises(ValidationError):
            ChatCompletionRequest(model="m", messages=MESSAGES, top_p=top_p)

    def test_accepts_the_range_boundaries(self) -> None:
        ChatCompletionRequest(model="m", messages=MESSAGES, temperature=0.0, top_p=1.0)
        ChatCompletionRequest(model="m", messages=MESSAGES, temperature=2.0)

    def test_rejects_a_non_positive_max_tokens(self) -> None:
        with pytest.raises(ValidationError):
            ChatCompletionRequest(model="m", messages=MESSAGES, max_tokens=0)

    def test_rejects_an_empty_message_list(self) -> None:
        with pytest.raises(ValidationError, match="messages"):
            ChatCompletionRequest(model="m", messages=[])

    def test_rejects_an_unknown_role(self) -> None:
        with pytest.raises(ValidationError):
            ChatCompletionRequest(model="m", messages=[{"role": "wizard", "content": "hi"}])

    def test_allows_null_content_for_a_tool_call_message(self) -> None:
        # An assistant message carrying only tool_calls has no textual content.
        request = ChatCompletionRequest(
            model="m",
            messages=[
                {"role": "user", "content": "weather?"},
                {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
                {"role": "tool", "content": "sunny", "tool_call_id": "1"},
            ],
        )

        assert request.messages[1].content is None


class TestVisionContent:
    def test_accepts_a_base64_data_uri(self) -> None:
        request = ChatCompletionRequest(
            model="vision",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is this?"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,iVBORw0KG"},
                        },
                    ],
                }
            ],
        )

        assert len(request.messages[0].content) == 2  # type: ignore[arg-type]

    def test_a_text_only_part_carries_no_image_url(self) -> None:
        request = ChatCompletionRequest(
            model="m",
            messages=[
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "hi", "image_url": None}],
                }
            ],
        )

        assert request.messages[0].content[0].image_url is None  # type: ignore[index,union-attr]

    @pytest.mark.parametrize(
        "url", ["https://example.com/cat.png", "http://example.com/cat.png", "HTTPS://EXAMPLE.COM"]
    )
    def test_rejects_a_remote_image_url_client_side(self, url: str) -> None:
        # The gateway refuses these as an SSRF mitigation; failing here explains why, rather than
        # spending a round trip to get an opaque 400 back.
        with pytest.raises(ValidationError, match="SSRF"):
            ChatCompletionRequest(
                model="vision",
                messages=[
                    {
                        "role": "user",
                        "content": [{"type": "image_url", "image_url": {"url": url}}],
                    }
                ],
            )
