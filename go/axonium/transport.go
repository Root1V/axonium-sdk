package axonium

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"strconv"
	"time"
)

// Turning an HTTP response into either a typed value or a typed error, in one place, so that error
// mapping, correlation metadata and rate-limit accounting are not re-implemented per endpoint.

// maxErrorBody bounds how much of an error response is read. An error envelope is small; a
// gateway misconfigured to stream something enormous under an error status should not be able to
// exhaust client memory before the SDK notices.
const maxErrorBody = 1 << 20

// instanceHeader pins a request to one instance, and names the serving instance on the response.
const instanceHeader = "X-Prometheus-Instance"

// idempotencyHeader makes a retry safe on the non-streaming endpoints: a repeat with the same key
// and body returns the stored result without reaching a model, recording usage, or counting
// against the spend cap. Streaming is excluded, and silently -- the gateway takes the key, ignores
// it, and generates again -- so the SDK refuses to send one there.
const idempotencyHeader = "Idempotency-Key"

// maxIdempotencyKeyLength is the gateway's limit. Checked client-side because exceeding it comes
// back as 409 idempotency-conflict -- the same type a genuine reuse produces -- which would tell a
// caller they repeated a request when their key is simply too long.
const maxIdempotencyKeyLength = 255

// retryAfterSeconds reads Retry-After, which may be either delta-seconds or an HTTP date.
//
// The result is always a finite, non-negative number of seconds or nil. A caller is likely to pass
// it straight to a sleep, where a negative value is meaningless and an infinite one never returns,
// so a malformed header is discarded rather than propagated.
func retryAfterSeconds(h http.Header) *float64 {
	raw := h.Get("Retry-After")
	if raw == "" {
		return nil
	}

	if seconds, err := strconv.ParseFloat(raw, 64); err == nil {
		return saneWait(seconds)
	}

	target, err := http.ParseTime(raw)
	if err != nil {
		return nil
	}
	serverNow := h.Get("Date")
	if serverNow == "" {
		return nil
	}
	reference, err := http.ParseTime(serverNow)
	if err != nil {
		return nil
	}
	// Both sides of this subtraction come from the server, so the wait is unaffected by any
	// difference between its clock and ours.
	return saneWait(target.Sub(reference).Seconds())
}

func saneWait(seconds float64) *float64 {
	if math.IsNaN(seconds) || math.IsInf(seconds, 0) {
		return nil
	}
	if seconds < 0 {
		seconds = 0
	}
	return &seconds
}

// errorFromResponse builds the most specific APIError for a failed response.
func errorFromResponse(resp *http.Response) *APIError {
	body, _ := decodeJSONObject(io.LimitReader(resp.Body, maxErrorBody))
	return errorFromBody(
		resp.StatusCode,
		body,
		retryAfterSeconds(resp.Header),
		rateLimitFromHeaders(resp.Header),
	)
}

func decodeJSONObject(r io.Reader) (map[string]any, error) {
	raw, err := io.ReadAll(r)
	if err != nil {
		return nil, err
	}
	var body map[string]any
	if err := json.Unmarshal(raw, &body); err != nil {
		return nil, err
	}
	return body, nil
}

func drainAndClose(body io.ReadCloser) {
	// Draining lets the connection go back to the pool instead of being dropped.
	_, _ = io.Copy(io.Discard, io.LimitReader(body, maxErrorBody))
	_ = body.Close()
}

// translateTransportError maps a Go HTTP failure onto this SDK's error taxonomy.
func translateTransportError(ctx context.Context, err error) error {
	// A cancelled or expired caller context is the caller's own signal coming back; reporting it
	// as ours would hide that they asked for it.
	if ctxErr := ctx.Err(); ctxErr != nil {
		if errors.Is(ctxErr, context.DeadlineExceeded) {
			return fmt.Errorf("%w: the request timed out; the backend may still be generating, so retrying would start a second billable generation rather than resuming this one", ErrTimeout)
		}
		return ctxErr
	}
	var netErr interface{ Timeout() bool }
	if errors.As(err, &netErr) && netErr.Timeout() {
		return fmt.Errorf("%w: %v", ErrTimeout, err)
	}
	return fmt.Errorf("%w: %v", ErrTransport, err)
}

// doJSON sends a request with retries and decodes a successful JSON response into out.
func (c *Client) doJSON(ctx context.Context, method, path string, payload any, out any, model, instance, idemKey string) (ResponseMeta, error) {
	var body []byte
	if payload != nil {
		encoded, err := json.Marshal(payload)
		if err != nil {
			return ResponseMeta{}, fmt.Errorf("%w: could not encode the request body: %v", ErrInvalidRequest, err)
		}
		body = encoded
	}

	resp, meta, err := c.send(ctx, method, path, body, false, model, instance, idemKey)
	if err != nil {
		return meta, err
	}
	defer drainAndClose(resp.Body)

	if out != nil {
		decoded, err := io.ReadAll(resp.Body)
		if err != nil {
			return meta, translateTransportError(ctx, err)
		}
		if capturer, ok := out.(rawCapturer); ok {
			var raw map[string]any
			if json.Unmarshal(decoded, &raw) == nil {
				capturer.setRaw(raw)
			}
		}
		if err := json.Unmarshal(decoded, out); err != nil {
			return meta, &APIError{
				Status:    resp.StatusCode,
				RequestID: meta.RequestID,
				TraceID:   meta.TraceID,
				Detail:    fmt.Sprintf("the gateway returned a body this SDK could not parse: %v", err),
			}
		}
	}
	return meta, nil
}

// send performs one logical call, retrying where the platform says no generation occurred.
//
// On success the response body is still open and belongs to the caller. streaming suppresses the
// whole-request timeout, because a long generation is not a stalled one.
func (c *Client) send(ctx context.Context, method, path string, body []byte, streaming bool, model, instance, idemKey string) (*http.Response, ResponseMeta, error) {
	// Keyed by model, not by gateway. 503 backend-unavailable is a per-model condition -- it means
	// every instance of that model is out, and other models on the same gateway keep serving.
	// Cooling the whole gateway would refuse requests the platform would have answered.
	key := cooldownKey(c.config.GatewayBaseURL, model)
	if wait := c.cooldown.remaining(key); wait > 0 {
		return nil, ResponseMeta{}, &APIError{
			Status:     http.StatusServiceUnavailable,
			TypeSuffix: "backend-unavailable",
			Detail: fmt.Sprintf(
				"the gateway reported this backend as unavailable and asked to wait; %s of that wait remains",
				wait.Round(time.Millisecond),
			),
			Hint:       "This was refused locally, without a request, to honor the wait the gateway supplied.",
			RetryAfter: ptr(wait.Seconds()),
		}
	}

	if len(idemKey) > maxIdempotencyKeyLength {
		return nil, ResponseMeta{}, fmt.Errorf(
			"%w: IdempotencyKey is %d characters; the gateway accepts at most %d",
			ErrInvalidRequest, len(idemKey), maxIdempotencyKeyLength)
	}

	policy := DefaultRetryPolicy()
	if c.config.Retry != nil {
		policy = *c.config.Retry
	}

	for attempt := 1; ; attempt++ {
		resp, meta, err := c.attempt(ctx, method, path, body, streaming, instance, idemKey)
		if err == nil {
			return resp, meta, nil
		}

		var apiErr *APIError
		if !errors.As(err, &apiErr) {
			// A client-side timeout normally ends the call: the backend is probably still
			// generating, so a retry would queue a second billable generation rather than resume
			// the first. An Idempotency-Key removes exactly that objection -- the repeat returns
			// the stored result without reaching a model -- so it is the one thing that makes this
			// retryable, and only for a timeout. Connection failures stay untouched either way.
			if idemKey != "" && errors.Is(err, ErrTimeout) && attempt < policy.MaxAttempts {
				if !sleepFor(ctx, policy.backoff(attempt)) {
					return nil, meta, ctx.Err()
				}
				continue
			}
			return nil, meta, err
		}

		c.cooldown.note(key, apiErr)

		delay, retry := policy.delayFor(apiErr, attempt)
		if !retry {
			return nil, meta, err
		}

		if !sleepFor(ctx, delay) {
			return nil, meta, ctx.Err()
		}
	}
}

func (c *Client) attempt(ctx context.Context, method, path string, body []byte, streaming bool, instance, idemKey string) (*http.Response, ResponseMeta, error) {
	if !streaming {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, c.config.Timeouts.Request)
		defer cancel()
	}

	var reader io.Reader
	if body != nil {
		reader = bytes.NewReader(body)
	}

	req, err := http.NewRequestWithContext(ctx, method, c.config.GatewayBaseURL+path, reader)
	if err != nil {
		return nil, ResponseMeta{}, fmt.Errorf("%w: %v", ErrInvalidRequest, err)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if streaming {
		req.Header.Set("Accept", "text/event-stream")
	} else {
		req.Header.Set("Accept", "application/json")
	}
	req.Header.Set("User-Agent", userAgent)
	if instance != "" {
		req.Header.Set(instanceHeader, instance)
	}
	if idemKey != "" {
		req.Header.Set(idempotencyHeader, idemKey)
	}

	token, err := c.auth.apply(ctx, req)
	if err != nil {
		return nil, ResponseMeta{}, err
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, ResponseMeta{}, translateTransportError(ctx, err)
	}

	// Reactive fallback for a token revoked mid-flight or an expiry the refresh-ahead missed. The
	// request is replayed once with a fresh token, and only once: a second 401 is the gateway
	// telling us the credential itself is the problem.
	if resp.StatusCode == http.StatusUnauthorized {
		drainAndClose(resp.Body)

		fresh, refreshErr := c.auth.refresh(ctx, token)
		if refreshErr != nil {
			return nil, ResponseMeta{}, refreshErr
		}

		retryReq, err := http.NewRequestWithContext(ctx, method, c.config.GatewayBaseURL+path, bodyReader(body))
		if err != nil {
			return nil, ResponseMeta{}, fmt.Errorf("%w: %v", ErrInvalidRequest, err)
		}
		retryReq.Header = req.Header.Clone()
		setBearer(retryReq, fresh)

		resp, err = c.http.Do(retryReq)
		if err != nil {
			return nil, ResponseMeta{}, translateTransportError(ctx, err)
		}
	}

	meta := metaFromHeaders(resp.Header)
	c.rememberRateLimit(meta.RateLimit)

	if resp.StatusCode >= 400 {
		apiErr := errorFromResponse(resp)
		_ = resp.Body.Close()
		c.explainForbidden(apiErr)
		return nil, meta, apiErr
	}

	if !streaming {
		// Read the body here, inside the timeout scope, rather than handing it back still open.
		// The deferred cancel above fires when this function returns, and a cancelled context
		// closes the body -- so a caller reading afterwards gets "context canceled" for any
		// response too large to have been buffered already. Small bodies hid it; a real
		// embeddings response, which is tens of kilobytes of floats, does not.
		//
		// Reading here also makes Timeouts.Request mean what it says: the whole exchange,
		// body included, rather than just the headers.
		body, err := io.ReadAll(resp.Body)
		_ = resp.Body.Close()
		if err != nil {
			return nil, meta, translateTransportError(ctx, err)
		}
		resp.Body = io.NopCloser(bytes.NewReader(body))
	}
	return resp, meta, nil
}

func bodyReader(body []byte) io.Reader {
	if body == nil {
		return nil
	}
	return bytes.NewReader(body)
}

func ptr[T any](v T) *T { return &v }

// rawCapturer is implemented by response types that retain the decoded body, so a field this SDK
// does not model is still reachable by a caller rather than silently dropped.
type rawCapturer interface{ setRaw(map[string]any) }

// cooldownKey scopes a cooldown to one model on one gateway. An empty model (the catalog
// endpoints) gets its own bucket rather than sharing with every unnamed call.
func cooldownKey(gateway, model string) string {
	if model == "" {
		model = "-"
	}
	return gateway + "|" + model
}

// sleepFor waits, returning false if the caller's context ended first.
func sleepFor(ctx context.Context, d time.Duration) bool {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-timer.C:
		return true
	}
}
