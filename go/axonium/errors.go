package axonium

import (
	"errors"
	"fmt"
	"strings"
)

// Two error envelopes exist and are deliberately kept apart.
//
// The gateway returns RFC 9457 problem details for everything under /v1/, modeled by APIError and
// matched by the last path segment of the problem-details "type" URI. The auth-service token
// endpoint returns the RFC 6749 {"error", "error_description"} shape, modeled by OAuthError, which
// is not an APIError because neither its fields nor its semantics carry over.
//
// The catalog lives in spec/errors.json and is mirrored by every language SDK. Match with
// errors.Is against the sentinels below, or read TypeSuffix; never match on Detail, which is
// human-readable prose that may be reworded at any time.
//
// Unlike the Python SDK's class hierarchy, Go models this as one struct plus sentinel values, so
// callers write errors.Is(err, axonium.ErrUnknownModel) rather than a type switch over fifteen
// types. The taxonomy is identical; only the idiom differs.
var (
	// Status-keyed fallbacks. An unrecognized type suffix matches one of these rather than
	// failing to parse: the catalog is expected to grow, and an SDK that hard-failed on an
	// unfamiliar error code would break the moment the gateway added one.
	ErrBadRequest   = errors.New("axonium: bad request")
	ErrUnauthorized = errors.New("axonium: unauthorized")
	ErrServer       = errors.New("axonium: server error")

	// 400
	ErrUnknownModel     = errors.New("axonium: unknown-model")
	ErrModalityMismatch = errors.New("axonium: modality-mismatch")
	ErrContextExceeded  = errors.New("axonium: context-exceeded")

	// 401
	ErrMissingCredentials = errors.New("axonium: missing-credentials")
	ErrInvalidToken       = errors.New("axonium: invalid-token")
	ErrTokenExpired       = errors.New("axonium: token-expired")
	ErrTokenRevoked       = errors.New("axonium: token-revoked")

	// 402 / 403
	ErrSpendCapExceeded = errors.New("axonium: spend-cap-exceeded")
	ErrForbidden        = errors.New("axonium: forbidden")

	// 429
	ErrRateLimit = errors.New("axonium: rate-limit-exceeded-requests")

	// 5xx
	ErrUpstream                = errors.New("axonium: upstream-error")
	ErrModelNotLoaded          = errors.New("axonium: model-not-loaded")
	ErrBackendUnavailable      = errors.New("axonium: backend-unavailable")
	ErrRateLimitingUnavailable = errors.New("axonium: rate-limiting-unavailable")
	ErrUsageStoreUnavailable   = errors.New("axonium: usage-store-unavailable")
)

// Non-HTTP failures.
var (
	// ErrConfiguration is returned before any network call when a required setting is missing or
	// invalid, naming both the setting and the environment variable that can supply it, so a
	// misconfigured deployment fails loudly at construction rather than as a confusing request
	// error later.
	ErrConfiguration = errors.New("axonium: configuration")

	// ErrInvalidRequest is returned where a value cannot be valid -- a temperature outside the
	// accepted range, an unknown role, a remote image URL -- so the mistake surfaces at the call
	// site instead of costing a round trip.
	ErrInvalidRequest = errors.New("axonium: invalid request")

	// ErrTransport means the request never produced an HTTP response (DNS, connection, or TLS).
	ErrTransport = errors.New("axonium: transport")

	// ErrAuthTransport means the auth-service could not be reached or answered with something
	// unusable. Distinct from OAuthError, which is the auth-service correctly reporting that the
	// credentials were rejected: this one means no token could be obtained at all.
	ErrAuthTransport = errors.New("axonium: auth transport")

	// ErrTimeout means the client gave up waiting. Never retried automatically: the backend may
	// still be generating, and a retry would queue a second billable generation on the first.
	ErrTimeout = errors.New("axonium: timeout")

	// ErrStreamInterrupted means the stream failed partway through. See StreamError.
	ErrStreamInterrupted = errors.New("axonium: stream interrupted")
)

// suffixSentinels maps a problem-details type suffix to its sentinel.
var suffixSentinels = map[string]error{
	"unknown-model":                ErrUnknownModel,
	"modality-mismatch":            ErrModalityMismatch,
	"context-exceeded":             ErrContextExceeded,
	"missing-credentials":          ErrMissingCredentials,
	"invalid-token":                ErrInvalidToken,
	"token-expired":                ErrTokenExpired,
	"token-revoked":                ErrTokenRevoked,
	"spend-cap-exceeded":           ErrSpendCapExceeded,
	"forbidden":                    ErrForbidden,
	"rate-limit-exceeded-requests": ErrRateLimit,
	"upstream-error":               ErrUpstream,
	"model-not-loaded":             ErrModelNotLoaded,
	"backend-unavailable":          ErrBackendUnavailable,
	"rate-limiting-unavailable":    ErrRateLimitingUnavailable,
	"usage-store-unavailable":      ErrUsageStoreUnavailable,
}

// retryableSuffixes are the errors where retrying can plausibly succeed. See spec/errors.json for
// the per-error policy; whether the SDK actually retries is decided by RetryPolicy, which is
// stricter still because a retried generation is billable rather than a replay.
var retryableSuffixes = map[string]bool{
	"token-expired":                true,
	"rate-limit-exceeded-requests": true,
	"upstream-error":               true,
	"backend-unavailable":          true,
	"rate-limiting-unavailable":    true,
	"usage-store-unavailable":      true,
	// model-not-loaded is a 5xx but is not retryable: it needs operator action, not patience.
	"model-not-loaded": false,
}

// APIError is an RFC 9457 problem-details error returned by the gateway.
type APIError struct {
	Status int

	// TypeSuffix is the last path segment of the problem-details type URI, e.g. "unknown-model".
	// Empty when the response was not parseable problem+json -- which happens for real: request
	// validation failures come back as 422 in FastAPI's default shape, with no type at all.
	TypeSuffix string

	Title    string
	Detail   string
	Instance string

	RequestID string
	// TraceID is omitted by the rate-limiting middleware's envelope, hence often empty.
	TraceID string

	// RetryAfter is the resolved wait in seconds, or nil when the platform supplied none.
	RetryAfter *float64

	RateLimit *RateLimitSnapshot

	// Raw is the decoded body, so a field this SDK does not model is still reachable.
	Raw map[string]any

	// Hint is a client-side diagnosis added where the SDK can say something the gateway's Detail
	// does not, such as exactly which scope a token is missing.
	Hint string
}

func (e *APIError) Error() string {
	msg := e.Detail
	if msg == "" {
		msg = e.Title
	}
	if msg == "" {
		msg = fmt.Sprintf("HTTP %d", e.Status)
	}

	parts := []string{msg}
	if e.Hint != "" {
		parts = append(parts, e.Hint)
	}
	if e.RequestID != "" {
		parts = append(parts, "request_id="+e.RequestID)
	}
	if e.TraceID != "" {
		parts = append(parts, "trace_id="+e.TraceID)
	}
	return strings.Join(parts, " ")
}

// Is reports whether this error matches a sentinel. A specific suffix matches its own sentinel and
// also the status-keyed fallback for its class, so errors.Is(err, ErrServer) is true for every 5xx
// including the ones with their own sentinel.
func (e *APIError) Is(target error) bool {
	if sentinel, ok := suffixSentinels[e.TypeSuffix]; ok && target == sentinel {
		return true
	}
	switch target {
	case ErrUnauthorized:
		return e.Status == 401
	case ErrServer:
		return e.Status >= 500
	case ErrBadRequest:
		return e.Status >= 400 && e.Status < 500 && e.Status != 401
	}
	return false
}

// Retryable reports whether retrying this error can plausibly succeed at all. It is a necessary
// condition, not a sufficient one: RetryPolicy additionally requires that no generation occurred,
// because this API has no idempotency mechanism.
func (e *APIError) Retryable() bool {
	return retryableSuffixes[e.TypeSuffix]
}

// StreamError is a mid-stream failure.
//
// Mid-stream failures cannot use an HTTP status: by the time a backend fails, the 200 and
// text/event-stream headers are already committed. The gateway signals them in-band instead, so
// this is produced by an error chunk rather than by a status code.
//
// PartialContent holds everything successfully received before the failure. Retrying means
// re-running a generation whose partial output was already delivered and billed.
type StreamError struct {
	Message        string
	PartialContent string
	RequestID      string
	TraceID        string
	Raw            map[string]any
}

func (e *StreamError) Error() string {
	parts := []string{"the stream was interrupted: " + e.Message}
	if e.RequestID != "" {
		parts = append(parts, "request_id="+e.RequestID)
	}
	if e.TraceID != "" {
		parts = append(parts, "trace_id="+e.TraceID)
	}
	return strings.Join(parts, " ")
}

func (e *StreamError) Is(target error) bool { return target == ErrStreamInterrupted }

// OAuth2 token-endpoint error codes (RFC 6749 section 5.2).
var (
	ErrUnsupportedGrantType = errors.New("axonium: unsupported_grant_type")
	ErrInvalidScope         = errors.New("axonium: invalid_scope")
	ErrInvalidClient        = errors.New("axonium: invalid_client")
	ErrUnauthorizedClient   = errors.New("axonium: unauthorized_client")
)

var oauthSentinels = map[string]error{
	"unsupported_grant_type": ErrUnsupportedGrantType,
	"invalid_scope":          ErrInvalidScope,
	"invalid_client":         ErrInvalidClient,
	"unauthorized_client":    ErrUnauthorizedClient,
}

// OAuthError is an error from the auth-service token endpoint.
//
// Deliberately not an APIError: the shape and the meaning both differ, so errors.Is(err, ErrServer)
// on a gateway error will not accidentally swallow an authentication failure.
type OAuthError struct {
	Status           int
	Code             string
	ErrorDescription string
	Raw              map[string]any
}

func (e *OAuthError) Error() string {
	msg := e.ErrorDescription
	if msg == "" {
		msg = e.Code
	}
	if msg == "" {
		msg = fmt.Sprintf("HTTP %d", e.Status)
	}
	return "axonium: token endpoint: " + msg
}

func (e *OAuthError) Is(target error) bool {
	sentinel, ok := oauthSentinels[e.Code]
	return ok && target == sentinel
}

// errorFromBody builds the most specific APIError for a gateway error response. retryAfter should
// already be resolved by the caller, which prefers the Retry-After header over the body field.
func errorFromBody(status int, body map[string]any, retryAfter *float64, rl *RateLimitSnapshot) *APIError {
	if body == nil {
		body = map[string]any{}
	}

	suffix := ""
	if raw, ok := body["type"].(string); ok {
		trimmed := strings.TrimRight(raw, "/")
		if i := strings.LastIndex(trimmed, "/"); i >= 0 {
			suffix = trimmed[i+1:]
		} else {
			suffix = trimmed
		}
	}

	if retryAfter == nil {
		if v, ok := numeric(body["retry_after"]); ok {
			retryAfter = &v
		}
	}

	return &APIError{
		Status:     status,
		TypeSuffix: suffix,
		Title:      stringOr(body["title"]),
		Detail:     stringOr(body["detail"]),
		Instance:   stringOr(body["instance"]),
		RequestID:  stringOr(body["request_id"]),
		TraceID:    stringOr(body["trace_id"]),
		RetryAfter: retryAfter,
		RateLimit:  rl,
		Raw:        body,
	}
}

func oauthErrorFromBody(status int, body map[string]any) *OAuthError {
	if body == nil {
		body = map[string]any{}
	}
	return &OAuthError{
		Status:           status,
		Code:             stringOr(body["error"]),
		ErrorDescription: stringOr(body["error_description"]),
		Raw:              body,
	}
}

func stringOr(v any) string {
	s, _ := v.(string)
	return s
}

func numeric(v any) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, true
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	}
	return 0, false
}
