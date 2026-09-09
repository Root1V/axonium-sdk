package axonium

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
)

// hostTokenSource stands in for the credential owner in the governed mode -- the shape Aeon's
// TokenSource has. It deduplicates by comparing the rejected token against what it holds, which is
// the whole reason the contract passes the token rather than a boolean.
type hostTokenSource struct {
	mu      sync.Mutex
	current string
	minted  int64
}

func (h *hostTokenSource) Token(_ context.Context, rejected string) (string, error) {
	h.mu.Lock()
	defer h.mu.Unlock()

	// If what we hold already differs from the rejected one, someone else refreshed: hand back
	// what we have. With a boolean this comparison is impossible, and the only alternatives are
	// minting twice or guessing with a time window.
	if rejected != "" && rejected != h.current {
		return h.current, nil
	}
	if rejected == "" && h.current != "" {
		return h.current, nil
	}

	n := atomic.AddInt64(&h.minted, 1)
	h.current = makeJWT(map[string]any{"scope": "inference:read model:llama3-8b-q4"}) + fmt.Sprintf(".v%d", n)
	return h.current, nil
}

// TestGovernedModeDeduplicatesConcurrentRejections is the Go half of the tri-party TokenProvider
// agreement: the provider receives the token that was rejected, so N concurrent callers hitting a
// 401 on the same dead token cause exactly one minting, not N.
func TestGovernedModeDeduplicatesConcurrentRejections(t *testing.T) {
	host := &hostTokenSource{}

	var rejectedOnce sync.Once
	dead := ""
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		auth := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")

		// The first token minted is treated as revoked mid-flight, so every caller holding it gets
		// a 401 at roughly the same moment -- the stampede this contract exists to prevent.
		rejectedOnce.Do(func() { dead = auth })
		if auth == dead {
			problemJSON(w, http.StatusUnauthorized, "token-expired", "the token has expired")
			return
		}

		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"id": "c1", "model": "llama3-8b-q4",
			"choices": []any{map[string]any{"index": 0, "message": map[string]any{"role": "assistant", "content": "ok"}, "finish_reason": "stop"}},
		})
	}))
	defer srv.Close()

	client, err := New(Config{
		AuthBaseURL:    srv.URL,
		GatewayBaseURL: srv.URL,
		TokenProvider:  host.Token,
	})
	if err != nil {
		t.Fatalf("building the client: %v", err)
	}
	defer client.Close()

	const callers = 10
	var wg sync.WaitGroup
	errs := make([]error, callers)
	for i := 0; i < callers; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			_, errs[i] = client.Chat.Create(context.Background(), simpleRequest())
		}(i)
	}
	wg.Wait()

	for i, err := range errs {
		if err != nil {
			t.Fatalf("caller %d failed: %v", i, err)
		}
	}

	// One dead token, one replacement. More than two mintings means the rejection rounds were not
	// deduplicated and every caller minted its own.
	if minted := atomic.LoadInt64(&host.minted); minted > 2 {
		t.Fatalf("%d concurrent callers on one dead token caused %d mintings; the rejected-token contract should collapse them into one replacement", callers, minted)
	}
}

// TestGovernedModeHoldsNoSecret checks the claim the mode exists to make: that "the SDK never holds
// a long-lived secret" is a fact about the object, not a statement about which code path reads what.
func TestGovernedModeHoldsNoSecret(t *testing.T) {
	t.Setenv("AXONIUM_CLIENT_ID", "id-from-environment")
	t.Setenv("AXONIUM_CLIENT_SECRET", "secret-from-environment")

	host := &hostTokenSource{}
	client, err := New(Config{
		AuthBaseURL:    "https://auth.example",
		GatewayBaseURL: "https://gateway.example",
		TokenProvider:  host.Token,
	})
	if err != nil {
		t.Fatalf("environment credentials must not block the governed mode, since a host process almost always has them set: %v", err)
	}
	defer client.Close()

	cfg := client.Config()
	if cfg.ClientID != "" || cfg.ClientSecret != "" {
		t.Fatalf("environment credentials were retained (id=%q secret set=%v); they must be discarded, not merely unused", cfg.ClientID, cfg.ClientSecret != "")
	}
}

// TestBothModesByNameIsRefused: asking for both explicitly is a contradiction about who owns the
// credential, and guessing which one the caller meant would be worse than saying so.
func TestBothModesByNameIsRefused(t *testing.T) {
	_, err := New(Config{
		AuthBaseURL:    "https://auth.example",
		GatewayBaseURL: "https://gateway.example",
		ClientID:       "explicit",
		ClientSecret:   "explicit",
		TokenProvider:  func(context.Context, string) (string, error) { return "t", nil },
	})
	if !errors.Is(err, ErrConfiguration) {
		t.Fatalf("expected a configuration error, got %v", err)
	}
}

// TestNoCredentialsAtAllIsRefused: failing at construction beats failing on the first request with
// a confusing 401.
func TestNoCredentialsAtAllIsRefused(t *testing.T) {
	for _, v := range []string{"AXONIUM_CLIENT_ID", "AXONIUM_CLIENT_SECRET"} {
		t.Setenv(v, "")
		_ = os.Unsetenv(v)
	}
	_, err := New(Config{AuthBaseURL: "https://auth.example", GatewayBaseURL: "https://gateway.example"})
	if !errors.Is(err, ErrConfiguration) {
		t.Fatalf("expected a configuration error, got %v", err)
	}
}

// TestProviderErrorIsReportedNotSwallowed: an empty token would become "Authorization: Bearer " and
// a 401 whose cause is invisible at the call site.
func TestProviderEmptyTokenIsRejected(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	defer srv.Close()

	client, err := New(Config{
		AuthBaseURL:    srv.URL,
		GatewayBaseURL: srv.URL,
		TokenProvider:  func(context.Context, string) (string, error) { return "", nil },
	})
	if err != nil {
		t.Fatalf("building the client: %v", err)
	}
	defer client.Close()

	_, err = client.Chat.Create(context.Background(), simpleRequest())
	if !errors.Is(err, ErrAuthTransport) {
		t.Fatalf("an empty token should be reported where it happens, got %v", err)
	}
}
