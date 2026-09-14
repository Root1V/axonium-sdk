"""The typed tool call, and the two things typing it was supposed to fix.

Tool calls used to arrive as raw dicts while everything around them was a model. That asymmetry
cost a consumer real work twice over: reaching into ``call["function"]["name"]`` by hand, and
converting back to dicts to feed a call into the next request. Both are covered here.
"""

from __future__ import annotations

import pytest

from axonium.errors import ToolCallArgumentsError
from axonium.models.chat import ChatCompletion, ToolCall
from axonium.models.requests import Message


def a_call(arguments: str, call_id: str = "call_1") -> ToolCall:
    return ToolCall.model_validate(
        {
            "id": call_id,
            "type": "function",
            "function": {"name": "get_weather", "arguments": arguments},
        }
    )


class TestParsingArguments:
    def test_decodes_the_arguments_string(self) -> None:
        assert a_call('{"city": "Lima"}').parse_arguments() == {"city": "Lima"}

    def test_a_truncated_call_raises_rather_than_returning_nothing(self) -> None:
        # This is the real failure: a generation stopped by max_tokens leaves arguments that were
        # never going to parse. Returning None or {} would make a truncated call indistinguishable
        # from a call that genuinely took no arguments, and the caller would invoke the tool with
        # the wrong input rather than finding out something went wrong.
        with pytest.raises(ToolCallArgumentsError) as caught:
            a_call('{"city": "Li').parse_arguments()

        assert "finish_reason" in str(caught.value), "the message should say how to check for this"
        assert '{"city": "Li' in str(caught.value), "the raw value should stay visible"
        assert caught.value.tool_call is not None
        assert caught.value.tool_call.id == "call_1"

    def test_arguments_that_decode_to_a_non_object_are_refused(self) -> None:
        # Valid JSON, wrong shape. Returning it would hand the caller a list where every downstream
        # line expects to index by argument name.
        with pytest.raises(ToolCallArgumentsError, match="not an object"):
            a_call("[1, 2]").parse_arguments()

    def test_the_error_is_ours_rather_than_the_json_modules(self) -> None:
        # Everything this SDK raises is an AxoniumError, so a caller never has to catch a
        # standard-library exception alongside ours. json.JSONDecodeError is a ValueError, and
        # letting it escape would break that.
        from axonium.errors import AxoniumError

        assert issubclass(ToolCallArgumentsError, AxoniumError)

    def test_the_raw_string_is_never_rewritten(self) -> None:
        # Whatever the model produced stays reachable verbatim, including when it cannot parse.
        call = a_call('{"city": "Li')
        assert call.function.arguments == '{"city": "Li'


class TestTheShapeItself:
    def test_name_reads_through_without_reaching_into_function(self) -> None:
        assert a_call("{}").name == "get_weather"

    def test_unknown_fields_survive(self) -> None:
        # Bodies are forwarded from heterogeneous backends verbatim, so a field this SDK does not
        # model must still reach the caller rather than being dropped on the way through.
        call = ToolCall.model_validate(
            {"id": "x", "type": "function", "function": {"name": "f"}, "backend_hint": 7}
        )
        assert call.model_dump()["backend_hint"] == 7

    def test_a_call_with_no_function_block_still_loads(self) -> None:
        # Nothing here is guaranteed by the gateway contract, so a missing piece must not turn a
        # usable response into a validation error.
        call = ToolCall.model_validate({"id": "x"})
        assert call.name is None
        assert call.function.arguments == ""


class TestFeedingACallBackIntoARequest:
    def test_a_response_call_goes_straight_into_the_next_message(self) -> None:
        # The tool-use loop: take what the model asked for, run it, send the call back alongside
        # the result. Before this was typed, the caller had to convert in both directions.
        completion = ChatCompletion.model_validate(
            {
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "tool_calls": [a_call('{"city": "Lima"}').model_dump()],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        )

        message = Message(role="assistant", tool_calls=completion.tool_calls)

        assert message.tool_calls is not None
        assert message.tool_calls[0].id == "call_1"

    def test_plain_dicts_are_still_accepted(self) -> None:
        # Typing the field must not break code written before it was typed.
        message = Message(
            role="assistant",
            tool_calls=[  # type: ignore[list-item]
                {"id": "c", "type": "function", "function": {"name": "f", "arguments": "{}"}}
            ],
        )
        assert message.tool_calls is not None
        assert message.tool_calls[0].name == "f"

    def test_it_serialises_back_to_the_wire_shape(self) -> None:
        # What goes out must be what the gateway expects, not this SDK's idea of a tool call.
        message = Message(role="assistant", tool_calls=[a_call('{"city": "Lima"}')])
        dumped = message.model_dump(exclude_none=True)

        assert dumped["tool_calls"] == [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city": "Lima"}'},
            }
        ]
