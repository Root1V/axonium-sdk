//! The typed tool call, and the two things typing it was supposed to fix.
//!
//! Tool calls used to arrive as bare `Value`s while everything around them was a struct. That
//! asymmetry cost a caller real work twice over: indexing through JSON by hand, and converting
//! back to `Value` to feed a call into the next request.

use axonium::{Error, FunctionCall, Message, ToolCall};

fn a_call(arguments: &str) -> ToolCall {
    ToolCall {
        id: "call_1".into(),
        kind: "function".into(),
        function: FunctionCall {
            name: "get_weather".into(),
            arguments: arguments.into(),
        },
    }
}

#[test]
fn parse_arguments_decodes_the_arguments_string() {
    let parsed = a_call(r#"{"city": "Lima"}"#).parse_arguments().unwrap();
    assert_eq!(parsed["city"], "Lima");
}

#[test]
fn a_truncated_call_fails_rather_than_yielding_nothing() {
    // This is the real failure: a generation stopped by max_tokens leaves arguments that were
    // never going to parse. Yielding an empty map would make a truncated call indistinguishable
    // from one that genuinely took no arguments, and the caller would invoke the tool with the
    // wrong input rather than finding out something went wrong.
    let call = a_call(r#"{"city": "Li"#);
    let error = call.parse_arguments().unwrap_err();

    assert!(
        matches!(error, Error::ToolCallArguments { .. }),
        "got {error:?}"
    );
    let rendered = error.to_string();
    assert!(
        rendered.contains("finish_reason"),
        "the message should say how to check for this: {rendered}"
    );
    assert!(
        rendered.contains(r#"{\"city\": \"Li"#),
        "the raw value should stay visible: {rendered}"
    );
    // And the call itself is untouched, so what the model managed to say stays readable.
    assert_eq!(call.function.arguments, r#"{"city": "Li"#);
}

#[test]
fn arguments_that_decode_to_a_non_object_are_refused() {
    // Valid JSON, wrong shape. Returning it would hand the caller something where every
    // downstream line expects to index by argument name.
    let error = a_call("[1, 2]").parse_arguments().unwrap_err();
    assert!(error.to_string().contains("an array"), "{error}");
}

#[test]
fn name_reads_through_without_reaching_into_function() {
    assert_eq!(a_call("{}").name(), "get_weather");
}

#[test]
fn a_response_call_goes_straight_into_the_next_message() {
    // The tool-use loop: take what the model asked for, run it, send the call back alongside the
    // result. Before this was typed, the caller had to convert in both directions.
    let mut message = Message::text("assistant", "");
    message.tool_calls = Some(vec![a_call(r#"{"city": "Lima"}"#)]);

    let calls = message.tool_calls.as_ref().unwrap();
    assert_eq!(calls[0].id, "call_1");
}

#[test]
fn it_serialises_back_to_the_wire_shape() {
    // What goes out must be what the gateway expects, not this SDK's idea of a tool call.
    let encoded = serde_json::to_value(a_call(r#"{"city": "Lima"}"#)).unwrap();
    assert_eq!(
        encoded,
        serde_json::json!({
            "id": "call_1",
            "type": "function",
            "function": {"name": "get_weather", "arguments": r#"{"city": "Lima"}"#},
        })
    );
}
