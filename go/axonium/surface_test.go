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

// An error message is the part of an SDK a caller reads at three in the morning, so what it
// contains is behaviour rather than decoration.

func TestAPIErrorMessageCarriesWhatIsNeededToReportIt(t *testing.T) {
	retry := 30.0
	err := &APIError{
		Status: 429, TypeSuffix: "rate-limit-exceeded-requests",
		Detail: "slow down", RequestID: "req-1", TraceID: "trace-1",
		Hint: "budget resets in a minute", RetryAfter: &retry,
	}
	msg := err.Error()
	for _, want := range []string{"slow down", "budget resets", "request_id=req-1", "trace_id=trace-1"} {
		if !strings.Contains(msg, want) {
			t.Errorf("the message should carry %q, got %q", want, msg)
		}
	}

	// Falling back through detail, then title, then the bare status: never an empty message.
	if got := (&APIError{Status: 500, Title: "Server Error"}).Error(); !strings.Contains(got, "Server Error") {
		t.Errorf("got %q", got)
	}
	if got := (&APIError{Status: 503}).Error(); !strings.Contains(got, "503") {
		t.Errorf("an error with nothing to say should still name the status, got %q", got)
	}
}

func TestStreamErrorMessageCarriesCorrelation(t *testing.T) {
	err := &StreamError{Message: "stream interrupted", PartialContent: "half an ans",
		RequestID: "req-9", TraceID: "trace-9"}
	msg := err.Error()
	for _, want := range []string{"stream interrupted", "request_id=req-9", "trace_id=trace-9"} {
		if !strings.Contains(msg, want) {
			t.Errorf("should carry %q, got %q", want, msg)
		}
	}
	if !errors.Is(err, ErrStreamInterrupted) {
		t.Error("a StreamError must match its sentinel")
	}
}

// The type suffix is the last segment of a URI, and the gateway is not obliged to keep sending one
// that looks like a URI. Neither shape may take the parse down.
func TestTypeSuffixExtraction(t *testing.T) {
	for raw, want := range map[string]string{
		"https://prometheus.internal/errors/unknown-model": "unknown-model",
		"https://prometheus.internal/errors/forbidden/":    "forbidden",
		"unknown-model": "unknown-model",
		"":              "",
	} {
		got := errorFromBody(400, map[string]any{"type": raw}, nil, nil).TypeSuffix
		if got != want {
			t.Errorf("%q -> %q, want %q", raw, got, want)
		}
	}

	// A type that is not a string at all is not a suffix, and must not panic.
	if got := errorFromBody(400, map[string]any{"type": 42}, nil, nil).TypeSuffix; got != "" {
		t.Errorf("a non-string type should yield no suffix, got %q", got)
	}
	// retry_after is read off the body when no header supplied one.
	err := errorFromBody(429, map[string]any{"retry_after": 12}, nil, nil)
	if err.RetryAfter == nil || *err.RetryAfter != 12 {
		t.Errorf("body retry_after was not read: %v", err.RetryAfter)
	}
}

func TestRequestValidationForEmbeddingsAndImages(t *testing.T) {
	for _, tc := range []struct {
		name    string
		err     error
		mustSay string
	}{
		{"embeddings without a model", (&EmbeddingRequest{Input: []string{"x"}}).validate(), "model is required"},
		{"embeddings without input", (&EmbeddingRequest{Model: "m"}).validate(), "input must not be empty"},
		{"images without a model", (&ImageRequest{Prompt: "a cat"}).validate(), "model is required"},
		{"images without a prompt", (&ImageRequest{Model: "m"}).validate(), "prompt is required"},
		{"images with negative n", (&ImageRequest{Model: "m", Prompt: "a cat", N: -1}).validate(), "negative"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if !errors.Is(tc.err, ErrInvalidRequest) {
				t.Fatalf("expected a validation error, got %v", tc.err)
			}
			if !strings.Contains(tc.err.Error(), tc.mustSay) {
				t.Errorf("should name the problem (%q), got %q", tc.mustSay, tc.err)
			}
		})
	}

	if err := (&EmbeddingRequest{Model: "m", Input: []string{"x"}}).validate(); err != nil {
		t.Errorf("a valid embeddings request was rejected: %v", err)
	}
	if err := (&ImageRequest{Model: "m", Prompt: "a cat"}).validate(); err != nil {
		t.Errorf("a valid image request was rejected: %v", err)
	}
}

// A 403 says what is missing rather than only that access was refused: deny-by-default grants per
// model, and streaming takes a different scope from non-streaming.
func TestForbiddenNamesTheScopesTheTokenHolds(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oauth2/token" {
			writeToken(w, "tok", 300)
			return
		}
		problemJSON(w, 403, "forbidden", "not authorized for this model")
	}))
	defer srv.Close()

	client := testClient(t, srv.URL)
	_, err := client.Chat.Create(context.Background(), simpleRequest())

	var apiErr *APIError
	if !errors.As(err, &apiErr) || !errors.Is(err, ErrForbidden) {
		t.Fatalf("expected a forbidden error, got %v", err)
	}
	if !strings.Contains(apiErr.Hint, "inference:stream") {
		t.Errorf("the hint should list the scopes actually held, got %q", apiErr.Hint)
	}
}

// The rate-limit budget is exposed so a caller can slow down before a 429 rather than only react
// to one. Absent on responses that carry no such headers, rather than reported as zero.
func TestLastRateLimitTracksTheMostRecentResponse(t *testing.T) {
	var withHeaders bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oauth2/token" {
			writeToken(w, "tok", 300)
			return
		}
		if withHeaders {
			w.Header().Set("X-RateLimit-Limit-Requests", "60")
			w.Header().Set("X-RateLimit-Remaining-Requests", "42")
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"id": "c1", "choices": []any{}})
	}))
	defer srv.Close()

	client := testClient(t, srv.URL)
	if client.LastRateLimit() != nil {
		t.Error("before any response there is no budget to report")
	}

	withHeaders = true
	if _, err := client.Chat.Create(context.Background(), simpleRequest()); err != nil {
		t.Fatalf("create: %v", err)
	}
	rl := client.LastRateLimit()
	if rl == nil || rl.RemainingRequests == nil || *rl.RemainingRequests != 42 {
		t.Fatalf("budget not recorded: %+v", rl)
	}

	// A response with no rate-limit headers must not erase what we last knew.
	withHeaders = false
	if _, err := client.Chat.Create(context.Background(), simpleRequest()); err != nil {
		t.Fatalf("create: %v", err)
	}
	if got := client.LastRateLimit(); got == nil || *got.RemainingRequests != 42 {
		t.Errorf("a response without headers should leave the last known budget alone, got %+v", got)
	}
}

// TokenClaims answers "what am I allowed to call" before a 403 rather than after one. In governed
// mode it deliberately reports nothing: the SDK holds no token there.
func TestTokenClaimsReflectTheCredentialMode(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oauth2/token" {
			writeToken(w, "tok", 300)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"id": "c1", "choices": []any{}})
	}))
	defer srv.Close()

	autonomous := testClient(t, srv.URL)
	if got := autonomous.TokenClaims(); got.Subject != "" || len(got.Scope) != 0 {
		t.Errorf("before any request there is no token to describe, got %+v", got)
	}
	if _, err := autonomous.Chat.Create(context.Background(), simpleRequest()); err != nil {
		t.Fatalf("create: %v", err)
	}
	claims := autonomous.TokenClaims()
	if claims.Subject != "test" {
		t.Errorf("subject: got %q", claims.Subject)
	}
	if len(claims.Scope) == 0 || !strings.Contains(strings.Join(claims.Scope, " "), "inference:read") {
		t.Errorf("scope: got %v", claims.Scope)
	}

	governed, err := New(Config{GatewayBaseURL: srv.URL,
		TokenProvider: func(context.Context, string) (string, error) {
			return makeJWT(map[string]any{"sub": "host", "scope": "inference:read"}) + ".sig", nil
		},
	})
	if err != nil {
		t.Fatalf("building: %v", err)
	}
	defer governed.Close()
	if _, err := governed.Chat.Create(context.Background(), simpleRequest()); err != nil {
		t.Fatalf("create: %v", err)
	}
	if got := governed.TokenClaims(); got.Subject != "" {
		t.Errorf("governed mode holds no token, so it must describe none; got %+v", got)
	}
}
