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
func (c *Client) doJSON(ctx context.Context, method, path string, payload any, out any) (ResponseMeta, error) {
	var body []byte
	if payload != nil {
		encoded, err := json.Marshal(payload)
		if err != nil {
			return ResponseMeta{}, fmt.Errorf("%w: could not encode the request body: %v", ErrInvalidRequest, err)
		}
		body = encoded
	}

	resp, meta, err := c.send(ctx, method, path, body, false)
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
func (c *Client) send(ctx context.Context, method, path string, body []byte, streaming bool) (*http.Response, ResponseMeta, error) {
	key := c.config.GatewayBaseURL
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

	policy := DefaultRetryPolicy()
	if c.config.Retry != nil {
		policy = *c.config.Retry
	}

	for attempt := 1; ; attempt++ {
		resp, meta, err := c.attempt(ctx, method, path, body, streaming)
		if err == nil {
			return resp, meta, nil
		}

		var apiErr *APIError
		if !errors.As(err, &apiErr) {
			// Transport failures and cancellation are never retried here: a client-side timeout
			// leaves the backend probably still working, and a retry would queue a second billable
			// generation on top of the first.
			return nil, meta, err
		}

		c.cooldown.note(key, apiErr)

		delay, retry := policy.delayFor(apiErr, attempt)
		if !retry {
			return nil, meta, err
		}

		timer := time.NewTimer(delay)
		select {
		case <-ctx.Done():
			timer.Stop()
			return nil, meta, ctx.Err()
		case <-timer.C:
		}
	}
}

func (c *Client) attempt(ctx context.Context, method, path string, body []byte, streaming bool) (*http.Response, ResponseMeta, error) {
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
