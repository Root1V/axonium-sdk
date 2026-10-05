package axonium

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strings"
)

// The pass-through route: the tasks OpenAI has no shape for.
//
// POST /v1/models/{model}/predict serves three modalities -- classification, zero_shot and
// typed_decision. Every other route on this gateway is OpenAI-shaped because every task it serves
// has an OpenAI endpoint to be shaped like. These do not, and inventing a body for them would be
// the gateway deciding, on the engine's behalf, what the engine's API should look like.
//
// So the body is forwarded to the engine verbatim and its answer comes back verbatim, and the shape
// is not stable even across engines serving the same modality. That is the cost of pass-through and
// it is paid by the caller; Model.PayloadSchema in the catalog is what identifies the shape.
//
// Two engines can serve one modality and disagree, and `tei.predict.v1` is the first case where
// that is not hypothetical. On `zero_shot`, `hf-inference.zero-shot-classification.v1` answers
// `{sequence, labels, scores}` normalised across the candidate labels YOU supplied, while
// `tei.predict.v1` answers scores across the MODEL'S OWN classes and has no notion of candidate labels
// at all. Both sum to 1, over different things. A caller dispatching on `modality` reads one as the
// other; dispatching on `payload_schema` is what prevents it, and this is the case that argument was
// waiting for.
//
// And `tei.predict.v1` has a trap worth reading before building a batch helper. Measured against a
// live server by the platform team:
//
//     inputs: "a text"                     -> one flat list of {label, score}
//     inputs: ["premise", "hypothesis"]    -> ONE PAIR, not a batch of two texts
//     inputs: ["a", "b", "c"]              -> 422
//     inputs: [["a"], ["b"]]               -> a batch of two single texts -> two lists
//     inputs: [["p1","h1"], ["p2","h2"]]   -> a batch of two pairs -> two lists
//
// A batch is ALWAYS a list of lists. A flat array of two strings is read as a single pair and answers
// once, in silence; three or more is a 422. So the obvious "send my N texts as an array" is the one
// form that quietly returns a single wrong answer, and wrapping each text in its own list is the form
// that batches. The per-request cap is set per instance (64 on the reference deployment).
//
// This is also why the result holds its value undecoded: a single input returns a flat list and a
// batch returns a list of lists, from the same model and the same endpoint, so a type that assumed
// either one would be wrong half the time.
//
// What does NOT pass through is the policy: the model still resolves, inference:read plus the
// specific model:<id> scope is still required, a dead replica is still skipped, and the request is
// still metered and still counts against a spend cap.

// PredictResult is whatever the engine returned, undecoded, and the correlation metadata beside it.
type PredictResult struct {
	// Value is the raw JSON body. NOT decoded into map[string]any, because one of the three live
	// shapes is not an object -- measured against a deployment:
	//
	//	sst2-clf     [{"label":"POSITIVE","score":0.978}]   <- a top-level array
	//	von-decide   {"sequence":...,"labels":[...],"scores":[...]}
	//	laya-decide  {"model":...,"answers":{...},"usage":{...},"routing":{...}}
	//
	// A map would have failed to unmarshal the first engine the platform shipped on this route, so
	// the bytes are handed over and the caller unmarshals into whatever the engine's contract says.
	Value json.RawMessage

	// Meta carries the request and trace IDs and the rate-limit budget as of this response. The
	// budget here is predict, shared by all three pass-through modalities rather than one each.
	Meta ResponseMeta
}

// Into unmarshals Value into dest, which is the normal way to read a result.
//
//	var labels []struct {
//		Label string  `json:"label"`
//		Score float64 `json:"score"`
//	}
//	err := result.Into(&labels)
func (r PredictResult) Into(dest any) error {
	if err := json.Unmarshal(r.Value, dest); err != nil {
		return fmt.Errorf("%w: the engine's answer does not fit %T: %v", ErrInvalidRequest, dest, err)
	}
	return nil
}

// PredictService is the pass-through route.
type PredictService struct {
	client *Client
}

// Create sends body to model unchanged and returns its answer unchanged.
//
// Deliberately NOT a Classify(text) typed per modality. That would promise a stability this
// endpoint does not offer -- sst2-clf and von-decide are both classifiers and want different
// payloads. Dispatch on Model.PayloadSchema, not on Model.Modality.
//
//	result, err := client.Predict.Create(ctx, "sst2-clf",
//		map[string]any{"inputs": "El servicio ha sido excelente"}, PredictOptions{})
//
// A model that HAS an OpenAI endpoint is refused here with 400 modality-mismatch -- the inverse of
// every other handler's check. Without it the same model would be reachable two ways, with two
// billing paths and two rate-limit budgets, and the one that billed correctly would be whichever
// the caller did not use.
func (s *PredictService) Create(ctx context.Context, model string, body any, opts PredictOptions) (*PredictResult, error) {
	if strings.TrimSpace(model) == "" {
		return nil, fmt.Errorf("%w: model is required", ErrInvalidRequest)
	}
	if err := checkPredictBody(body); err != nil {
		return nil, err
	}
	if err := s.client.checkModality(ctx, model, modalitiesPredict); err != nil {
		return nil, err
	}

	// The model ID is a path segment here rather than a body field, which is new on this route.
	// Escaping it matters: a slash in an ID would otherwise address a different endpoint, and the
	// gateway would answer about a route instead of about a model.
	path := "/v1/models/" + url.PathEscape(model) + "/predict"

	var raw json.RawMessage
	meta, err := s.client.doJSON(ctx, http.MethodPost, path, body, &raw, model, opts.Instance, opts.IdempotencyKey)
	if err != nil {
		return nil, err
	}
	return &PredictResult{Value: raw, Meta: meta}, nil
}

// PredictOptions carries the per-call knobs the other services take on their request structs. The
// body here belongs to the engine, so they cannot live inside it: a field this SDK added would be
// forwarded to the engine as part of its payload.
type PredictOptions struct {
	// IdempotencyKey makes a retry safe: a repeat with the same key and the same body returns the
	// stored result without reaching a model, recording usage, or counting against the spend cap.
	IdempotencyKey string

	// Instance pins the request to one replica. Diagnostic; leave empty to let the gateway route.
	Instance string
}

// checkPredictBody refuses a body that is not a JSON object.
//
// Every engine seen on this route takes an object. An array or a scalar is not refused because it
// is impossible -- it is refused because nothing in the contract describes one, so sending it would
// be guessing on the caller's behalf, and the engine's own 4xx arrives wrapped as
// predict-backend-rejected with a status that is the engine's rather than the gateway's.
//
// Note the asymmetry with the response, which genuinely can be an array: the request shape is
// something the contract could describe and does, the response shape is the engine's.
func checkPredictBody(body any) error {
	if body == nil {
		return fmt.Errorf("%w: the predict body is required", ErrInvalidRequest)
	}
	encoded, err := json.Marshal(body)
	if err != nil {
		return fmt.Errorf("%w: could not encode the predict body: %v", ErrInvalidRequest, err)
	}
	if trimmed := strings.TrimLeft(string(encoded), " \t\r\n"); !strings.HasPrefix(trimmed, "{") {
		return fmt.Errorf(
			"%w: the predict body must be a JSON object; got %s. The engine's own contract is named by payload_schema in the catalog",
			ErrInvalidRequest, firstRunes(trimmed, 40))
	}
	return nil
}

func firstRunes(s string, n int) string {
	runes := []rune(s)
	if len(runes) <= n {
		return s
	}
	return string(runes[:n]) + "..."
}
