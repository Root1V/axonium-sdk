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

	// 422 -- the body carries an `errors` array naming the offending fields.
	ErrValidation = errors.New("axonium: validation-error")

	// ErrNotFound reports a resource that does not exist or does not belong to this client. The
	// two are deliberately indistinguishable: a 403 would confirm an id exists, which is what a
	// probe wants to learn. On a usage lookup there is a third case behind the same status and
	// it is the common one -- the id belongs to a replay, which is not billed and has no row.
	ErrNotFound = errors.New("axonium: not-found")

	// ErrUnknownRoute reports no route at that URL: a mistake in the calling code rather than a
	// fact about the caller's data. Deliberately NOT ErrNotFound, and the platform split them for
	// this SDK's benefit -- not-found is a statement about data, which a caller may read as an
	// empty result or retry, and a bad URL is neither. Sharing one sentinel would have made every
	// SDK that dispatches on it do the wrong thing with one of the two.
	//
	// Before PRM-174 this arrived as Starlette's bare {"detail":"Not Found"}, with no type and no
	// correlation ids in the body. It now carries the full envelope.
	ErrUnknownRoute = errors.New("axonium: unknown-route")

	// ErrMethodNotAllowed reports a URL that exists with a verb that does not. The Allow response
	// header lists the verbs that do, and survives the gateway's re-wrapping.
	ErrMethodNotAllowed = errors.New("axonium: method-not-allowed")

	// ErrInconsistentModelGroup reports replicas of one model disagreeing about their modality.
	// The gateway refuses the group rather than dropping the odd one: answering a chat request
	// from an embedding backend produces confident nonsense, which is the expensive failure.
	ErrInconsistentModelGroup = errors.New("axonium: inconsistent-model-group")

	// ErrUnauthorizedRequest reports a request that carried no verified claims. Distinct from
	// ErrMissingCredentials, which the auth middleware raises earlier, and not a token that aged
	// out -- so refreshing one does not help.
	ErrUnauthorizedRequest = errors.New("axonium: unauthorized")

	// ErrInvalidDate, ErrInvalidRange and ErrRangeTooLarge are the usage-export range errors.
	ErrInvalidDate   = errors.New("axonium: invalid-date")
	ErrInvalidRange  = errors.New("axonium: invalid-range")
	ErrRangeTooLarge = errors.New("axonium: range-too-large")

	// ErrTokenEndpointUnavailable reports that the gateway could not reach the auth-service to
	// issue a token. The only problem+json a token request can produce -- every other token
	// outcome uses the RFC 6749 OAuth2 shape -- and the distinction is what makes it safe to
	// retry, where an OAuth2 failure never is.
	ErrTokenEndpointUnavailable = errors.New("axonium: upstream-unavailable")

	// ErrTokenEndpointNotConfigured reports a deployment with no token endpoint wired up. Shares
	// a status with ErrTokenEndpointUnavailable but not its retryability.
	ErrTokenEndpointNotConfigured = errors.New("axonium: not-configured")

	// ErrToolCallArguments reports a tool call whose arguments string could not be decoded.
	// Almost always a generation stopped by max_tokens partway through writing the call. Raised
	// rather than returning an empty map so a truncated call cannot be mistaken for one that
	// genuinely took no arguments.
	ErrToolCallArguments = errors.New("axonium: tool call arguments are not a JSON object")

	// ErrUnknownInstance means a pinned instance does not serve the requested model. A pin never
	// silently falls back: you get that instance or an error.
	ErrUnknownInstance = errors.New("axonium: unknown-instance")
	// ErrUnknownParameter is raised only when the request carried require_parameters: true.
	// Without that flag the same request succeeds and the dropped names come back in
	// X-Prometheus-Ignored-Parameters. Not a 422: "that field does not exist here", not
	// "that value is wrong".
	ErrUnknownParameter = errors.New("axonium: unknown-parameter")

	// The four idempotency refusals. They need opposite handling, which is why they are four
	// types and not one: only ErrIdempotencyInProgress is ever worth retrying.

	// ErrInvalidIdempotencyKey means the key is malformed -- today, over 255 characters. A 400
	// rather than a conflict, because it never conflicted with anything. The SDK checks the length
	// before sending, so this usually surfaces as ErrInvalidRequest instead.
	ErrInvalidIdempotencyKey = errors.New("axonium: invalid-idempotency-key")
	// ErrIdempotencyKeyReuse means the key was already used for a different request. The
	// fingerprint covers path as well as body, so another endpoint counts. Use a fresh key.
	ErrIdempotencyKeyReuse = errors.New("axonium: idempotency-key-reuse")
	// ErrIdempotencyInProgress means the first request with this key is still running. The only
	// one worth retrying, and the only one carrying Retry-After.
	ErrIdempotencyInProgress = errors.New("axonium: idempotency-in-progress")
	// ErrIdempotencyResponseNotRetained means the original succeeded but its response was too
	// large to store. Not retryable, and it is proof the original worked.
	ErrIdempotencyResponseNotRetained = errors.New("axonium: idempotency-response-not-retained")

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

	// 4xx, status not fixed -- see PredictBackendRejected in the catalog.
	ErrPredictBackendRejected = errors.New("axonium: predict-backend-rejected")

	// 5xx
	ErrUpstream                = errors.New("axonium: upstream-error")
	ErrCapacityExhausted       = errors.New("axonium: capacity-exhausted")
	ErrModelNotLoaded          = errors.New("axonium: model-not-loaded")
	ErrBackendUnavailable      = errors.New("axonium: backend-unavailable")
	ErrRateLimitingUnavailable = errors.New("axonium: rate-limiting-unavailable")
	ErrUsageStoreUnavailable   = errors.New("axonium: usage-store-unavailable")

	// ErrRerankDialectUnknown reports a reranker running on an engine whose rerank request shape
	// the gateway has not recorded. Only on POST /v1/rerank.
	//
	// Not retryable, unlike every other 503 in the catalog, which is why it is named rather than
	// left to fall through: the gateway records each engine's dialect deliberately, because a
	// reranker on a new engine is not llama.cpp's shape just because the last one was. Waiting
	// cannot register it; an operator has to.
	ErrRerankDialectUnknown = errors.New("axonium: rerank-dialect-unknown")
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
	"unknown-model":                     ErrUnknownModel,
	"modality-mismatch":                 ErrModalityMismatch,
	"context-exceeded":                  ErrContextExceeded,
	"validation-error":                  ErrValidation,
	"not-found":                         ErrNotFound,
	"unknown-route":                     ErrUnknownRoute,
	"method-not-allowed":                ErrMethodNotAllowed,
	"inconsistent-model-group":          ErrInconsistentModelGroup,
	"unauthorized":                      ErrUnauthorizedRequest,
	"invalid-date":                      ErrInvalidDate,
	"invalid-range":                     ErrInvalidRange,
	"range-too-large":                   ErrRangeTooLarge,
	"upstream-unavailable":              ErrTokenEndpointUnavailable,
	"not-configured":                    ErrTokenEndpointNotConfigured,
	"unknown-instance":                  ErrUnknownInstance,
	"unknown-parameter":                 ErrUnknownParameter,
	"invalid-idempotency-key":           ErrInvalidIdempotencyKey,
	"idempotency-key-reuse":             ErrIdempotencyKeyReuse,
	"idempotency-in-progress":           ErrIdempotencyInProgress,
	"idempotency-response-not-retained": ErrIdempotencyResponseNotRetained,
	"missing-credentials":               ErrMissingCredentials,
	"invalid-token":                     ErrInvalidToken,
	"token-expired":                     ErrTokenExpired,
	"token-revoked":                     ErrTokenRevoked,
	"spend-cap-exceeded":                ErrSpendCapExceeded,
	"forbidden":                         ErrForbidden,
	"rate-limit-exceeded-requests":      ErrRateLimit,
	"upstream-error":                    ErrUpstream,
	"capacity-exhausted":                ErrCapacityExhausted,
	"predict-backend-rejected":          ErrPredictBackendRejected,
	"model-not-loaded":                  ErrModelNotLoaded,
	"backend-unavailable":               ErrBackendUnavailable,
	"rate-limiting-unavailable":         ErrRateLimitingUnavailable,
	"usage-store-unavailable":           ErrUsageStoreUnavailable,
	"rerank-dialect-unknown":            ErrRerankDialectUnknown,
}

// retryableSuffixes are the errors where retrying can plausibly succeed. See spec/errors.json for
// the per-error policy; whether the SDK actually retries is decided by RetryPolicy, which is
// stricter still because a retried generation is billable rather than a replay.
var retryableSuffixes = map[string]bool{
	// A token 503 is the gateway failing to reach the auth-service, not an OAuth2 outcome, so
	// unlike every 4xx from that endpoint it is worth retrying. not-configured is not: it shares
	// the status but needs operator action.
	"upstream-unavailable":         true,
	"token-expired":                true,
	"rate-limit-exceeded-requests": true,
	"upstream-error":               true,
	"backend-unavailable":          true,
	"rate-limiting-unavailable":    true,
	"usage-store-unavailable":      true,
	"idempotency-in-progress":      true,
	// capacity-exhausted means every replica is busy rather than broken, which is the one 503
	// where waiting is the whole remedy: a slot frees when some other request finishes.
	"capacity-exhausted": true,
	// model-not-loaded is a 5xx but is not retryable: it needs operator action, not patience.
	"model-not-loaded": false,
	// predict-backend-rejected is deliberately absent: its status is the engine's, so the answer
	// is not a property of the name. Retryable() decides it from Status instead.
	//
	// rerank-dialect-unknown is absent because this map is an allowlist and absence is the right
	// answer -- but it is written here because it is the one 503 where that is surprising, and a
	// future reader comparing this map against the status column would otherwise "fix" it.
	"rerank-dialect-unknown": false,
}

// predictBackendRetryableStatuses are the engine statuses worth repeating when the gateway wraps a
// refusal as predict-backend-rejected. Everything else the engine rejects is the request to fix.
var predictBackendRetryableStatuses = map[int]bool{429: true}

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
// condition, not a sufficient one: RetryPolicy additionally requires either that no generation
// occurred or that an Idempotency-Key makes the repeat a replay rather than a second billing.
//
// Keyed on the suffix, with one exception the catalog marks "by_status". predict-backend-rejected
// carries whatever status the engine returned, so its name says nothing about whether waiting
// helps and only the status can.
func (e *APIError) Retryable() bool {
	if e.TypeSuffix == "predict-backend-rejected" {
		return predictBackendRetryableStatuses[e.Status]
	}
	return retryableSuffixes[e.TypeSuffix]
}

// BackendError is the engine's own error body, preserved verbatim, on an error from
// POST /v1/models/{model}/predict.
//
// Nil when the extension member is absent: a gateway that wrapped the refusal without capturing
// it, in which case the status is all there is. The shape is the engine's and this SDK does not
// model it -- that is the point of the pass-through route.
func (e *APIError) BackendError() any {
	return e.Raw["backend_error"]
}

// BackendStatus is the engine's status when it differs from this error's.
//
// Present on the 502 path, where the gateway reports its own status because a 500 the engine
// produced is not one a caller can act on. On the 4xx path the two are the same and this is 0.
func (e *APIError) BackendStatus() int {
	if value, ok := e.Raw["backend_status"].(float64); ok {
		return int(value)
	}
	return 0
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
// requestID and traceID are what the response headers carried; the body wins when it has them.
// Not every error body is a complete problem+json envelope -- a validation failure forwarded from a
// backend, or an HTML page from a proxy that never reached the gateway -- and without this an error
// that DID carry a request id hands the caller nothing to take to the platform team.
func errorFromBody(status int, body map[string]any, retryAfter *float64, rl *RateLimitSnapshot,
	requestID, traceID string) *APIError {
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
		RequestID:  firstNonEmpty(stringOr(body["request_id"]), requestID),
		TraceID:    firstNonEmpty(stringOr(body["trace_id"]), traceID),
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

// firstNonEmpty returns the body's value when it has one, and the header's otherwise.
func firstNonEmpty(fromBody, fromHeader string) string {
	if fromBody != "" {
		return fromBody
	}
	return fromHeader
}
