package axonium

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// Client-side validation exists to turn a round trip into an immediate, specific message. These
// tests are as much about the message as about the rejection: an error that says "invalid request"
// and nothing else is barely better than the 422.

func TestRequestValidationRejectsBeforeTheWire(t *testing.T) {
	for _, tc := range []struct {
		name    string
		req     ChatRequest
		mustSay string
	}{
		{"no model", ChatRequest{Messages: []Message{TextMessage("user", "hi")}}, "model is required"},
		{"no messages", ChatRequest{Model: "m"}, "messages must not be empty"},
		{"bad role", ChatRequest{Model: "m", Messages: []Message{{Role: "wizard", Content: "hi"}}}, "wizard"},
		{"temperature high", ChatRequest{Model: "m", Messages: []Message{TextMessage("user", "hi")}, Temperature: ptr(2.5)}, "temperature"},
		{"top_p zero", ChatRequest{Model: "m", Messages: []Message{TextMessage("user", "hi")}, TopP: ptr(0.0)}, "top_p"},
		{"max_tokens zero", ChatRequest{Model: "m", Messages: []Message{TextMessage("user", "hi")}, MaxTokens: ptr(0)}, "max_tokens"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			err := tc.req.validate()
			if !errors.Is(err, ErrInvalidRequest) {
				t.Fatalf("expected a validation error, got %v", err)
			}
			if !strings.Contains(err.Error(), tc.mustSay) {
				t.Errorf("the message should name the problem (%q), got %q", tc.mustSay, err)
			}
		})
	}
}

// A remote image URL is refused client-side with the reason spelled out. The gateway will not
// fetch one -- accepting a caller-supplied URL server-side would be an SSRF vector -- and an error
// that only said "invalid" would leave the caller guessing why their URL is unwelcome.
func TestRemoteImageURLIsRefusedWithTheReason(t *testing.T) {
	req := ChatRequest{
		Model: "vision-model",
		Messages: []Message{{Role: "user", Content: []any{
			map[string]any{"type": "text", "text": "what is this?"},
			map[string]any{"type": "image_url", "image_url": map[string]any{"url": "https://example.com/cat.png"}},
		}}},
	}

	err := req.validate()
	if !errors.Is(err, ErrInvalidRequest) {
		t.Fatalf("a remote image URL must be refused, got %v", err)
	}
	if !strings.Contains(err.Error(), "SSRF") {
		t.Errorf("the message should say why, got %q", err)
	}

	// A data: URI is the supported form and must pass.
	req.Messages[0].Content = []any{
		map[string]any{"type": "image_url", "image_url": map[string]any{"url": "data:image/png;base64,aGk="}},
	}
	if err := req.validate(); err != nil {
		t.Errorf("a data: URI is the supported form and must be accepted, got %v", err)
	}
}

// The gateway's modality check is one-directional: an embedding model on /v1/chat/completions
// returns 200 with degenerate output that is billed. This is the client-side closing of that gap.
func TestModalityCheckCatchesTheGatewaysOneDirectionalGap(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oauth2/token" {
			writeToken(w, "tok", 300)
			return
		}
		if r.URL.Path == "/v1/models" {
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]any{"object": "list", "data": []any{
				map[string]any{"id": "embed-model", "modality": "embedding"},
				map[string]any{"id": "llama3-8b-q4", "modality": "text"},
			}})
			return
		}
		t.Error("the request reached the gateway; the modality check should have stopped it")
	}))
	defer srv.Close()

	client, err := New(Config{
		AuthBaseURL: srv.URL, GatewayBaseURL: srv.URL,
		ClientID: "id", ClientSecret: "secret", VerifyModality: true,
	})
	if err != nil {
		t.Fatalf("building the client: %v", err)
	}
	defer client.Close()

	_, err = client.Chat.Create(context.Background(), ChatRequest{
		Model:    "embed-model",
		Messages: []Message{TextMessage("user", "hi")},
	})
	if !errors.Is(err, ErrInvalidRequest) {
		t.Fatalf("chat on an embedding model should be refused, got %v", err)
	}
	if !strings.Contains(err.Error(), "bill") {
		t.Errorf("the message should say what it saved the caller from, got %q", err)
	}

	// A typo is caught by the same check, and the message lists what is available.
	_, err = client.Chat.Create(context.Background(), ChatRequest{
		Model:    "llama3-8b-q5",
		Messages: []Message{TextMessage("user", "hi")},
	})
	if !errors.Is(err, ErrInvalidRequest) || !strings.Contains(err.Error(), "llama3-8b-q4") {
		t.Errorf("a mistyped model should be caught and the catalog offered, got %v", err)
	}
}

// The guard rail must not become a new way for inference to break: if the catalog cannot be
// loaded, the check is skipped rather than failing the request.
func TestModalityCheckSkipsWhenTheCatalogIsUnavailable(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/oauth2/token":
			writeToken(w, "tok", 300)
		case "/v1/models":
			problemJSON(w, 503, "usage-store-unavailable", "catalog is down")
		default:
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]any{
				"id": "c1", "model": "m",
				"choices": []any{map[string]any{"index": 0, "message": map[string]any{"role": "assistant", "content": "went through"}}},
			})
		}
	}))
	defer srv.Close()

	client, err := New(Config{
		AuthBaseURL: srv.URL, GatewayBaseURL: srv.URL,
		ClientID: "id", ClientSecret: "secret", VerifyModality: true, Retry: fastRetry(),
	})
	if err != nil {
		t.Fatalf("building the client: %v", err)
	}
	defer client.Close()

	out, err := client.Chat.Create(context.Background(), simpleRequest())
	if err != nil {
		t.Fatalf("an unavailable catalog must not break inference: %v", err)
	}
	if out.Content() != "went through" {
		t.Errorf("content: got %q", out.Content())
	}
}

// A reasoning model streams its chain of thought before any answer token, in a separate field.
// With a small max_tokens it never leaves that phase: Content stays empty, FinishReason is
// "length", and there is usage to pay for. A caller reading only Content sees an empty answer with
// no explanation, so both are exposed and neither is inferred from the other.
func TestReasoningIsKeptApartFromTheAnswer(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oauth2/token" {
			writeToken(w, "tok", 300)
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		for _, thought := range []string{"Let me", " think"} {
			fmt.Fprintf(w, "data: {\"choices\":[{\"index\":0,\"delta\":{\"reasoning_content\":%q}}]}\n\n", thought)
		}
		fmt.Fprint(w, "data: {\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"length\"}]}\n\n")
		fmt.Fprint(w, "data: [DONE]\n\n")
	}))
	defer srv.Close()

	client := testClient(t, srv.URL)
	stream, err := client.Chat.Stream(context.Background(), simpleRequest())
	if err != nil {
		t.Fatalf("opening the stream: %v", err)
	}
	defer stream.Close()

	var last *ChatCompletionChunk
	for stream.Next() {
		last = stream.Current()
	}
	if stream.Err() != nil {
		t.Fatalf("the stream failed: %v", stream.Err())
	}

	if stream.Content() != "" {
		t.Errorf("the answer must stay empty while the model is only reasoning, got %q", stream.Content())
	}
	if stream.Reasoning() != "Let me think" {
		t.Errorf("reasoning: got %q, want %q", stream.Reasoning(), "Let me think")
	}
	if last == nil || last.FinishReason() != "length" {
		t.Errorf("the caller needs finish_reason to understand the empty answer, got %+v", last)
	}
}

// Extra fields are merged into the encoded request, so sending something the SDK does not model is
// a deliberate act rather than a typo that vanishes.
func TestExtraFieldsReachTheWire(t *testing.T) {
	received := make(chan map[string]any, 1)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oauth2/token" {
			writeToken(w, "tok", 300)
			return
		}
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		received <- body
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"id": "c1", "choices": []any{}})
	}))
	defer srv.Close()

	client := testClient(t, srv.URL)
	req := simpleRequest()
	req.Extra = map[string]any{"repetition_penalty": 1.1}
	if _, err := client.Chat.Create(context.Background(), req); err != nil {
		t.Fatalf("create: %v", err)
	}

	body := <-received
	if body["repetition_penalty"] != 1.1 {
		t.Errorf("the extra field did not reach the wire: %v", body)
	}
	if body["model"] != "llama3-8b-q4" {
		t.Errorf("merging Extra must not disturb the modeled fields: %v", body)
	}
}
