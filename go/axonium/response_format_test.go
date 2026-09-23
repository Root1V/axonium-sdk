package axonium

import (
	"encoding/json"
	"testing"
)

// The field this SDK used to be unable to express discoverably.
//
// The platform enabled structured outputs on 2026-09-18 (PRM-126). Go could always smuggle it
// through Extra, but an untyped escape hatch is not a capability a caller can find -- and Python,
// which strips unknown fields, could not send it at all.
func TestResponseFormatReachesTheWire(t *testing.T) {
	schema := map[string]any{"type": "json_schema"}
	body, err := json.Marshal(ChatRequest{
		Model:          "m",
		Messages:       []Message{TextMessage("user", "hi")},
		ResponseFormat: schema,
	})
	if err != nil {
		t.Fatalf("marshalling: %v", err)
	}

	var sent map[string]any
	if err := json.Unmarshal(body, &sent); err != nil {
		t.Fatalf("unmarshalling: %v", err)
	}
	if sent["response_format"] == nil {
		t.Errorf("response_format did not reach the body: %s", body)
	}
}

func TestResponseFormatIsAbsentWhenNotAskedFor(t *testing.T) {
	// omitempty matters here rather than being tidiness: the gateway treats the field's presence
	// as a request for constrained output, so sending a null would change what a caller gets.
	body, _ := json.Marshal(ChatRequest{Model: "m", Messages: []Message{TextMessage("user", "hi")}})

	var sent map[string]any
	_ = json.Unmarshal(body, &sent)
	if _, present := sent["response_format"]; present {
		t.Errorf("response_format was sent unasked: %s", body)
	}
}
