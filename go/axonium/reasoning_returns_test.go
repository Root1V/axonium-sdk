package axonium

import (
	"encoding/json"
	"testing"
)

// Does a reasoning model's own analysis go back out, or only come in?
//
// PRM-VRT-003, 2026-10-13. Prometheus found their gateway deleting reasoning_content from every
// message of every client -- their internal message model did not name the field, Pydantic dropped
// what it did not name -- and then asked the three frameworks the one question they could not
// answer themselves: does that field survive YOUR serialization?
//
// It is not cosmetic. llama.cpp issue #27720 is closed as not fixable in the parser, and the thread
// measures it: 7 failed turns out of 120 with the reasoning withheld, 0 of 120 with it returned.
//
// Go was one of the three that already worked, because Message declares the field. Swift could not
// send it at all and TypeScript refused it at compile time. The hole was never the field: every
// team that hit this had tests asserting what ARRIVED and none asserting what was SENT BACK. A
// field that enters and does not leave breaks nothing visible.
func TestReasoningContentLeavesOnAnAssistantTurn(t *testing.T) {
	req := ChatRequest{
		Model: "gpt-oss-20b-mxfp4",
		Messages: []Message{
			TextMessage("user", "weather in Quito?"),
			{
				Role:             "assistant",
				ReasoningContent: "The user wants weather. I should call get_weather.",
				ToolCalls: []ToolCall{{
					ID: "c1", Type: "function",
					Function: FunctionCall{Name: "get_weather", Arguments: `{"city":"Quito"}`},
				}},
			},
			{Role: "tool", ToolCallID: "c1", Content: "cloudy, 17C"},
		},
	}

	encoded, err := json.Marshal(req)
	if err != nil {
		t.Fatalf("marshalling: %v", err)
	}
	var body struct {
		Messages []map[string]any `json:"messages"`
	}
	if err := json.Unmarshal(encoded, &body); err != nil {
		t.Fatalf("decoding what we sent: %v", err)
	}

	assistant := body.Messages[1]
	if got := assistant["reasoning_content"]; got != "The user wants weather. I should call get_weather." {
		t.Errorf("reasoning_content did not reach the wire: %v", got)
	}
	// The tool call has to survive beside it. The measured turn is the one carrying both, and a
	// fix that sent the reasoning while dropping the call would satisfy a weaker assertion.
	calls, ok := assistant["tool_calls"].([]any)
	if !ok || len(calls) != 1 {
		t.Fatalf("the tool call did not survive alongside the reasoning: %v", assistant)
	}
}

// Never "": absent and empty are different assertions, and the second one claims the model thought
// about nothing. Aeon and Synaptum fixed this field in the same week and both wrote the same rule.
func TestReasoningContentIsOmittedWhenThereIsNone(t *testing.T) {
	req := ChatRequest{Model: "m", Messages: []Message{TextMessage("assistant", "hi")}}

	encoded, err := json.Marshal(req)
	if err != nil {
		t.Fatalf("marshalling: %v", err)
	}
	var body struct {
		Messages []map[string]any `json:"messages"`
	}
	if err := json.Unmarshal(encoded, &body); err != nil {
		t.Fatalf("decoding: %v", err)
	}

	if _, present := body.Messages[0]["reasoning_content"]; present {
		t.Errorf("sent the key with no reasoning to report: %v", body.Messages[0])
	}
}

// The round trip, which is what a tool-use loop does. Asserted end to end rather than field by
// field, because the failure lives in the join: in three of the five SDKs the response type
// carried the field and the request type did not, and each half looked right on its own.
func TestAResponseMessageCanBeHandedStraightBack(t *testing.T) {
	var completion ChatCompletion
	raw := `{"id":"c","object":"chat.completion","created":0,"model":"gpt-oss-20b-mxfp4",
	 "choices":[{"index":0,"finish_reason":"tool_calls","message":{"role":"assistant",
	 "reasoning_content":"thinking out loud","tool_calls":[{"id":"c1","type":"function",
	 "function":{"name":"f","arguments":"{}"}}]}}]}`
	if err := json.Unmarshal([]byte(raw), &completion); err != nil {
		t.Fatalf("decoding a response: %v", err)
	}
	if completion.Reasoning() != "thinking out loud" {
		t.Fatalf("the response did not expose the reasoning: %q", completion.Reasoning())
	}

	req := ChatRequest{Model: "m", Messages: []Message{*completion.Choices[0].Message}}
	encoded, err := json.Marshal(req)
	if err != nil {
		t.Fatalf("marshalling: %v", err)
	}
	var body struct {
		Messages []map[string]any `json:"messages"`
	}
	_ = json.Unmarshal(encoded, &body)
	if got := body.Messages[0]["reasoning_content"]; got != "thinking out loud" {
		t.Errorf("a response message handed back lost its reasoning: %v", got)
	}
}
