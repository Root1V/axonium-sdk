package axonium

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// A client-side timeout is the one failure this SDK refuses to retry, because the backend is
// probably still generating and a retry would queue a second billable generation. An
// Idempotency-Key removes that objection and nothing else does, so the rule is conditional on the
// key rather than relaxed outright.

// keyRecorder collects the keys the gateway saw. It is mutex-guarded on purpose: the handler for a
// request the client has already timed out is still running, so the test reads this while that
// goroutine may still be writing. The race detector catches it without the lock.
type keyRecorder struct {
	mu   sync.Mutex
	keys []string
}

func (k *keyRecorder) add(key string) {
	k.mu.Lock()
	defer k.mu.Unlock()
	k.keys = append(k.keys, key)
}

func (k *keyRecorder) seen() []string {
	k.mu.Lock()
	defer k.mu.Unlock()
	return append([]string(nil), k.keys...)
}

func slowThenFast(t *testing.T, attempts *int64, rec *keyRecorder) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oauth2/token" {
			writeToken(w, "tok", 300)
			return
		}
		rec.add(r.Header.Get("Idempotency-Key"))
		if atomic.AddInt64(attempts, 1) == 1 {
			time.Sleep(400 * time.Millisecond) // outlive the request timeout
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Idempotent-Replay", "true")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"id": "c1", "model": "qwen3-0.6b",
			"choices": []any{map[string]any{"index": 0, "message": map[string]any{"role": "assistant", "content": "ok"}}},
		})
	}))
	t.Cleanup(srv.Close)
	return srv
}

func keyedClient(t *testing.T, url string) *Client {
	t.Helper()
	policy := fastRetry()
	client, err := New(Config{
		AuthBaseURL: url, GatewayBaseURL: url, ClientID: "id", ClientSecret: "secret",
		Retry: policy, Timeouts: Timeouts{Request: 150 * time.Millisecond},
	})
	if err != nil {
		t.Fatalf("building the client: %v", err)
	}
	t.Cleanup(func() { _ = client.Close() })
	return client
}

func TestTimeoutIsRetriedUnderAnIdempotencyKey(t *testing.T) {
	var attempts int64
	rec := &keyRecorder{}
	client := keyedClient(t, slowThenFast(t, &attempts, rec).URL)

	req := simpleRequest()
	req.IdempotencyKey = "key-1"
	out, err := client.Chat.Create(context.Background(), req)
	if err != nil {
		t.Fatalf("a keyed timeout should have been retried: %v", err)
	}
	if out.Content() != "ok" {
		t.Errorf("content: got %q", out.Content())
	}
	seen := rec.seen()
	if len(seen) != 2 {
		t.Fatalf("expected one retry, the gateway saw %d requests", len(seen))
	}
	for i, k := range seen {
		if k != "key-1" {
			t.Errorf("attempt %d carried key %q; without the same key the retry is a new generation", i+1, k)
		}
	}
	if !out.Meta.IdempotentReplay {
		t.Error("a replayed response must be flagged: its usage describes the original generation")
	}
}

func TestTimeoutWithoutAKeyIsNeverRetried(t *testing.T) {
	var attempts int64
	client := keyedClient(t, slowThenFast(t, &attempts, &keyRecorder{}).URL)

	if _, err := client.Chat.Create(context.Background(), simpleRequest()); !errors.Is(err, ErrTimeout) {
		t.Fatalf("expected a timeout, got %v", err)
	}
	if n := atomic.LoadInt64(&attempts); n != 1 {
		t.Fatalf("retrying an unkeyed timeout queues a second billable generation; saw %d attempts", n)
	}
}

func TestIdempotencyConflictIsTypedAndNotRetried(t *testing.T) {
	var attempts int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oauth2/token" {
			writeToken(w, "tok", 300)
			return
		}
		atomic.AddInt64(&attempts, 1)
		problemJSON(w, 409, "idempotency-conflict", "was already used for a different request")
	}))
	defer srv.Close()

	client := testClient(t, srv.URL)
	req := simpleRequest()
	req.IdempotencyKey = "reused"
	_, err := client.Chat.Create(context.Background(), req)

	if !errors.Is(err, ErrIdempotencyConflict) {
		t.Fatalf("expected a typed conflict, got %v", err)
	}
	// Three causes share this type and are told apart only by prose. Two must never be retried and
	// one is resolved by waiting, so not retrying is the safe default for all three.
	if n := atomic.LoadInt64(&attempts); n != 1 {
		t.Errorf("a conflict must not be retried, saw %d attempts", n)
	}
}

func TestStreamRefusesAnIdempotencyKey(t *testing.T) {
	client := testClient(t, "http://unused.invalid")
	req := simpleRequest()
	req.IdempotencyKey = "key-1"

	_, err := client.Chat.Stream(context.Background(), req)
	if !errors.Is(err, ErrInvalidRequest) {
		t.Fatalf("streaming must refuse a key rather than drop it, got %v", err)
	}
	if !strings.Contains(err.Error(), "ignores it") {
		t.Errorf("the message should say why, got %q", err)
	}
}
