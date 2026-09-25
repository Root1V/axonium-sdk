package axonium

import (
	"context"
	"fmt"
	"net/url"
)

// Per-request usage lookup.
//
// Answers the one question a caller cannot otherwise answer about their own bill: this request was
// charged -- why did it stop? Both aggregate usage endpoints require admin:read, which a normal
// client neither has nor should have; this one reads exactly the caller's own row.

// RequestUsage is the billing record for one request.
//
// Every field is optional. The gateway guarantees the row exists for a billed request, not which
// columns a given deployment populates -- CostUSD is nil where no price is configured, and new
// columns are appended over time.
type RequestUsage struct {
	// RequestID is the id this row describes, which should match what was asked for.
	RequestID string `json:"request_id"`
	Model     string `json:"model"`
	// RequestKind is what kind of request was billed. Measured against a deployment 2026-09-25:
	// "chat", "embedding", "rerank", "image", "predict".
	//
	// Open, and a plain string on purpose. The platform adds kinds as it adds endpoints -- rerank
	// and predict both arrived after this field was first written -- so a typed enum here would
	// turn every new one into a parse failure in an SDK that predates it. Switch on it if you
	// must, but always leave a default.
	RequestKind string `json:"request_kind,omitempty"`

	// Usage mirrors an inference response field for field. CacheReadTokens is a subset of
	// PromptTokens, the same convention as everywhere else, so a row and the response it describes
	// can be compared without arithmetic.
	Usage *Usage `json:"usage,omitempty"`

	ImageCount *int `json:"image_count,omitempty"`

	// Interrupted is whether the caller received less than the whole answer. Derived from
	// TerminationReason by the gateway and never assigned separately, so the two cannot disagree.
	Interrupted *bool `json:"interrupted,omitempty"`

	// TerminationReason is "complete", "upstream_error" or "client_disconnected" today.
	//
	// Deliberately a plain string rather than a closed set of constants. The platform proposed a
	// fourth value this week and withdrew it; the next one may not be withdrawn, and an exhaustive
	// switch would turn a new value into a failure for a caller who only wanted the token counts.
	TerminationReason string `json:"termination_reason,omitempty"`

	// CostUSD is nil where the deployment has no price configured, which is not the same as free.
	CostUSD *float64 `json:"cost_usd,omitempty"`

	// InstanceID is which replica served it -- the value to quote when asking the platform team
	// about this row.
	InstanceID string `json:"instance_id,omitempty"`

	CreatedAt string `json:"created_at,omitempty"`

	// Raw is the decoded body as received, so a column this SDK does not model stays reachable.
	Raw map[string]any `json:"-"`

	Meta ResponseMeta `json:"-"`
}

// UsageService reads what one of your own requests was charged.
type UsageService struct{ client *Client }

// Retrieve returns the usage row for requestID, which comes from any response's Meta.RequestID.
//
// A missing row is an *APIError matching ErrNotFound. That covers three cases the gateway
// deliberately does not distinguish -- the id is not yours, the id never existed, and the id
// belongs to a replay. A replay reaches no model and is not billed, so it has no row of its own;
// use Meta.IdempotentReplayOf to get the id of the generation that was charged, and look that up
// instead.
func (s *UsageService) Retrieve(ctx context.Context, requestID string) (*RequestUsage, error) {
	// Escaped because the id comes from a caller who may have stored or mistyped it, and an
	// unescaped separator would silently address a different route.
	path := fmt.Sprintf("/v1/usage/%s", url.PathEscape(requestID))

	var out RequestUsage
	meta, err := s.client.doJSON(ctx, "GET", path, nil, &out, "", "", "")
	if err != nil {
		return nil, err
	}
	out.Meta = meta
	return &out, nil
}
