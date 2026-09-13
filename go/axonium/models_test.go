package axonium

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// The accessors are where a caller meets a backend-shaped payload, so they have to survive one
// that is missing the parts this SDK would like to find.

func TestAccessorsSurviveAnEmptyPayload(t *testing.T) {
	var completion ChatCompletion
	if got := completion.Content(); got != "" {
		t.Errorf("Content on a choiceless completion: got %q", got)
	}
	if got := completion.Reasoning(); got != "" {
		t.Errorf("Reasoning on a choiceless completion: got %q", got)
	}
	if got := completion.FinishReason(); got != "" {
		t.Errorf("FinishReason on a choiceless completion: got %q", got)
	}
	if got := completion.ToolCalls(); got != nil {
		t.Errorf("ToolCalls on a choiceless completion: got %v", got)
	}

	var chunk ChatCompletionChunk
	if chunk.Content() != "" || chunk.Reasoning() != "" || chunk.FinishReason() != "" {
		t.Error("a choiceless chunk should report nothing rather than panic")
	}

	// A choice with no message is not the same as no choice, and is equally survivable.
	completion.Choices = []Choice{{Index: 0}}
	chunk.Choices = []Choice{{Index: 0}}
	if completion.Content() != "" || chunk.Content() != "" {
		t.Error("a choice without a message or delta should report nothing")
	}
}

// Multimodal content is a list of parts, not a string. Flattening keeps the text and skips the
// rest, so a vision response reads like any other rather than as an empty answer.
func TestContentFlattensMultimodalParts(t *testing.T) {
	completion := ChatCompletion{Choices: []Choice{{Message: &Message{Content: []any{
		map[string]any{"type": "text", "text": "a cat"},
		map[string]any{"type": "image_url", "image_url": map[string]any{"url": "data:..."}},
		map[string]any{"type": "text", "text": " on a mat"},
		"a bare string that is not a part object",
	}}}}}

	if got := completion.Content(); got != "a cat on a mat" {
		t.Errorf("got %q, want %q", got, "a cat on a mat")
	}

	// An unknown content shape yields nothing rather than a Go-syntax rendering of the payload.
	completion.Choices[0].Message.Content = 42
	if got := completion.Content(); got != "" {
		t.Errorf("an unmodelled content shape should read as empty, got %q", got)
	}
}

func TestFinishReasonAndToolCallsComeFromTheFirstChoice(t *testing.T) {
	raw := `{"choices":[{"index":0,"finish_reason":"tool_calls","message":{"role":"assistant",
	  "content":null,"tool_calls":[{"id":"c1","function":{"name":"f"}}]}}]}`
	var completion ChatCompletion
	if err := json.Unmarshal([]byte(raw), &completion); err != nil {
		t.Fatalf("decoding: %v", err)
	}

	if completion.FinishReason() != "tool_calls" {
		t.Errorf("finish reason: got %q", completion.FinishReason())
	}
	if len(completion.ToolCalls()) != 1 {
		t.Fatalf("tool calls: got %v", completion.ToolCalls())
	}
	// A null content is not an empty answer: the model answered with calls.
	if completion.Content() != "" {
		t.Errorf("content alongside tool calls: got %q", completion.Content())
	}
}

func TestChunkAccessorsReadTheDelta(t *testing.T) {
	raw := `{"choices":[{"index":0,"finish_reason":"length",
	  "delta":{"content":"answer","reasoning_content":"thinking"}}]}`
	var chunk ChatCompletionChunk
	if err := json.Unmarshal([]byte(raw), &chunk); err != nil {
		t.Fatalf("decoding: %v", err)
	}

	if chunk.Content() != "answer" || chunk.Reasoning() != "thinking" {
		t.Errorf("content=%q reasoning=%q", chunk.Content(), chunk.Reasoning())
	}
	if chunk.FinishReason() != "length" {
		t.Errorf("finish reason: got %q", chunk.FinishReason())
	}
}

// The catalog resolves an alias as well as a canonical id, because a caller may hold a name from
// before a rename and the catalog no longer advertises it.
func TestCatalogFindMatchesIDsAndAliases(t *testing.T) {
	list := ModelList{Data: []Model{
		{ID: "qwen3-0.6b", Aliases: []string{"qwen3-0-6b-iq4-nl-local-2"}},
		{ID: "sd-turbo"},
	}}

	for _, name := range []string{"qwen3-0.6b", "qwen3-0-6b-iq4-nl-local-2", "sd-turbo"} {
		if _, ok := list.Find(name); !ok {
			t.Errorf("%q should resolve", name)
		}
	}
	if _, ok := list.Find("not-a-model"); ok {
		t.Error("an unknown name must not resolve")
	}
	if got := list.IDs(); len(got) != 2 || got[0] != "qwen3-0.6b" {
		t.Errorf("IDs: got %v", got)
	}
}

func TestImageDecodesAndSaves(t *testing.T) {
	// "hi" in base64. Small on purpose: this tests the decode path, not the codec.
	img := Image{B64JSON: "aGk="}

	data, err := img.Bytes()
	if err != nil {
		t.Fatalf("decoding: %v", err)
	}
	if string(data) != "hi" {
		t.Errorf("got %q, want %q", data, "hi")
	}

	path := filepath.Join(t.TempDir(), "out.png")
	if err := img.Save(path); err != nil {
		t.Fatalf("saving: %v", err)
	}
	onDisk, err := os.ReadFile(path)
	if err != nil || string(onDisk) != "hi" {
		t.Errorf("what landed on disk: %q, %v", onDisk, err)
	}

	// A payload that is not base64 is reported as a transport problem, not returned as garbage.
	broken := Image{B64JSON: "not base64 at all!!"}
	if _, err := broken.Bytes(); err == nil {
		t.Error("an undecodable payload must be an error rather than silent nonsense")
	}
	if err := broken.Save(path); err == nil {
		t.Error("Save must fail on an undecodable payload rather than write junk")
	}
}
