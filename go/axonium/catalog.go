package axonium

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"sync"
)

// Model is one entry of the gateway's catalog.
type Model struct {
	ID       string   `json:"id"`
	Object   string   `json:"object"`
	OwnedBy  string   `json:"owned_by"`
	Modality string   `json:"modality"`
	Aliases  []string `json:"aliases,omitempty"`

	Raw map[string]any `json:"-"`
}

// ModelList is a catalog response.
type ModelList struct {
	Object string  `json:"object"`
	Data   []Model `json:"data"`

	Meta ResponseMeta `json:"-"`

	// Raw is the decoded body as received, so backend-specific fields this SDK does
	// not model stay reachable rather than being dropped.
	Raw map[string]any `json:"-"`
}

func (l *ModelList) setRaw(raw map[string]any) { l.Raw = raw }

// IDs returns just the model identifiers.
func (l *ModelList) IDs() []string {
	ids := make([]string, 0, len(l.Data))
	for _, m := range l.Data {
		ids = append(ids, m.ID)
	}
	return ids
}

// Find returns the model with the given ID, matching aliases too.
func (l *ModelList) Find(id string) (*Model, bool) {
	for i := range l.Data {
		if l.Data[i].ID == id {
			return &l.Data[i], true
		}
		for _, alias := range l.Data[i].Aliases {
			if alias == id {
				return &l.Data[i], true
			}
		}
	}
	return nil, false
}

// ModelsService is the model catalog API.
type ModelsService struct {
	client *Client

	mu     sync.Mutex
	mine   *ModelList
	cached bool
}

// List returns the full public catalog. This endpoint needs no authentication, so it works before
// any credential is configured -- useful for checking connectivity.
func (s *ModelsService) List(ctx context.Context) (*ModelList, error) {
	var out ModelList
	meta, err := s.client.doJSON(ctx, http.MethodGet, "/v1/models", nil, &out)
	if err != nil {
		return nil, err
	}
	out.Meta = meta
	return &out, nil
}

// Mine returns the models this token is actually allowed to call.
//
// Access is deny-by-default and granted per model, so the public catalog is not the answer to
// "what can I call": this endpoint is. The result is cached for the client's lifetime, since scope
// grants do not change within one process's run.
func (s *ModelsService) Mine(ctx context.Context) (*ModelList, error) {
	s.mu.Lock()
	if s.cached {
		defer s.mu.Unlock()
		return s.mine, nil
	}
	s.mu.Unlock()

	var out ModelList
	meta, err := s.client.doJSON(ctx, http.MethodGet, "/v1/models/mine", nil, &out)
	if err != nil {
		return nil, err
	}
	out.Meta = meta

	s.mu.Lock()
	s.mine, s.cached = &out, true
	s.mu.Unlock()
	return &out, nil
}

// Modality sets accepted by each endpoint.
var (
	modalitiesChat       = []string{"text", "vision"}
	modalitiesEmbeddings = []string{"embedding"}
	modalitiesImages     = []string{"image"}
)

// checkModality verifies a model's modality against the endpoint before sending.
//
// The gateway's own check is one-directional: calling /v1/embeddings with a text model is
// rejected, but calling /v1/chat/completions with an embedding model is not -- it returns 200 with
// degenerate output that is billed. This closes that gap, and catches typos, before the request is
// sent.
//
// Off unless Config.VerifyModality is set, because it costs one catalog request per client and the
// SDK otherwise makes no request a caller did not ask for. If the catalog cannot be loaded the
// check is skipped rather than failing the request: a guard rail must not become a new way for
// inference to break.
func (c *Client) checkModality(ctx context.Context, model string, accepted []string) error {
	if !c.config.VerifyModality {
		return nil
	}

	catalog, err := c.Models.List(ctx)
	if err != nil {
		return nil
	}
	found, ok := catalog.Find(model)
	if !ok {
		return fmt.Errorf("%w: model %q is not in the gateway's catalog; known models: %s",
			ErrInvalidRequest, model, strings.Join(catalog.IDs(), ", "))
	}
	if found.Modality == "" {
		return nil
	}
	for _, a := range accepted {
		if found.Modality == a {
			return nil
		}
	}
	return fmt.Errorf("%w: model %q has modality %q, which this endpoint does not accept (it takes %s); the gateway would answer 200 with degenerate output and bill for it",
		ErrInvalidRequest, model, found.Modality, strings.Join(accepted, " or "))
}
