package axonium

import (
	"net/http"
	"strconv"
)

// RateLimitSnapshot is the rate-limit budget as of one response, parsed from the X-RateLimit-*
// headers.
//
// Present on the inference and catalog endpoints, absent on health and metrics routes -- in which
// case every field is nil.
//
// The token figures reflect the gateway's post-hoc accounting rather than a pre-flight
// reservation, so a burst of large requests can still exceed the token budget between header
// updates. Treat them as a strong signal, not a guarantee against ever seeing a 429.
type RateLimitSnapshot struct {
	LimitRequests     *int
	RemainingRequests *int
	// ResetRequests is the Unix timestamp at which the request window resets.
	ResetRequests *int

	LimitTokens     *int
	RemainingTokens *int
	// ResetTokens is the Unix timestamp at which the token window resets.
	ResetTokens *int
}

// IsEmpty reports whether the response carried no rate-limit headers at all.
func (s *RateLimitSnapshot) IsEmpty() bool {
	return s.LimitRequests == nil && s.RemainingRequests == nil && s.ResetRequests == nil &&
		s.LimitTokens == nil && s.RemainingTokens == nil && s.ResetTokens == nil
}

func rateLimitFromHeaders(h http.Header) *RateLimitSnapshot {
	read := func(name string) *int {
		raw := h.Get(name)
		if raw == "" {
			return nil
		}
		n, err := strconv.Atoi(raw)
		if err != nil {
			return nil
		}
		return &n
	}

	s := &RateLimitSnapshot{
		LimitRequests:     read("X-RateLimit-Limit-Requests"),
		RemainingRequests: read("X-RateLimit-Remaining-Requests"),
		ResetRequests:     read("X-RateLimit-Reset-Requests"),
		LimitTokens:       read("X-RateLimit-Limit-Tokens"),
		RemainingTokens:   read("X-RateLimit-Remaining-Tokens"),
		ResetTokens:       read("X-RateLimit-Reset-Tokens"),
	}
	if s.IsEmpty() {
		return nil
	}
	return s
}

// ResponseMeta is correlation and budget information attached to every response.
//
// Carried on successes as well as failures: correlating a slow but successful call with platform
// traces matters as much as correlating a failed one.
type ResponseMeta struct {
	// RequestID is the server-generated per-request UUID, from the X-Request-ID header.
	RequestID string
	// TraceID is the log-correlation ID, from the X-Trace-ID header.
	TraceID   string
	RateLimit *RateLimitSnapshot
}

func metaFromHeaders(h http.Header) ResponseMeta {
	return ResponseMeta{
		RequestID: h.Get("X-Request-ID"),
		TraceID:   h.Get("X-Trace-ID"),
		RateLimit: rateLimitFromHeaders(h),
	}
}

// Usage is token accounting for a request.
//
// Every counter is a pointer because nil and zero mean different things: nil is "nobody measured
// this", zero is "measured, and it was zero". Embedding responses carry no CompletionTokens
// because there is no generation phase, and llama.cpp-family backends report no usage at all when
// streaming. Collapsing those onto 0 would let a caller conclude a phase was free when in truth it
// was never counted -- the same three-state distinction the tri-party Usage vocabulary settled on.
type Usage struct {
	PromptTokens     *int `json:"prompt_tokens,omitempty"`
	CompletionTokens *int `json:"completion_tokens,omitempty"`
	TotalTokens      *int `json:"total_tokens,omitempty"`

	// Estimated is true when the counts were derived from a backend timings object rather than
	// reported directly. A derived figure must never be mistaken for a measured one: it is the
	// difference between billing on a fact and billing on an inference.
	Estimated bool `json:"-"`
}
