package axonium

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
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

// The modality check refuses before the wire, and only when it knows enough to.
//
// It was named for the gateway's one-directional check, which no longer exists: RM-66 made the
// server-side check hold in every direction. The test survives the premise because what it asserts
// is the request COUNT -- 0 for a wrong modality, exactly 1 for a model absent from the catalog --
// and that is still the behaviour worth pinning now that the saving is a round trip rather than a
// billed generation.
func TestModalityCheckRefusesBeforeTheWire(t *testing.T) {
	var chatCalls atomic.Int64
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
		// Reaching here is correct for an ABSENT model and wrong for a visible one whose
		// modality does not fit. Counted rather than rejected, so the two cases can be told
		// apart by a number instead of by a server-side assertion that cannot distinguish them.
		chatCalls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"choices": []any{map[string]any{"index": 0, "message": map[string]any{"content": "hi"}}},
		})
	}))
	defer srv.Close()

	client, err := New(Config{GatewayBaseURL: srv.URL,
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
	// This asserted "bill", and was right when written: the gateway answered 200 with degenerate
	// billable output here. RM-66 made its own check hold in every direction, so the local refusal
	// now saves a request and a rate-limit unit instead. Nothing went red when that premise died --
	// the code was still correct and still produced a message -- which is why a mutation could not
	// have found it and only measuring the live gateway did. What is asserted now is that the
	// message says the gateway refuses it too, so nobody reads a local refusal as the only thing
	// standing between them and a bill.
	if !strings.Contains(err.Error(), "refuses this combination too") {
		t.Errorf("the message should say the gateway refuses it too, got %q", err)
	}
	if n := chatCalls.Load(); n != 0 {
		t.Errorf("a visible model with the wrong modality must not reach the gateway; %d did", n)
	}

	// A model absent from the catalog is NOT caught, and that is the point of PRM-167.
	//
	// This used to assert the opposite -- that a typo is refused locally and the message lists
	// what is available -- and it was right while the catalog was the platform's full public
	// list. Since PRM-167 it holds only the models this token has a grant for, so absence has two
	// causes this SDK cannot tell apart: not registered, which the gateway answers as
	// 400 unknown-model, and not granted, which it answers as 403 forbidden.
	//
	// Refusing here told a caller to check a name that was spelled correctly, and pre-empted the
	// 403 whose entire job is to name the missing scope. So the request goes, and the error that
	// comes back is one only the gateway could produce. A typo costs a round trip; a missing grant
	// gets diagnosed. That is the right way round.
	_, err = client.Chat.Create(context.Background(), ChatRequest{
		Model:    "llama3-8b-q5",
		Messages: []Message{TextMessage("user", "hi")},
	})
	if errors.Is(err, ErrInvalidRequest) {
		t.Errorf("an absent model must be left to the gateway, which can tell 400 from 403; got %v", err)
	}
	if n := chatCalls.Load(); n != 1 {
		t.Errorf("the absent model should have reached the gateway exactly once; %d did", n)
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

	client, err := New(Config{GatewayBaseURL: srv.URL,
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

// TestTopLogprobsRequiresLogprobs holds the contract case PRM-187 introduced.
//
// The rule is the ENGINE's -- llama.cpp answers "top_logprobs requires logprobs to be set to true"
// -- and the gateway enforces it before forwarding so the refusal arrives as problem+json. Checking
// it here is not duplicating the gateway's job: it is the difference between learning it at the call
// site and learning it after a round trip. No recorded corpus case covers it, so this is the only
// thing holding the rule in this SDK.
func TestTopLogprobsRequiresLogprobs(t *testing.T) {
	three := 3
	twentyOne := 21
	yes, no := true, false

	for name, req := range map[string]ChatRequest{
		"alone":        {Model: "m", Messages: []Message{TextMessage("user", "x")}, TopLogprobs: &three},
		"with false":   {Model: "m", Messages: []Message{TextMessage("user", "x")}, Logprobs: &no, TopLogprobs: &three},
		"out of range": {Model: "m", Messages: []Message{TextMessage("user", "x")}, Logprobs: &yes, TopLogprobs: &twentyOne},
	} {
		t.Run(name, func(t *testing.T) {
			if err := req.validate(); err == nil {
				t.Fatal("accepted a request the gateway answers 422 to")
			}
		})
	}

	// The asymmetry is the point: logprobs on its own is a complete request.
	ok := ChatRequest{Model: "m", Messages: []Message{TextMessage("user", "x")}, Logprobs: &yes}
	if err := ok.validate(); err != nil {
		t.Fatalf("logprobs alone was refused: %v", err)
	}
}

// TestProbabilityIsExpOfTheLogprob guards the one number a caller is most likely to misread.
//
// -0.00054 is ~99.95%, not ~0. Read as a probability it looks like a number near zero meaning
// "unlikely", and nothing about the mistake is loud.
func TestProbabilityIsExpOfTheLogprob(t *testing.T) {
	got := TokenLogprob{Token: "yes", Logprob: -0.00054}.Probability()
	if got < 0.999 || got > 1.0 {
		t.Fatalf("Probability() = %v, which is not exp(-0.00054)", got)
	}
}
