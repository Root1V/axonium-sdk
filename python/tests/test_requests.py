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


class TestResponseFormatReachesTheWire:
    """The field the SDK used to strip while telling the caller the gateway did not support it.

    The platform enabled structured outputs on 2026-09-18 (PRM-126) and said so. Our allowlist was
    not updated, so for five days this SDK answered a caller asking for structured output with

        The gateway does not support response_format; it was not sent.

    which was false in both halves. Measured against a deployment after the fix: the field goes out
    and the answer comes back constrained by the schema.
    """

    def test_it_is_sent_rather_than_stripped(self) -> None:
        schema = {"type": "json_schema", "json_schema": {"name": "x", "schema": {}}}

        payload = ChatCompletionRequest.build(
            {
                "model": "m",
                "messages": [{"role": "user", "content": "hi"}],
                "response_format": schema,
            }
        ).to_payload()

        assert payload["response_format"] == schema

    def test_it_no_longer_warns(self, recwarn: pytest.WarningsRecorder) -> None:
        ChatCompletionRequest.build(
            {
                "model": "m",
                "messages": [{"role": "user", "content": "hi"}],
                "response_format": {"type": "text"},
            }
        )

        assert not [w for w in recwarn if issubclass(w.category, UnsupportedFieldWarning)]

    def test_it_is_absent_when_not_asked_for(self) -> None:
        # to_payload drops unset optionals, so a caller who never mentions it sends nothing --
        # which matters because the gateway treats its presence as a request for constrained output.
        payload = ChatCompletionRequest.build(
            {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
        ).to_payload()

        assert "response_format" not in payload

    def test_the_fields_the_gateway_really_drops_still_warn(
        self, recwarn: pytest.WarningsRecorder
    ) -> None:
        # Measured 2026-09-23 from X-Prometheus-Ignored-Parameters on a live response, so this list
        # is the gateway's own answer rather than our recollection of it.
        ChatCompletionRequest.build(
            {
                "model": "m",
                "messages": [{"role": "user", "content": "hi"}],
                "n": 1,
                "seed": 7,
                "logit_bias": {},
                "user": "u",
                "presence_penalty": 0.1,
                "frequency_penalty": 0.1,
            }
        )

        warned = " ".join(str(w.message) for w in recwarn)
        for field in ("n", "seed", "logit_bias", "user", "presence_penalty", "frequency_penalty"):
            assert field in warned, f"{field} is dropped by the gateway and was not reported"


class TestTheUnsupportedListCannotGoStale:
    """`KNOWN_UNSUPPORTED` may not name a field the model also declares.

    Such an entry is dead: a declared field is assigned by pydantic and never reaches
    ``__pydantic_extra__``, which is the only thing the warning inspects. Nothing fails, the entry
    simply stops meaning anything, and the next reader believes the field is dropped.

    Written after exactly that: `response_format` sat in this set for five days after the platform
    started honouring it, and removing it from the set was not enough -- the field had to be
    declared for it to reach the wire at all. A mutation putting it back changes nothing
    observable, which is why this guard exists instead of a test that would pass either way.
    """

    @pytest.mark.parametrize(
        "request_type",
        [ChatCompletionRequest, EmbeddingsRequest, ImageGenerationRequest],
        ids=lambda cls: cls.__name__,
    )
    def test_no_declared_field_is_also_listed_as_unsupported(self, request_type: type) -> None:
        declared = set(request_type.model_fields)

        overlap = sorted(declared & request_type.KNOWN_UNSUPPORTED)

        assert not overlap, (
            f"{request_type.__name__} declares {overlap} and also lists them as unsupported; "
            f"the list entry is dead and says the opposite of what the field does"
        )
