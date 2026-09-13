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

func TestIdempotencyKeyReuseIsTypedAndNotRetried(t *testing.T) {
	var attempts int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oauth2/token" {
			writeToken(w, "tok", 300)
			return
		}
		atomic.AddInt64(&attempts, 1)
		problemJSON(w, 409, "idempotency-key-reuse", "was already used for a different request")
	}))
	defer srv.Close()

	client := testClient(t, srv.URL)
	req := simpleRequest()
	req.IdempotencyKey = "reused"
	_, err := client.Chat.Create(context.Background(), req)

	if !errors.Is(err, ErrIdempotencyKeyReuse) {
		t.Fatalf("expected a typed reuse, got %v", err)
	}
	// Reuse can never be fixed by repeating: the caller needs a fresh key per logical request.
	if n := atomic.LoadInt64(&attempts); n != 1 {
		t.Errorf("a conflict must not be retried, saw %d attempts", n)
	}
}

// Streaming now accepts a key. If the gateway's stream completed and the caller's connection
// dropped, the key replays the stored frames; if the model's own stream broke there is nothing to
// replay and the retry generates again. Either way the key must reach the wire.
func TestStreamSendsAnIdempotencyKey(t *testing.T) {
	got := make(chan string, 1)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oauth2/token" {
			writeToken(w, "tok", 300)
			return
		}
		got <- r.Header.Get("Idempotency-Key")
		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("Idempotent-Replay", "true")
		_, _ = w.Write([]byte("data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"hi\"}}]}\n\ndata: [DONE]\n\n"))
	}))
	defer srv.Close()

	client := testClient(t, srv.URL)
	req := simpleRequest()
	req.IdempotencyKey = "key-1"

	stream, err := client.Chat.Stream(context.Background(), req)
	if err != nil {
		t.Fatalf("streaming must accept a key: %v", err)
	}
	defer stream.Close()
	for stream.Next() {
	}

	if sent := <-got; sent != "key-1" {
		t.Errorf("the key did not reach the wire: got %q", sent)
	}
	if !stream.Meta().IdempotentReplay {
		t.Error("a replayed stream must be flagged: it was not generated again and was not charged again")
	}
}

// An over-length key is refused before the wire. The gateway reports it as 409
// idempotency-conflict -- the same type a genuine reuse produces -- so a caller branching on that
// would conclude they had repeated a request when their key is merely too long.
func TestOverlongIdempotencyKeyIsRefusedLocally(t *testing.T) {
	var reached int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oauth2/token" {
			writeToken(w, "tok", 300)
			return
		}
		atomic.AddInt64(&reached, 1)
	}))
	defer srv.Close()

	client := testClient(t, srv.URL)
	req := simpleRequest()
	req.IdempotencyKey = strings.Repeat("x", 256)

	_, err := client.Chat.Create(context.Background(), req)
	if !errors.Is(err, ErrInvalidRequest) {
		t.Fatalf("expected a local rejection naming the real problem, got %v", err)
	}
	if errors.Is(err, ErrIdempotencyKeyReuse) {
		t.Error("a too-long key is not a reuse; saying so would send the caller hunting a repeat that never happened")
	}
	if n := atomic.LoadInt64(&reached); n != 0 {
		t.Errorf("the request should not have been sent, but the gateway saw %d", n)
	}
	if !strings.Contains(err.Error(), "255") {
		t.Errorf("the message should name the limit, got %q", err)
	}
}
