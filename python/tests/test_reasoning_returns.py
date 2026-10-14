"""Does a reasoning model's own analysis go back out, or only come in?

`PRM-VRT-003`, 2026-10-13. Prometheus found their gateway **deleting `reasoning_content` from
every message of every client** — their internal message model did not name the field, Pydantic
dropped what it did not name, and the serializer wrote the rest to the engine. Fixed on their side,
and then they asked the three frameworks the one question they could not answer themselves: does
that field survive *your* serialization?

**It is not cosmetic.** llama.cpp issue #27720 is closed as not fixable in the parser, and the
thread measures the cost: **7 failed turns out of 120** with the reasoning withheld, **0 of 120**
with it returned. A reasoning model asked to continue from its own tool call, without its analysis
channel, emits malformed headers that lose the whole turn. On models that do not use Harmony it is
not an error at all — it is quietly worse answers.

**What measuring found here, across five SDKs:** Python, Go and Rust returned it; Python only
because ``Message`` allows extras, so it worked by configuration rather than by decision.
**TypeScript refused it at compile time** (`TS2353`), so the supported way to do the right thing did
not type-check. **Swift could not send it at all** — `wireForm` built the payload field by field
with no field for it and no escape hatch, while `ChatCompletion` had exposed `reasoningContent`
since `0.2.0`. The field came in and could not go back out.

**And the hole was never the field, it was that nothing looked.** Synaptum and Aeon each reported
the same shape in their own words: their suites asserted what arrived and none asserted what was
sent back. A field that enters and does not leave breaks nothing visible — the model answers worse,
or one turn in seventeen fails, and nobody attributes it to this. So this file asserts the
direction nobody was asserting.
"""

from __future__ import annotations

import warnings

from axonium.models.chat import ChatCompletion
from axonium.models.requests import ChatCompletionRequest


def a_turn_that_called_a_tool() -> dict[str, object]:
    """The shape the measurement is about: assistant reasoning plus a tool call, no content."""
    return {
        "role": "assistant",
        "content": None,
        "reasoning_content": "The user wants weather. I should call get_weather.",
        "tool_calls": [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city": "Quito"}'},
            }
        ],
    }


class TestItLeaves:
    def test_the_assistant_turn_carries_it_to_the_wire(self) -> None:
        request = ChatCompletionRequest.build(
            {
                "model": "gpt-oss-20b-mxfp4",
                "messages": [
                    {"role": "user", "content": "weather in Quito?"},
                    a_turn_that_called_a_tool(),
                    {"role": "tool", "tool_call_id": "c1", "content": "cloudy, 17C"},
                ],
            }
        )

        assistant = request.to_payload()["messages"][1]
        assert assistant["reasoning_content"] == (
            "The user wants weather. I should call get_weather."
        )
        # The tool call has to survive alongside it: the measured turn is the one carrying both,
        # and a fix that sent the reasoning while dropping the call would pass a weaker assertion.
        assert assistant["tool_calls"][0]["id"] == "c1"

    def test_a_response_message_can_be_handed_straight_back(self) -> None:
        """The round trip, which is what a tool-use loop actually does.

        Asserted end to end rather than field by field, because the failure this file exists for
        lives in the join: the response model carried it and the request model did not, and each
        half looked right on its own.
        """
        completion = ChatCompletion.model_validate(
            {
                "id": "c",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-oss-20b-mxfp4",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": a_turn_that_called_a_tool(),
                    }
                ],
            }
        )

        assert completion.reasoning == "The user wants weather. I should call get_weather."

        returned = ChatCompletionRequest.build(
            {
                "model": "gpt-oss-20b-mxfp4",
                "messages": [completion.choices[0].message.model_dump(exclude_none=True)],
            }
        )
        assert (
            returned.to_payload()["messages"][0]["reasoning_content"]
            == "The user wants weather. I should call get_weather."
        )


class TestAbsentIsNotEmpty:
    def test_it_is_omitted_when_the_model_reported_none(self) -> None:
        """Never ``""``.

        Absent and empty are different assertions and the second one claims the model thought about
        nothing. Both Aeon and Synaptum fixed this field in the same week and both wrote the same
        rule down, so it is pinned here rather than left to the serializer's defaults.
        """
        request = ChatCompletionRequest.build(
            {"model": "m", "messages": [{"role": "assistant", "content": "hi"}]}
        )

        assistant = request.to_payload()["messages"][0]
        assert "reasoning_content" not in assistant, assistant

    def test_declaring_the_field_did_not_start_warning_about_it(self) -> None:
        """A warning on every agent turn would be worse than the bug.

        Before it was declared the field reached the wire as an extra, and
        ``_drop_unsupported`` warns about unrecognised fields on the *request*. It never looked
        inside a ``Message``, so nothing warned — measured before declaring it, so that this test
        pins the absence of a regression rather than the absence of a feature.
        """
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ChatCompletionRequest.build({"model": "m", "messages": [a_turn_that_called_a_tool()]})
        assert not [w for w in caught if "reasoning" in str(w.message).lower()], [
            str(w.message) for w in caught
        ]
