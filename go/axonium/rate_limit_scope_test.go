package axonium

import (
	"net/http"
	"testing"
)

// Which budget a set of counters describes, once the endpoints hold separate budgets.

func headersWith(pairs map[string]string) http.Header {
	h := http.Header{}
	for name, value := range pairs {
		h.Set(name, value)
	}
	return h
}

func TestAScopeAloneIsNotABudget(t *testing.T) {
	// IsEmpty decides whether a snapshot is worth remembering. A scope labels a budget rather than
	// being one, so a response carrying only a scope has still reported no numbers.
	if got := rateLimitFromHeaders(headersWith(map[string]string{
		"X-RateLimit-Scope": "rerank",
	})); got != nil {
		t.Errorf("got %#v, want nil: a scope with no counters is not a budget", got)
	}
}

func TestTheBodySuppliesTheScopeWhenTheHeaderDoesNot(t *testing.T) {
	// Measured 2026-09-19: the 429 omits the header and carries "scope" in the body instead.
	headerless := rateLimitFromHeaders(headersWith(map[string]string{
		"X-RateLimit-Remaining-Requests": "0",
	}))
	if headerless.Scope != "" {
		t.Fatalf("precondition: scope should be empty, got %q", headerless.Scope)
	}

	if got := headerless.withScopeFrom(map[string]any{"scope": "embeddings"}); got.Scope != "embeddings" {
		t.Errorf("Scope = %q, want %q", got.Scope, "embeddings")
	}
}

// A defensive rule, not an observed response: nothing seen so far carries both.
//
// Pinned anyway because it is the same precedence already documented for Retry-After, and an
// unstated tie-break is one somebody re-decides differently later. Deliberately not a contract case
// -- the corpus holds recorded bytes, and inventing a response nobody has seen is how a fixture
// ends up describing a gateway that does not exist.
func TestTheHeaderWinsOverABodyThatDisagrees(t *testing.T) {
	fromHeader := rateLimitFromHeaders(headersWith(map[string]string{
		"X-RateLimit-Scope":              "rerank",
		"X-RateLimit-Remaining-Requests": "0",
	}))

	if got := fromHeader.withScopeFrom(map[string]any{"scope": "embeddings"}); got.Scope != "rerank" {
		t.Errorf("Scope = %q, want %q: the header is authoritative", got.Scope, "rerank")
	}
}

func TestABodyWithoutAScopeLeavesItUnset(t *testing.T) {
	headerless := rateLimitFromHeaders(headersWith(map[string]string{
		"X-RateLimit-Remaining-Requests": "0",
	}))

	if got := headerless.withScopeFrom(map[string]any{"detail": "none here"}); got.Scope != "" {
		t.Errorf("Scope = %q, want empty", got.Scope)
	}
	if got := headerless.withScopeFrom(nil); got.Scope != "" {
		t.Errorf("Scope = %q, want empty", got.Scope)
	}
}
