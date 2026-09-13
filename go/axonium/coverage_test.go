package axonium

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// Embeddings and images through a real round trip, including the fields that only exist there.
func TestEmbeddingsAndImagesEndToEnd(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.URL.Path == "/oauth2/token":
			writeToken(w, "tok", 300)
		case strings.HasSuffix(r.URL.Path, "/embeddings"):
			_ = json.NewEncoder(w).Encode(map[string]any{
				"object": "list", "model": "qwen3-embedding",
				"data":  []any{map[string]any{"object": "embedding", "index": 0, "embedding": []float64{0.1, 0.2}}},
				"usage": map[string]any{"prompt_tokens": 6, "total_tokens": 6},
			})
		default:
			_ = json.NewEncoder(w).Encode(map[string]any{
				"created": 1, "model": "sd-turbo", "output_format": "png",
				"data": []any{map[string]any{"b64_json": "aGk="}},
			})
		}
	}))
	defer srv.Close()

	client := testClient(t, srv.URL)
	ctx := context.Background()

	embeddings, err := client.Embeddings.Create(ctx, EmbeddingRequest{Model: "qwen3-embedding", Input: []string{"x"}})
	if err != nil {
		t.Fatalf("embeddings: %v", err)
	}
	if len(embeddings.Data) != 1 || len(embeddings.Data[0].Embedding) != 2 {
		t.Errorf("vectors: %+v", embeddings.Data)
	}
	// There is no generation phase, so this counter is absent rather than zero.
	if embeddings.Usage.CompletionTokens != nil {
		t.Errorf("embeddings have no completion tokens, got %v", *embeddings.Usage.CompletionTokens)
	}
	if embeddings.Raw == nil {
		t.Error("the decoded body should stay reachable")
	}

	images, err := client.Images.Generate(ctx, ImageRequest{Model: "sd-turbo", Prompt: "a cat"})
	if err != nil {
		t.Fatalf("images: %v", err)
	}
	if images.OutputFormat != "png" {
		t.Errorf("output format: %q", images.OutputFormat)
	}
	decoded, err := images.Data[0].Bytes()
	if err != nil || string(decoded) != "hi" {
		t.Errorf("decoding: %q %v", decoded, err)
	}

	// Validation runs before the wire on both.
	if _, err := client.Embeddings.Create(ctx, EmbeddingRequest{}); !errors.Is(err, ErrInvalidRequest) {
		t.Errorf("embeddings validation: %v", err)
	}
	if _, err := client.Images.Generate(ctx, ImageRequest{}); !errors.Is(err, ErrInvalidRequest) {
		t.Errorf("images validation: %v", err)
	}
}

// In governed mode the SDK holds no token, so a 403 is diagnosed from the scopes of the last one
// applied -- metadata it keeps deliberately, while keeping no credential.
func TestGovernedModeStillDiagnosesScopes(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		problemJSON(w, 403, "forbidden", "not authorized")
	}))
	defer srv.Close()

	token := makeJWT(map[string]any{"scope": "inference:read model:qwen3-0.6b"}) + ".sig"
	client, err := New(Config{
		AuthBaseURL: srv.URL, GatewayBaseURL: srv.URL,
		TokenProvider: func(context.Context, string) (string, error) { return token, nil },
	})
	if err != nil {
		t.Fatalf("building: %v", err)
	}
	defer client.Close()

	_, err = client.Chat.Create(context.Background(), simpleRequest())
	var apiErr *APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("expected an APIError, got %v", err)
	}
	if !strings.Contains(apiErr.Hint, "inference:read") {
		t.Errorf("the scopes of the applied token should still be reportable, got %q", apiErr.Hint)
	}
	if client.Config().ClientSecret != "" {
		t.Error("governed mode must hold no secret")
	}
}

func TestProviderErrorsSurfaceWhereTheyHappen(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	defer srv.Close()

	boom := errors.New("the host's vault is unreachable")
	client, err := New(Config{
		AuthBaseURL: srv.URL, GatewayBaseURL: srv.URL,
		TokenProvider: func(context.Context, string) (string, error) { return "", boom },
	})
	if err != nil {
		t.Fatalf("building: %v", err)
	}
	defer client.Close()

	_, err = client.Chat.Create(context.Background(), simpleRequest())
	if !errors.Is(err, ErrAuthTransport) {
		t.Fatalf("a provider failure is an auth transport failure, got %v", err)
	}
	if !errors.Is(err, boom) {
		t.Error("the host's own error must stay reachable through the wrapper")
	}
}

// The modality preflight is a guard rail, so an unreachable catalog must not become a new way for
// inference to fail.
func TestModalityCheckIsSilentOnAnUnknownModality(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/oauth2/token":
			writeToken(w, "tok", 300)
		case "/v1/models":
			_ = json.NewEncoder(w).Encode(map[string]any{"object": "list", "data": []any{
				map[string]any{"id": "future-model", "modality": "holographic"},
				map[string]any{"id": "undeclared"},
			}})
		default:
			_ = json.NewEncoder(w).Encode(map[string]any{"id": "c1", "choices": []any{}})
		}
	}))
	defer srv.Close()

	client, err := New(Config{
		AuthBaseURL: srv.URL, GatewayBaseURL: srv.URL,
		ClientID: "i", ClientSecret: "s", VerifyModality: true,
	})
	if err != nil {
		t.Fatalf("building: %v", err)
	}
	defer client.Close()

	for _, model := range []string{"future-model", "undeclared"} {
		req := simpleRequest()
		req.Model = model
		if _, err := client.Chat.Create(context.Background(), req); err != nil {
			t.Errorf("%s: a modality this SDK does not recognise must not block the call: %v", model, err)
		}
	}
}

func TestSSEEdges(t *testing.T) {
	// A structured error object is described rather than dropped, so the caller learns what broke.
	var acc accumulator
	_, err := acc.feed(sseEvent{payload: map[string]any{"error": map[string]any{"code": 500}}}, ResponseMeta{})
	var streamErr *StreamError
	if !errors.As(err, &streamErr) || !strings.Contains(streamErr.Message, "500") {
		t.Errorf("a structured error should be rendered, got %v", err)
	}

	// Something unserialisable degrades to a placeholder rather than taking the stream down.
	_, err = acc.feed(sseEvent{payload: map[string]any{"error": make(chan int)}}, ResponseMeta{})
	if err == nil {
		t.Error("any error key must interrupt the stream")
	}

	// Non-numeric timings contribute nothing rather than being coerced.
	acc2 := accumulator{timings: map[string]any{"prompt_n": "seven", "predicted_n": 3.0}}
	usage := acc2.finalUsage()
	if usage == nil || *usage.PromptTokens != 0 || *usage.CompletionTokens != 3 {
		t.Errorf("a non-numeric timing should count as zero, got %+v", usage)
	}
}
