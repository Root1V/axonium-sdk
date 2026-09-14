package axonium

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// Claims are decoded without verifying the signature, which is fine because nothing here makes an
// access-control decision. What matters is that a malformed token degrades to empty claims rather
// than taking the request down with it.
func TestDecodeClaimsToleratesRubbish(t *testing.T) {
	for name, token := range map[string]string{
		"empty":           "",
		"no dots":         "notajwt",
		"one segment":     "header.",
		"not base64":      "header.!!!not-base64!!!.sig",
		"not json":        "header." + b64("this is not json") + ".sig",
		"json but a list": "header." + b64(`["not","an","object"]`) + ".sig",
	} {
		t.Run(name, func(t *testing.T) {
			claims := DecodeClaims(token)
			if claims.Subject != "" || len(claims.Scope) != 0 || claims.ExpiresAt != 0 {
				t.Errorf("rubbish should decode to nothing, got %+v", claims)
			}
		})
	}
}

func TestDecodeClaimsReadsWhatIsThere(t *testing.T) {
	token := makeJWT(map[string]any{
		"sub": "client-1", "client_name": "Axonium", "role": "app",
		"scope": "inference:read model:qwen3-0.6b", "exp": 1700000200, "iat": 1700000000,
	})

	claims := DecodeClaims(token)
	if claims.Subject != "client-1" || claims.ClientName != "Axonium" || claims.Role != "app" {
		t.Errorf("identity claims: %+v", claims)
	}
	if len(claims.Scope) != 2 || claims.Scope[1] != "model:qwen3-0.6b" {
		t.Errorf("scope: %v", claims.Scope)
	}
	if claims.ExpiresAt != 1700000200 || claims.IssuedAt != 1700000000 {
		t.Errorf("times: exp=%d iat=%d", claims.ExpiresAt, claims.IssuedAt)
	}
}

// The token's lifetime comes from the server twice over: expires_in, and the gap between the
// response Date and the JWT exp. Both are server-side readings, so no clock difference between
// this machine and the platform can stretch or shrink the result. The shorter wins, because
// treating a token as shorter-lived only costs an early refresh.
func TestEffectiveLifetimeTakesTheShorterServerReading(t *testing.T) {
	const serverNow = "Tue, 14 Nov 2023 22:13:20 GMT" // exactly epoch 1700000000
	headers := http.Header{}
	headers.Set("Date", serverNow)

	// exp says 200s; expires_in says 300s. The exp wins.
	token := makeJWT(map[string]any{"exp": 1700000200})
	if got := effectiveLifetime(headers, token, 300*time.Second); got != 200*time.Second {
		t.Errorf("got %v, want 200s", got)
	}

	// exp says 400s; expires_in says 300s. expires_in wins.
	token = makeJWT(map[string]any{"exp": 1700000400})
	if got := effectiveLifetime(headers, token, 300*time.Second); got != 300*time.Second {
		t.Errorf("got %v, want 300s", got)
	}

	// Already expired by the server's own reckoning: fail fast rather than spend a request on it.
	token = makeJWT(map[string]any{"exp": 1699999000})
	if got := effectiveLifetime(headers, token, 300*time.Second); got != 0 {
		t.Errorf("an already-expired token should have no life left, got %v", got)
	}

	// Without both readings there is nothing to cross-check, so expires_in stands alone.
	if got := effectiveLifetime(http.Header{}, token, 300*time.Second); got != 300*time.Second {
		t.Errorf("no Date header: got %v", got)
	}
	noExp := makeJWT(map[string]any{"sub": "x"})
	if got := effectiveLifetime(headers, noExp, 300*time.Second); got != 300*time.Second {
		t.Errorf("no exp claim: got %v", got)
	}
	bad := http.Header{}
	bad.Set("Date", "not a date")
	if got := effectiveLifetime(bad, token, 300*time.Second); got != 300*time.Second {
		t.Errorf("an unparseable Date should be ignored, got %v", got)
	}
}

// An absurd expires_in is clamped rather than believed. Trusting it would mean never refreshing
// proactively and falling back to the reactive 401 path forever.
func TestAnAbsurdLifetimeIsClamped(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"access_token": makeJWT(map[string]any{"scope": "inference:read"}) + ".sig",
			"token_type":   "Bearer",
			"expires_in":   999999999,
		})
	}))
	defer srv.Close()

	cfg, err := Config{AuthBaseURL: srv.URL, GatewayBaseURL: srv.URL, ClientID: "i", ClientSecret: "s"}.resolve()
	if err != nil {
		t.Fatalf("resolving: %v", err)
	}
	m := newTokenManager(cfg, srv.Client())
	set, err := m.current(context.Background())
	if err != nil {
		t.Fatalf("fetching: %v", err)
	}
	if set.lifetime > maxPlausibleTTL {
		t.Errorf("lifetime %v exceeds the clamp %v", set.lifetime, maxPlausibleTTL)
	}
}

func TestTokenEndpointFailuresAreTyped(t *testing.T) {
	for _, tc := range []struct {
		name     string
		handler  http.HandlerFunc
		sentinel error
		mustSay  string
	}{
		{"rejected credentials", func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(401)
			_ = json.NewEncoder(w).Encode(map[string]any{"error": "invalid_client", "error_description": "bad secret"})
		}, ErrInvalidClient, "bad secret"},
		{"not json", func(w http.ResponseWriter, r *http.Request) {
			_, _ = w.Write([]byte("<html>a proxy got in the way</html>"))
		}, ErrAuthTransport, "non-JSON"},
		{"no access_token", func(w http.ResponseWriter, r *http.Request) {
			_ = json.NewEncoder(w).Encode(map[string]any{"token_type": "Bearer", "expires_in": 300})
		}, ErrAuthTransport, "no access_token"},
		{"unusable expires_in", func(w http.ResponseWriter, r *http.Request) {
			_ = json.NewEncoder(w).Encode(map[string]any{"access_token": "t", "expires_in": -5})
		}, ErrAuthTransport, "expires_in"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv := httptest.NewServer(tc.handler)
			defer srv.Close()

			cfg, _ := Config{AuthBaseURL: srv.URL, GatewayBaseURL: srv.URL, ClientID: "i", ClientSecret: "s"}.resolve()
			_, err := newTokenManager(cfg, srv.Client()).current(context.Background())

			if !errors.Is(err, tc.sentinel) {
				t.Fatalf("got %v", err)
			}
			if !strings.Contains(err.Error(), tc.mustSay) {
				t.Errorf("the message should mention %q, got %q", tc.mustSay, err)
			}
		})
	}
}

// An OAuth failure must stay outside the gateway taxonomy: a handler for "the gateway is unhappy"
// should not silently absorb "your credentials are wrong".
func TestOAuthErrorMessages(t *testing.T) {
	withDescription := oauthErrorFromBody(401, map[string]any{"error": "invalid_client", "error_description": "bad secret"})
	if !strings.Contains(withDescription.Error(), "bad secret") {
		t.Errorf("got %q", withDescription)
	}
	codeOnly := oauthErrorFromBody(401, map[string]any{"error": "unauthorized_client"})
	if !strings.Contains(codeOnly.Error(), "unauthorized_client") {
		t.Errorf("got %q", codeOnly)
	}
	bare := oauthErrorFromBody(503, nil)
	if !strings.Contains(bare.Error(), "503") {
		t.Errorf("with nothing to report it should still name the status, got %q", bare)
	}
}

// A failed token request can arrive in either of two shapes, and they mean opposite things.
//
// Not in the shared contract corpus because the token endpoint is not in it at all -- each runner
// mocks it per case rather than exercising it. Recorded as its own gap rather than papered over.

func tokenFailing(t *testing.T, status int, body string) *Client {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_, _ = w.Write([]byte(body))
	}))
	t.Cleanup(srv.Close)

	client, err := New(Config{ClientID: "i", ClientSecret: "s", GatewayBaseURL: srv.URL})
	if err != nil {
		t.Fatalf("building: %v", err)
	}
	t.Cleanup(func() { _ = client.Close() })
	return client
}

func TestA4xxFromTheTokenEndpointIsAnOAuth2Error(t *testing.T) {
	client := tokenFailing(t, 401, `{"error":"invalid_client","error_description":"Invalid."}`)

	_, err := client.Models.Mine(context.Background())

	var oauthErr *OAuthError
	if !errors.As(err, &oauthErr) {
		t.Fatalf("got %T: %v", err, err)
	}
	if oauthErr.Code != "invalid_client" {
		t.Errorf("code: got %q", oauthErr.Code)
	}
}

func TestA503FromTheTokenEndpointIsTheGatewayFailingAndIsRetryable(t *testing.T) {
	// The distinction that matters: reading this as OAuth2 would give it no type and no
	// retryability, so a momentary blip would look exactly like bad credentials and the caller
	// would abandon a request that was about to succeed.
	client := tokenFailing(t, 503,
		`{"type":"https://prometheus.internal/errors/upstream-unavailable","status":503,"detail":"x"}`)

	_, err := client.Models.Mine(context.Background())

	var apiErr *APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("got %T: %v", err, err)
	}
	if apiErr.TypeSuffix != "upstream-unavailable" {
		t.Errorf("suffix: got %q", apiErr.TypeSuffix)
	}
	if !apiErr.Retryable() {
		t.Error("a gateway failure reaching the auth-service should be retryable")
	}
	if !errors.Is(err, ErrTokenEndpointUnavailable) {
		t.Error("did not match its sentinel")
	}
}

func TestADeploymentWithoutATokenEndpointIsNotRetryable(t *testing.T) {
	// Same status as the one above and the opposite answer, which is why the suffix has to drive
	// the decision rather than the status.
	client := tokenFailing(t, 503,
		`{"type":"https://prometheus.internal/errors/not-configured","status":503,"detail":"x"}`)

	_, err := client.Models.Mine(context.Background())

	var apiErr *APIError
	if !errors.As(err, &apiErr) || apiErr.Retryable() {
		t.Fatalf("got %T retryable=%v", err, err)
	}
}
