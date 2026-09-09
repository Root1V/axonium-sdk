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

			client, err := New(Config{
				AuthBaseURL: srv.URL, GatewayBaseURL: srv.URL,
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

	client, err := New(Config{
		AuthBaseURL: srv.URL, GatewayBaseURL: srv.URL,
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

	client, err := New(Config{
		AuthBaseURL: srv.URL, GatewayBaseURL: srv.URL,
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

	client, err := New(Config{
		AuthBaseURL: srv.URL, GatewayBaseURL: srv.URL,
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

	client, err := New(Config{
		AuthBaseURL: srv.URL, GatewayBaseURL: srv.URL,
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
