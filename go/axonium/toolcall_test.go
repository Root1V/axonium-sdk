package axonium

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"
)

// The typed tool call, and the two things typing it was supposed to fix.
//
// Tool calls used to arrive as []any while everything around them was a struct. That asymmetry
// cost a caller real work twice over: type-asserting through map[string]any by hand, and
// converting back to maps to feed a call into the next request.

func aCall(arguments string) ToolCall {
	return ToolCall{
		ID:       "call_1",
		Type:     "function",
		Function: FunctionCall{Name: "get_weather", Arguments: arguments},
	}
}

func TestParseArgumentsDecodesTheArgumentsString(t *testing.T) {
	got, err := aCall(`{"city": "Lima"}`).ParseArguments()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got["city"] != "Lima" {
		t.Errorf("got %v, want city=Lima", got)
	}
}

func TestATruncatedCallFailsRatherThanReturningNothing(t *testing.T) {
	// This is the real failure: a generation stopped by max_tokens leaves arguments that were
	// never going to parse. Returning an empty map would make a truncated call indistinguishable
	// from one that genuinely took no arguments, and the caller would invoke the tool with the
	// wrong input rather than finding out something went wrong.
	_, err := aCall(`{"city": "Li`).ParseArguments()
	if err == nil {
		t.Fatal("expected an error for truncated arguments")
	}
	if !errors.Is(err, ErrToolCallArguments) {
		t.Errorf("error does not match its sentinel: %v", err)
	}
	if !strings.Contains(err.Error(), "FinishReason") {
		t.Errorf("the message should say how to check for this: %v", err)
	}
	// %q-escaped, which is what the reader sees and is unambiguous about where the string ended.
	if !strings.Contains(err.Error(), `"{\"city\": \"Li"`) {
		t.Errorf("the raw value should stay visible: %v", err)
	}
}

func TestArgumentsThatDecodeToANonObjectAreRefused(t *testing.T) {
	// Valid JSON, wrong shape. Returning it would hand the caller something where every
	// downstream line expects to index by argument name.
	if _, err := aCall("[1, 2]").ParseArguments(); !errors.Is(err, ErrToolCallArguments) {
		t.Errorf("got %v, want ErrToolCallArguments", err)
	}
}

func TestTheRawArgumentStringIsNeverRewritten(t *testing.T) {
	call := aCall(`{"city": "Li`)
	if _, err := call.ParseArguments(); err == nil {
		t.Fatal("expected an error")
	}
	if call.Function.Arguments != `{"city": "Li` {
		t.Errorf("arguments were modified: %q", call.Function.Arguments)
	}
}

func TestAResponseCallGoesStraightIntoTheNextMessage(t *testing.T) {
	// The tool-use loop: take what the model asked for, run it, send the call back alongside the
	// result. Before this was typed, the caller had to convert in both directions.
	completion := &ChatCompletion{
		Choices: []Choice{{Message: &Message{Role: "assistant", ToolCalls: []ToolCall{aCall(`{"city": "Lima"}`)}}}},
	}

	message := Message{Role: "assistant", ToolCalls: completion.ToolCalls()}

	if len(message.ToolCalls) != 1 || message.ToolCalls[0].ID != "call_1" {
		t.Fatalf("got %+v, want the call passed straight through", message.ToolCalls)
	}
}

func TestAToolCallSerialisesBackToTheWireShape(t *testing.T) {
	// What goes out must be what the gateway expects, not this SDK's idea of a tool call.
	encoded, err := json.Marshal(Message{Role: "assistant", ToolCalls: []ToolCall{aCall(`{"city": "Lima"}`)}})
	if err != nil {
		t.Fatalf("could not encode: %v", err)
	}
	want := `{"role":"assistant","tool_calls":[{"id":"call_1","type":"function","function":{"name":"get_weather","arguments":"{\"city\": \"Lima\"}"}}]}`
	if string(encoded) != want {
		t.Errorf("\n got %s\nwant %s", encoded, want)
	}
}
