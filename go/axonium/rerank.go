package axonium

import (
	"context"
	"fmt"
	"net/http"
	"strings"
)

// The rerank endpoint.
//
// A reranker is a cross-encoder: it scores a query against each document and returns them ordered.
// It generates nothing, so there are no completion tokens and billing is prompt-only.
//
// Worth knowing if you are coming from a chat-based workaround: the whole document set is one
// request rather than one per document, which against a 60 RPM budget is the difference between
// scoring 50 candidates for 1 unit and for 50.

// RerankRequest is the body for POST /v1/rerank.
type RerankRequest struct {
	Model string `json:"model"`
	Query string `json:"query"`
	// Documents is the whole set in one request. Must be non-empty: the gateway answers
	// 400 validation-error otherwise, and this SDK refuses before the wire.
	Documents []string `json:"documents"`
	// TopN is optional; leave nil to get every document back.
	TopN *int `json:"top_n,omitempty"`

	// IdempotencyKey makes a retry safe: a repeat with the same key and the same body returns the
	// stored result without reaching a model, recording usage, or counting against the spend cap.
	IdempotencyKey string `json:"-"`
}

func (r *RerankRequest) validate() error {
	var problems []string
	if strings.TrimSpace(r.Model) == "" {
		problems = append(problems, "model is required")
	}
	if strings.TrimSpace(r.Query) == "" {
		problems = append(problems, "query is required")
	}
	// The gateway answers 400 validation-error for an empty list. Refusing here saves the round
	// trip and says which field, which the envelope does too but only after the fact.
	if len(r.Documents) == 0 {
		problems = append(problems, "documents must contain at least one document")
	}
	if len(problems) > 0 {
		return fmt.Errorf("%w: %s", ErrInvalidRequest, strings.Join(problems, "; "))
	}
	return nil
}

// RerankResult is one document's score against the query.
type RerankResult struct {
	// Index is the position in the Documents slice YOU sent, which is what keeps a reordered
	// result attributable to its input. Never an index into Results.
	Index int `json:"index"`
	// RelevanceScore is a probability in [0, 1], computed by the engine rather than reconstructed
	// from logprobs.
	RelevanceScore float64 `json:"relevance_score"`
}

// RerankResponse is the documents scored against a query, best first.
type RerankResponse struct {
	Object  string         `json:"object"`
	Model   string         `json:"model"`
	Results []RerankResult `json:"results"`
	// Usage has TotalTokens equal to PromptTokens: a reranker generates nothing.
	Usage *Usage `json:"usage,omitempty"`

	// Raw is the decoded body as received, so a field this SDK does not model stays reachable.
	Raw map[string]any `json:"-"`

	Meta ResponseMeta `json:"-"`
}

// Ranking returns the indices of your documents, best first.
//
// The common case is reordering the slice you already hold, and doing that through Results means
// remembering that Index points into the input rather than the output.
func (r *RerankResponse) Ranking() []int {
	out := make([]int, 0, len(r.Results))
	for _, result := range r.Results {
		out = append(out, result.Index)
	}
	return out
}

// RerankService scores documents against a query.
type RerankService struct{ client *Client }

// Create scores Documents against Query, best first.
//
// The model must have rerank modality; a rerank model returns 400 modality-mismatch from the chat
// endpoint, and a chat model returns it from here.
func (s *RerankService) Create(ctx context.Context, req RerankRequest) (*RerankResponse, error) {
	if err := req.validate(); err != nil {
		return nil, err
	}
	if err := s.client.checkModality(ctx, req.Model, []string{"rerank"}); err != nil {
		return nil, err
	}

	var out RerankResponse
	meta, err := s.client.doJSON(ctx, http.MethodPost, "/v1/rerank", req, &out, req.Model, "", req.IdempotencyKey)
	if err != nil {
		return nil, err
	}
	out.Meta = meta
	return &out, nil
}
