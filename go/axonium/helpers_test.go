package axonium

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"sync/atomic"
	"testing"
	"time"
)

// testClient builds a client pointed at a test server, in autonomous mode.
func testClient(t *testing.T, baseURL string) *Client {
	t.Helper()
	client, err := New(Config{
		AuthBaseURL:    baseURL,
		GatewayBaseURL: baseURL,
		ClientID:       "test-client",
		ClientSecret:   "test-secret",
	})
	if err != nil {
		t.Fatalf("building the client: %v", err)
	}
	t.Cleanup(func() { _ = client.Close() })
	return client
}

func simpleRequest() ChatRequest {
	return ChatRequest{
		Model:    "llama3-8b-q4",
		Messages: []Message{TextMessage("user", "Hello")},
	}
}

// mintCounter makes every issued token distinct. Without it two tokens minted in the same second
// are byte-identical, and a test exercising the reactive 401 path would silently be replaying the
// rejected token rather than a fresh one -- passing or failing for the wrong reason.
var mintCounter atomic.Int64

// writeToken writes an OAuth2 token response carrying a JWT whose exp matches expiresIn, so the
// server Date / exp cross-check has something consistent to read.
func writeToken(w http.ResponseWriter, token string, expiresIn int) {
	jwt := makeJWT(map[string]any{
		"sub":   "test",
		"jti":   mintCounter.Add(1),
		"scope": "inference:read inference:stream model:llama3-8b-q4",
		"exp":   time.Now().Add(time.Duration(expiresIn) * time.Second).Unix(),
	})
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"access_token": jwt + "." + token,
		"token_type":   "Bearer",
		"expires_in":   expiresIn,
		"scope":        "inference:read inference:stream model:llama3-8b-q4",
	})
}

// makeJWT builds the header.payload prefix of an unsigned JWT. The SDK decodes claims without
// verifying, by design, so a signature is not needed to exercise that path.
func makeJWT(claims map[string]any) string {
	header := base64.RawURLEncoding.EncodeToString([]byte(`{"alg":"none","typ":"JWT"}`))
	encoded, _ := json.Marshal(claims)
	return header + "." + base64.RawURLEncoding.EncodeToString(encoded)
}

// problemJSON writes an RFC 9457 problem-details error.
func problemJSON(w http.ResponseWriter, status int, suffix, detail string) {
	w.Header().Set("Content-Type", "application/problem+json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"type":       fmt.Sprintf("https://gateway.example/errors/%s", suffix),
		"title":      suffix,
		"status":     status,
		"detail":     detail,
		"request_id": "req-1",
		"trace_id":   "trace-1",
	})
}
