package axonium

import (
	"encoding/json"
	"net/http"
	"strconv"
	"strings"
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

	// Instance is the short label of the instance that served this response ("#1", "#2"), unique
	// within the model. Stable for an instance's life, but a number can be reused after the
	// highest-numbered instance is deleted -- so log it for readability and key on InstanceID.
	Instance string
	// InstanceID is the full id of the instance that served this response. This is the value to
	// report when asking the platform team about a slow or odd response.
	InstanceID string

	// IdempotentReplay is true when this response was replayed from an Idempotency-Key rather than
	// generated. A replay reached no model, recorded no usage and counted against no spend cap, so
	// its Usage describes the original generation rather than a second one.
	IdempotentReplay bool

	// IdempotentReplayOf is, on a replay, the request id of the generation that was actually billed.
	//
	// A replay carries its own request id, and that id has no usage row of its own -- looking it up
	// returns 404, correctly, because replaying does not reach a model and is not billed. This is
	// the id that does resolve, so it is the only way from the response a caller received to the
	// charge it corresponds to. Empty on anything that is not a replay.
	IdempotentReplayOf string
}

func metaFromHeaders(h http.Header) ResponseMeta {
	return ResponseMeta{
		RequestID:          h.Get("X-Request-ID"),
		TraceID:            h.Get("X-Trace-ID"),
		Instance:           h.Get("X-Prometheus-Instance"),
		InstanceID:         h.Get("X-Prometheus-Instance-Id"),
		IdempotentReplay:   strings.EqualFold(h.Get("Idempotent-Replay"), "true"),
		IdempotentReplayOf: h.Get("X-Idempotent-Replay-Of"),
		RateLimit:          rateLimitFromHeaders(h),
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

	// CacheReadTokens is how many of PromptTokens were served from cache. It is a subset of
	// PromptTokens, not a separate bucket: the input counter includes the cached prefix. That
	// convention was settled across the three fronts because providers report it that way, so an
	// adapter copies instead of subtracting -- copying cannot be done wrong, and a forgotten
	// subtraction double-counts the cache without producing any error.
	//
	// nil when the backend did not report it. Only llama.cpp-family timings carry it today, so a
	// non-streaming response usually leaves this unset rather than zero.
	CacheReadTokens *int `json:"cache_read_tokens,omitempty"`

	// Estimated is true when the counts were derived from a backend timings object rather than
	// reported directly. A derived figure must never be mistaken for a measured one: it is the
	// difference between billing on a fact and billing on an inference.
	Estimated bool `json:"-"`
}

// promptTokensDetails is the OpenAI-shaped breakdown of the prompt count. Non-streaming responses
// report the cached share here, so cache_read is a measured figure on that path rather than one
// derived from timings.
type promptTokensDetails struct {
	CachedTokens *int `json:"cached_tokens"`
}

// UnmarshalJSON lifts prompt_tokens_details.cached_tokens onto CacheReadTokens.
//
// Without this the count sits nested where nothing reads it, and the SDK would report that nobody
// measured the cache on the one path where somebody did.
func (u *Usage) UnmarshalJSON(data []byte) error {
	type plain Usage
	var raw struct {
		plain
		PromptTokensDetails *promptTokensDetails `json:"prompt_tokens_details"`
	}
	if err := json.Unmarshal(data, &raw); err != nil {
		return err
	}
	*u = Usage(raw.plain)
	if u.CacheReadTokens == nil && raw.PromptTokensDetails != nil {
		u.CacheReadTokens = raw.PromptTokensDetails.CachedTokens
	}
	return nil
}
