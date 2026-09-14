package axonium

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

// This API has no idempotency mechanism, so a retried generation is a new billable one rather than
// a replay. The policy therefore retries only where the platform said no generation happened.
// These tests are about what is NOT retried as much as what is.

// fastRetry keeps the policy's decisions intact but shrinks the waits, so a test measures which
// errors are retried rather than how long the SDK is willing to sleep. MaxBackoff is small on
// purpose: a server-supplied wait above it is surfaced to the caller instead of slept through, and
// that is the branch these tests want to reach quickly.
func fastRetry() *RetryPolicy {
	p := DefaultRetryPolicy()
	p.InitialBackoff = time.Millisecond
	p.MaxBackoff = 10 * time.Millisecond
	p.Jitter = false
	return &p
}

func TestRetriesOnlyWhereNoGenerationOccurred(t *testing.T) {
	for _, tc := range []struct {
		name        string
		status      int
		suffix      string
		wantRetried bool
		why         string
	}{
		{"rate limit", 429, "rate-limit-exceeded-requests", true, "the request was refused before reaching a model"},
		{"circuit breaker open", 503, "backend-unavailable", true, "the gateway fast-failed without calling the backend"},
		{"upstream error", 502, "upstream-error", false, "the request may have reached a model, so a retry would be a second billable generation"},
		{"unknown model", 400, "unknown-model", false, "the request itself is wrong; repeating it changes nothing"},
		{"forbidden", 403, "forbidden", false, "a missing scope does not appear by waiting"},
		{"spend cap", 402, "spend-cap-exceeded", false, "waiting does not help inside a billing period"},
		{"model not loaded", 503, "model-not-loaded", false, "a 5xx that needs operator action, not patience"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var attempts int64
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/oauth2/token" {
					writeToken(w, "tok", 300)
					return
				}
				atomic.AddInt64(&attempts, 1)
				problemJSON(w, tc.status, tc.suffix, "failing on purpose")
			}))
			defer srv.Close()

			client, err := New(Config{GatewayBaseURL: srv.URL,
				ClientID: "id", ClientSecret: "secret", Retry: fastRetry(),
			})
			if err != nil {
				t.Fatalf("building the client: %v", err)
			}
			defer client.Close()

			if _, err := client.Chat.Create(context.Background(), simpleRequest()); err == nil {
				t.Fatal("expected an error")
			}

			got := atomic.LoadInt64(&attempts)
			if tc.wantRetried && got < 2 {
				t.Errorf("%s should be retried (%s), but was attempted %d time(s)", tc.suffix, tc.why, got)
			}
			if !tc.wantRetried && got != 1 {
				t.Errorf("%s must not be retried (%s), but was attempted %d times", tc.suffix, tc.why, got)
			}
		})
	}
}

// A Retry-After longer than MaxBackoff is handed back rather than slept through: blocking a caller
// for minutes inside one call is worse than telling them, and the error carries the wait so they
// can schedule it themselves.
func TestOverlongRetryAfterIsSurfacedNotSlept(t *testing.T) {
	var attempts int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oauth2/token" {
			writeToken(w, "tok", 300)
			return
		}
		atomic.AddInt64(&attempts, 1)
		w.Header().Set("Retry-After", "3600")
		problemJSON(w, 429, "rate-limit-exceeded-requests", "slow down")
	}))
	defer srv.Close()

	client, err := New(Config{GatewayBaseURL: srv.URL,
		ClientID: "id", ClientSecret: "secret", Retry: fastRetry(),
	})
	if err != nil {
		t.Fatalf("building the client: %v", err)
	}
	defer client.Close()

	start := time.Now()
	_, err = client.Chat.Create(context.Background(), simpleRequest())
	elapsed := time.Since(start)

	if elapsed > 5*time.Second {
		t.Fatalf("the SDK slept through a one-hour Retry-After (%v elapsed)", elapsed)
	}
	if n := atomic.LoadInt64(&attempts); n != 1 {
		t.Errorf("expected a single attempt, got %d", n)
	}

	var apiErr *APIError
	if !errors.As(err, &apiErr) || apiErr.RetryAfter == nil || *apiErr.RetryAfter != 3600 {
		t.Fatalf("the wait must reach the caller so they can schedule it, got %v", err)
	}
}

// A hostile or broken Retry-After must not reach a sleep. Negative, NaN and infinite values are
// discarded rather than propagated.
func TestMalformedRetryAfterIsDiscarded(t *testing.T) {
	for _, raw := range []string{"-100", "NaN", "Inf", "not-a-number", "1e400"} {
		h := http.Header{}
		h.Set("Retry-After", raw)
		got := retryAfterSeconds(h)
		if got != nil && (*got < 0 || *got > 1e307) {
			t.Errorf("Retry-After %q produced an unusable wait: %v", raw, *got)
		}
	}
}

// A client-side timeout is never retried: the backend is probably still generating, and a retry
// would queue a second billable generation on top of the first.
func TestClientTimeoutIsNotRetried(t *testing.T) {
	var attempts int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oauth2/token" {
			writeToken(w, "tok", 300)
			return
		}
		atomic.AddInt64(&attempts, 1)
		time.Sleep(2 * time.Second)
	}))
	defer srv.Close()

	client, err := New(Config{GatewayBaseURL: srv.URL,
		ClientID: "id", ClientSecret: "secret", Retry: fastRetry(),
		Timeouts: Timeouts{Request: 200 * time.Millisecond},
	})
	if err != nil {
		t.Fatalf("building the client: %v", err)
	}
	defer client.Close()

	if _, err = client.Chat.Create(context.Background(), simpleRequest()); !errors.Is(err, ErrTimeout) {
		t.Fatalf("expected a timeout, got %v", err)
	}
	if n := atomic.LoadInt64(&attempts); n != 1 {
		t.Fatalf("a client timeout must not be retried, but the gateway saw %d attempts", n)
	}
}

// A cooldown the gateway asked for is honored locally: sending anyway just buys another 503.
func TestCooldownIsHonoredWithoutARequest(t *testing.T) {
	var attempts int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oauth2/token" {
			writeToken(w, "tok", 300)
			return
		}
		atomic.AddInt64(&attempts, 1)
		w.Header().Set("Retry-After", "30")
		problemJSON(w, 503, "backend-unavailable", "circuit breaker open")
	}))
	defer srv.Close()

	client, err := New(Config{GatewayBaseURL: srv.URL,
		ClientID: "id", ClientSecret: "secret", Retry: fastRetry(),
	})
	if err != nil {
		t.Fatalf("building the client: %v", err)
	}
	defer client.Close()

	_, _ = client.Chat.Create(context.Background(), simpleRequest())
	first := atomic.LoadInt64(&attempts)

	_, err = client.Chat.Create(context.Background(), simpleRequest())
	if atomic.LoadInt64(&attempts) != first {
		t.Error("the second call reached the gateway despite an unexpired cooldown")
	}
	if !errors.Is(err, ErrBackendUnavailable) {
		t.Fatalf("expected the local refusal to keep its own error identity, got %v", err)
	}
}

// The reactive 401 path: a token revoked mid-flight is replaced and the request replayed once.
func TestReactiveRefreshReplaysOnceAfter401(t *testing.T) {
	var tokenRequests, inferenceAttempts int64
	var firstToken atomic.Value

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oauth2/token" {
			n := atomic.AddInt64(&tokenRequests, 1)
			writeToken(w, "tok", 300)
			_ = n
			return
		}
		atomic.AddInt64(&inferenceAttempts, 1)
		auth := r.Header.Get("Authorization")
		if firstToken.Load() == nil {
			firstToken.Store(auth)
		}
		if auth == firstToken.Load() {
			problemJSON(w, 401, "token-expired", "the token has expired")
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"id": "c1", "model": "m",
			"choices": []any{map[string]any{"index": 0, "message": map[string]any{"role": "assistant", "content": "recovered"}, "finish_reason": "stop"}},
		})
	}))
	defer srv.Close()

	client, err := New(Config{GatewayBaseURL: srv.URL,
		ClientID: "id", ClientSecret: "secret", Retry: fastRetry(),
	})
	if err != nil {
		t.Fatalf("building the client: %v", err)
	}
	defer client.Close()

	out, err := client.Chat.Create(context.Background(), simpleRequest())
	if err != nil {
		t.Fatalf("the reactive refresh should have recovered this: %v", err)
	}
	if out.Content() != "recovered" {
		t.Errorf("content: got %q", out.Content())
	}
	if n := atomic.LoadInt64(&inferenceAttempts); n != 2 {
		t.Errorf("expected exactly one replay after the 401, got %d attempts", n)
	}
}

// A response bigger than whatever the transport happened to buffer must still be readable.
//
// This pins a real bug: the per-request timeout context was cancelled when the send returned, and
// a cancelled context closes the response body, so anything not already buffered failed with
// "context canceled". Small fixtures hid it completely -- they arrive in one segment. A real
// embeddings response, tens of kilobytes of floats, does not.
func TestLargeResponseBodySurvivesTheRequestTimeout(t *testing.T) {
	const vectors, dims = 24, 1024

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oauth2/token" {
			writeToken(w, "tok", 300)
			return
		}
		data := make([]any, 0, vectors)
		for i := 0; i < vectors; i++ {
			vec := make([]float64, dims)
			for j := range vec {
				vec[j] = float64(j) / 1000
			}
			data = append(data, map[string]any{"object": "embedding", "index": i, "embedding": vec})
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"object": "list", "model": "embed", "data": data,
			"usage": map[string]any{"prompt_tokens": 2, "total_tokens": 2},
		})
	}))
	defer srv.Close()

	client := testClient(t, srv.URL)
	out, err := client.Embeddings.Create(context.Background(), EmbeddingRequest{
		Model: "embed", Input: []string{"a", "b"},
	})
	if err != nil {
		t.Fatalf("a large body must survive the request timeout scope: %v", err)
	}
	if len(out.Data) != vectors {
		t.Fatalf("got %d vectors, want %d", len(out.Data), vectors)
	}
	if len(out.Data[vectors-1].Embedding) != dims {
		t.Fatalf("the last vector was truncated: %d dims", len(out.Data[vectors-1].Embedding))
	}
}

// A pin is kept across a retry, confirmed by the platform team: a pin never silently falls back,
// and dropping it on retry would answer a different question than the caller asked.
func TestInstancePinSurvivesRetries(t *testing.T) {
	var seen []string
	var attempts int64

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oauth2/token" {
			writeToken(w, "tok", 300)
			return
		}
		seen = append(seen, r.Header.Get("X-Prometheus-Instance"))
		if atomic.AddInt64(&attempts, 1) == 1 {
			problemJSON(w, 429, "rate-limit-exceeded-requests", "slow down")
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Prometheus-Instance", "#2")
		w.Header().Set("X-Prometheus-Instance-Id", "qwen3-0-6b-iq4-nl-local-1")
		_ = json.NewEncoder(w).Encode(map[string]any{"id": "c1", "model": "qwen3-0.6b", "choices": []any{}})
	}))
	defer srv.Close()

	client, err := New(Config{GatewayBaseURL: srv.URL,
		ClientID: "id", ClientSecret: "secret", Retry: fastRetry(),
	})
	if err != nil {
		t.Fatalf("building the client: %v", err)
	}
	defer client.Close()

	req := simpleRequest()
	req.Instance = "#2"
	out, err := client.Chat.Create(context.Background(), req)
	if err != nil {
		t.Fatalf("create: %v", err)
	}

	if len(seen) != 2 {
		t.Fatalf("expected one retry, the gateway saw %d requests", len(seen))
	}
	for i, pin := range seen {
		if pin != "#2" {
			t.Errorf("attempt %d carried pin %q, want %q -- a dropped pin silently changes the question", i+1, pin, "#2")
		}
	}
	if out.Meta.Instance != "#2" || out.Meta.InstanceID != "qwen3-0-6b-iq4-nl-local-1" {
		t.Errorf("the serving instance must reach the caller: %+v", out.Meta)
	}
}

// A cooldown is scoped to one model. 503 backend-unavailable means every instance of THAT model is
// out; other models on the same gateway keep serving, so cooling the gateway would refuse requests
// the platform would have answered.
func TestCooldownIsScopedToOneModel(t *testing.T) {
	var reached []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oauth2/token" {
			writeToken(w, "tok", 300)
			return
		}
		var body struct {
			Model string `json:"model"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		reached = append(reached, body.Model)

		if body.Model == "down-model" {
			w.Header().Set("Retry-After", "30")
			problemJSON(w, 503, "backend-unavailable", "every instance of down-model is out")
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"id": "c1", "choices": []any{}})
	}))
	defer srv.Close()

	client, err := New(Config{GatewayBaseURL: srv.URL,
		ClientID: "id", ClientSecret: "secret", Retry: fastRetry(),
	})
	if err != nil {
		t.Fatalf("building the client: %v", err)
	}
	defer client.Close()

	down := simpleRequest()
	down.Model = "down-model"
	if _, err := client.Chat.Create(context.Background(), down); err == nil {
		t.Fatal("expected the unavailable model to fail")
	}

	// The healthy model must still reach the gateway.
	healthy := simpleRequest()
	healthy.Model = "healthy-model"
	if _, err := client.Chat.Create(context.Background(), healthy); err != nil {
		t.Fatalf("a cooldown on one model must not block another: %v", err)
	}
	if got := reached[len(reached)-1]; got != "healthy-model" {
		t.Errorf("the healthy model never reached the gateway; last seen %q", got)
	}

	// ...while the cooled one is still refused locally, without a request.
	before := len(reached)
	if _, err := client.Chat.Create(context.Background(), down); !errors.Is(err, ErrBackendUnavailable) {
		t.Fatalf("the cooled model should still be refused locally, got %v", err)
	}
	if len(reached) != before {
		t.Error("the cooled model reached the gateway despite an unexpired cooldown")
	}
}
