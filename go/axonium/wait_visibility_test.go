package axonium

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// A retried call must be able to explain its own duration without anyone reading a log.
//
// This SDK does not configure the host application's logging, so the INFO line announcing a wait
// is invisible unless the application opted in. Three teams reported a respected Retry-After as a
// hang because of exactly that. ResponseMeta is the copy of the answer that needs no configuration
// and cannot be missed.

func retryingServer(t *testing.T, failures int) *httptest.Server {
	t.Helper()
	calls := 0
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oauth2/token" {
			writeToken(w, "contract", 300)
			return
		}
		calls++
		if calls <= failures {
			// No Retry-After: the policy's own backoff decides, which is what the assertions below
			// interrogate it about.
			w.Header().Set("Content-Type", "application/problem+json")
			w.WriteHeader(429)
			_, _ = w.Write([]byte(`{"type":"x/rate-limit-exceeded-requests","status":429}`))
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"object":"list","data":[]}`))
	}))
}

func TestMetaReportsTheTotalWaitedAndHowManyAttempts(t *testing.T) {
	srv := retryingServer(t, 2)
	defer srv.Close()

	// Two different delays, deliberately: an implementation that overwrote instead of accumulating
	// would still agree with a single retry, or with two equal ones.
	policy := RetryPolicy{MaxAttempts: 3, InitialBackoff: 40 * time.Millisecond,
		MaxBackoff: time.Second}
	client, err := New(Config{GatewayBaseURL: srv.URL, ClientID: "i", ClientSecret: "s",
		Retry: &policy})
	if err != nil {
		t.Fatalf("building: %v", err)
	}
	defer client.Close()

	// The oracle is the policy itself answering what it would return -- the real function, not a
	// copy of its arithmetic reproduced here. Whether the backoff curve is correct is settled in
	// TestBackoffGrows; what is under test here is that the loop adds up what it actually slept.
	want := policy.backoff(1) + policy.backoff(2)
	if policy.backoff(1) == policy.backoff(2) {
		t.Fatalf("the two delays must differ for this test to have teeth: %v", policy.backoff(1))
	}

	started := time.Now()
	result, err := client.Models.Mine(context.Background())
	elapsed := time.Since(started)
	if err != nil {
		t.Fatalf("the retries should have succeeded: %v", err)
	}

	if got := result.Meta.Attempts; got != 3 {
		t.Errorf("Attempts = %d, want 3", got)
	}
	if got := result.Meta.WaitedFor; got != want {
		t.Errorf("WaitedFor = %v, want %v (the sum of both waits, not the last one)", got, want)
	}
	if elapsed < want {
		t.Errorf("the call took %v but claims to have waited %v -- it cannot have slept that long",
			elapsed, want)
	}
}

func TestMetaOnAFirstTimeSuccessWaitedForNothing(t *testing.T) {
	srv := retryingServer(t, 0)
	defer srv.Close()

	client, err := New(Config{GatewayBaseURL: srv.URL, ClientID: "i", ClientSecret: "s"})
	if err != nil {
		t.Fatalf("building: %v", err)
	}
	defer client.Close()

	result, err := client.Models.Mine(context.Background())
	if err != nil {
		t.Fatalf("listing: %v", err)
	}

	if result.Meta.WaitedFor != 0 {
		t.Errorf("WaitedFor = %v, want 0", result.Meta.WaitedFor)
	}
	// Zero would be a count nothing can be true of: something served this response.
	if result.Meta.Attempts != 1 {
		t.Errorf("Attempts = %d, want 1", result.Meta.Attempts)
	}
}

// Not tested here: what meta says after a call that waited and then failed anyway -- which is the
// call whose duration most needs explaining. Every resource returns `nil, err` on failure and
// discards meta, and APIError carries no ResponseMeta, so today the caller gets nothing. The loop
// stamps the error paths too, so the values are correct the day meta is propagated; asserting on
// them now would be a test of code nothing can reach. Recorded as AXO-91 instead.
