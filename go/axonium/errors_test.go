package axonium

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
)

// The error taxonomy is shared across every language SDK through spec/errors.json. Asserting
// against that file rather than a hand-written list is what stops the taxonomies drifting: a row
// added to the catalog fails here until this SDK maps it.

type errorCatalog struct {
	GatewayErrors []struct {
		Status    int    `json:"status"`
		Suffix    string `json:"suffix"`
		Retryable bool   `json:"retryable"`
	} `json:"gateway_errors"`
	OAuthErrors []struct {
		Status string `json:"-"`
		Code   string `json:"error"`
	} `json:"oauth_errors"`
}

func loadCatalog(t *testing.T) errorCatalog {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(specDir(t), "errors.json"))
	if err != nil {
		t.Fatalf("reading the error catalog: %v", err)
	}
	var catalog errorCatalog
	if err := json.Unmarshal(raw, &catalog); err != nil {
		t.Fatalf("parsing the error catalog: %v", err)
	}
	return catalog
}

func TestEveryCatalogedErrorMapsToASentinel(t *testing.T) {
	catalog := loadCatalog(t)
	if len(catalog.GatewayErrors) == 0 {
		t.Fatal("the catalog lists no gateway errors")
	}

	for _, entry := range catalog.GatewayErrors {
		sentinel, ok := suffixSentinels[entry.Suffix]
		if !ok {
			t.Errorf("%s is in spec/errors.json but this SDK maps no sentinel for it", entry.Suffix)
			continue
		}

		err := errorFromBody(entry.Status, map[string]any{
			"type":   "https://gateway.example/errors/" + entry.Suffix,
			"title":  entry.Suffix,
			"detail": "something went wrong",
		}, nil, nil)

		if !errors.Is(err, sentinel) {
			t.Errorf("%s did not match its own sentinel", entry.Suffix)
		}
		if err.Retryable() != entry.Retryable {
			t.Errorf("%s: retryable is %v here, %v in the catalog", entry.Suffix, err.Retryable(), entry.Retryable)
		}
	}
}

func TestEveryCatalogedOAuthErrorMapsToASentinel(t *testing.T) {
	for _, entry := range loadCatalog(t).OAuthErrors {
		err := oauthErrorFromBody(400, map[string]any{"error": entry.Code})
		sentinel, ok := oauthSentinels[entry.Code]
		if !ok {
			t.Errorf("%s is in spec/errors.json but this SDK maps no sentinel for it", entry.Code)
			continue
		}
		if !errors.Is(err, sentinel) {
			t.Errorf("%s did not match its own sentinel", entry.Code)
		}
	}
}

// An OAuth failure must not be catchable as a gateway error. The two envelopes mean different
// things, and a caller handling "the gateway is unhappy" should not silently absorb "your
// credentials are wrong".
func TestOAuthErrorsAreNotAPIErrors(t *testing.T) {
	err := oauthErrorFromBody(401, map[string]any{"error": "invalid_client"})

	var apiErr *APIError
	if errors.As(err, &apiErr) {
		t.Fatal("an OAuth error must not satisfy errors.As for *APIError")
	}
	for _, sentinel := range []error{ErrUnauthorized, ErrServer, ErrBadRequest} {
		if errors.Is(err, sentinel) {
			t.Fatalf("an OAuth error must not match the gateway sentinel %v", sentinel)
		}
	}
}

// An unknown suffix must fall back by status rather than failing to parse: the catalog will grow,
// and an SDK that hard-failed on an unfamiliar code would break the day the gateway adds one.
func TestUnknownSuffixFallsBackByStatus(t *testing.T) {
	for _, tc := range []struct {
		status   int
		sentinel error
	}{
		{404, ErrBadRequest},
		{401, ErrUnauthorized},
		{500, ErrServer},
	} {
		err := errorFromBody(tc.status, map[string]any{
			"type": "https://gateway.example/errors/invented-tomorrow",
		}, nil, nil)
		if !errors.Is(err, tc.sentinel) {
			t.Errorf("status %d with an unknown suffix should match the status fallback", tc.status)
		}
	}
}

// The observed 422: FastAPI's default shape, no type field, no request_id. An SDK that assumed
// every error carries a type would break here, which is exactly why the fallback is by status.
func TestValidationErrorWithoutProblemEnvelope(t *testing.T) {
	err := errorFromBody(422, map[string]any{
		"detail": []any{map[string]any{"loc": []any{"body", "model"}, "msg": "field required"}},
	}, nil, nil)

	if err.TypeSuffix != "" {
		t.Errorf("a 422 carries no type suffix, got %q", err.TypeSuffix)
	}
	if !errors.Is(err, ErrBadRequest) {
		t.Error("a 422 should still fall back to the 4xx sentinel")
	}
	if err.Retryable() {
		t.Error("a validation failure is never retryable: the request is the problem")
	}
}
