package axonium

import (
	"errors"
	"math"
	"math/rand"
	"sync"
	"time"
)

// The gateway already retries against the backend up to three times with exponential backoff
// before it returns anything to a client, and runs its own per-backend circuit breaker. Retrying
// symmetrically on top of that would multiply load on a struggling backend, so this layer is
// deliberately narrow.
//
// There is no idempotency-key mechanism in this API. A retried chat, embeddings or image request
// is a genuinely new generation: billable again, and not a replay of the first. So the default
// policy retries only where the platform tells us no generation happened -- a rate limit, or a
// circuit breaker that fast-failed without ever calling the backend. Everything that might have
// reached a model is left to the caller to decide about.

// noGenerationOccurred are the errors where the platform fast-failed without reaching a model, so
// retrying cannot duplicate a generation or double-bill.
var noGenerationOccurred = map[string]bool{
	"rate-limit-exceeded-requests": true,
	"backend-unavailable":          true,
	"rate-limiting-unavailable":    true,
	"usage-store-unavailable":      true,
}

// RetryPolicy decides when to retry and how long to wait.
type RetryPolicy struct {
	MaxAttempts int
	// InitialBackoff is the base delay when the platform supplies no Retry-After. Doubles per
	// attempt.
	InitialBackoff time.Duration
	// MaxBackoff is the longest this SDK will block inside a single call. It also caps a
	// server-supplied Retry-After: a longer wait is surfaced to the caller instead of slept
	// through.
	MaxBackoff time.Duration
	// Jitter spreads retries so concurrent callers recovering from the same outage do not
	// resynchronize.
	Jitter bool
	// RetryUpstreamErrors is off by default. A 502 upstream-error means the gateway's own three
	// attempts already failed, so an immediate fourth is unlikely to help -- and unlike the
	// fast-fail errors, the request may have reached a model, making a retry a second billable
	// generation. Enable it only where that trade is acceptable.
	RetryUpstreamErrors bool
}

// DefaultRetryPolicy returns the conservative policy described above.
func DefaultRetryPolicy() RetryPolicy {
	return RetryPolicy{
		MaxAttempts:    3,
		InitialBackoff: time.Second,
		MaxBackoff:     60 * time.Second,
		Jitter:         true,
	}
}

// delayFor returns how long to wait before attempt+1, and whether to retry at all. attempt is
// 1-based and counts the request that just failed.
func (p RetryPolicy) delayFor(err *APIError, attempt int) (time.Duration, bool) {
	if attempt >= p.MaxAttempts || !err.Retryable() {
		return 0, false
	}

	if err.TypeSuffix == "upstream-error" {
		// Capped at a single extra attempt regardless of MaxAttempts.
		if !p.RetryUpstreamErrors || attempt > 1 {
			return 0, false
		}
	} else if !noGenerationOccurred[err.TypeSuffix] {
		return 0, false
	}

	if err.RetryAfter != nil {
		// Server-supplied and authoritative: for an open circuit breaker it is the real expected
		// recovery time, which no local heuristic can improve on. But a wait longer than
		// MaxBackoff is not something to sit through inside a single call -- blocking a caller for
		// minutes is worse than telling them. The error carries RetryAfter, so they can schedule
		// the work themselves.
		wait := time.Duration(math.Max(0, *err.RetryAfter) * float64(time.Second))
		if wait > p.MaxBackoff {
			return 0, false
		}
		return wait, true
	}

	return p.backoff(attempt), true
}

func (p RetryPolicy) backoff(attempt int) time.Duration {
	delay := p.InitialBackoff * (1 << (attempt - 1))
	if delay > p.MaxBackoff {
		delay = p.MaxBackoff
	}
	if p.Jitter {
		delay = time.Duration(float64(delay) * (0.5 + rand.Float64()/2))
	}
	return delay
}

// cooldownRegistry remembers server-supplied waits so a known-open circuit is not hammered.
//
// When the gateway reports an open circuit breaker it also says when the backend is expected to
// recover. Ignoring that and sending the next request anyway just buys another 503, so the wait is
// recorded and later calls for the same backend fail locally until it elapses.
//
// Only ever populated from a wait the platform supplied: this never invents a cooldown of its own,
// and never guesses that a backend is unhealthy from local failure counts.
type cooldownRegistry struct {
	mu    sync.Mutex
	until map[string]time.Time
}

func newCooldownRegistry() *cooldownRegistry {
	return &cooldownRegistry{until: map[string]time.Time{}}
}

func (r *cooldownRegistry) record(key string, d time.Duration) {
	if d <= 0 {
		return
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	r.until[key] = time.Now().Add(d)
}

// remaining returns how long is still to wait for key, or zero if it is clear.
func (r *cooldownRegistry) remaining(key string) time.Duration {
	r.mu.Lock()
	defer r.mu.Unlock()
	until, ok := r.until[key]
	if !ok {
		return 0
	}
	remaining := time.Until(until)
	if remaining <= 0 {
		delete(r.until, key)
		return 0
	}
	return remaining
}

// note records a cooldown if this error came with a server-supplied wait.
func (r *cooldownRegistry) note(key string, err error) {
	var apiErr *APIError
	if !errors.As(err, &apiErr) || !errors.Is(apiErr, ErrBackendUnavailable) || apiErr.RetryAfter == nil {
		return
	}
	r.record(key, time.Duration(*apiErr.RetryAfter*float64(time.Second)))
}
