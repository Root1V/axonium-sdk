package axonium

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

// The platform issues short-lived access tokens and has no refresh-token grant: a new token is
// obtained by repeating the client_credentials request. Token lifetimes are per-account and an
// operator can override them, so the TTL is always read from the token response's expires_in and
// never assumed.
//
// The manager refreshes ahead of expiry rather than waiting for a 401, so the normal path never
// spends a failed request discovering that a token died. The reactive 401 path still exists as a
// fallback for a token revoked mid-flight, but it is not the primary mechanism.

const tokenEndpoint = "/oauth2/token"

// maxPlausibleTTL is the longest lifetime an operator can configure for an account. A larger
// expires_in is not something the platform can legitimately issue, so it is clamped rather than
// trusted: believing it would mean never refreshing proactively and falling back to the reactive
// 401 path forever.
const maxPlausibleTTL = 24 * time.Hour

// tokenSet is a cached access token and everything needed to decide when to replace it.
type tokenSet struct {
	accessToken string
	// expiresAt is a time.Time captured from time.Now, which carries a monotonic reading that Add
	// preserves and Before uses. The comparison therefore measures elapsed time rather than wall
	// clock, so neither a constant offset against the auth-service nor an NTP step can make a live
	// token look expired. The length of the life still comes from the server; see effectiveLifetime.
	expiresAt time.Time
	lifetime  time.Duration
	// scope is what the server actually granted, which may be narrower than what was requested.
	scope []string
}

func (t *tokenSet) needsRefresh(ratio float64, min time.Duration) bool {
	remaining := time.Until(t.expiresAt)
	return remaining <= time.Duration(float64(t.lifetime)*(1.0-ratio)) || remaining < min
}

// TokenClaims are claims read out of an access token.
//
// Decoded without verifying the signature, which is fine because this is introspection for display
// and diagnostics only. The gateway is the sole authority on what a token may do -- never make an
// access-control decision from these values. Note that the gateway does not use role for
// authorization either; only scope governs what a token can actually call.
type TokenClaims struct {
	Subject    string
	ClientName string
	Role       string
	Scope      []string
	ExpiresAt  int64
	IssuedAt   int64
	Raw        map[string]any
}

// DecodeClaims reads a JWT's payload without verifying it. See TokenClaims.
func DecodeClaims(accessToken string) TokenClaims {
	segments := strings.Split(accessToken, ".")
	if len(segments) < 2 {
		return TokenClaims{}
	}
	decoded, err := base64.RawURLEncoding.DecodeString(strings.TrimRight(segments[1], "="))
	if err != nil {
		return TokenClaims{}
	}
	var payload map[string]any
	if json.Unmarshal(decoded, &payload) != nil {
		return TokenClaims{}
	}

	claims := TokenClaims{
		Subject:    stringOr(payload["sub"]),
		ClientName: stringOr(payload["client_name"]),
		Role:       stringOr(payload["role"]),
		Raw:        payload,
	}
	if scope, ok := payload["scope"].(string); ok {
		claims.Scope = strings.Fields(scope)
	}
	if exp, ok := numeric(payload["exp"]); ok {
		claims.ExpiresAt = int64(exp)
	}
	if iat, ok := numeric(payload["iat"]); ok {
		claims.IssuedAt = int64(iat)
	}
	return claims
}

// authenticator attaches credentials to outgoing requests. Both credential modes implement it.
type authenticator interface {
	// apply sets the Authorization header, returning the token it used so that a subsequent
	// rejection can name it.
	apply(ctx context.Context, req *http.Request) (string, error)
	// refresh is called after a 401 with the token that was rejected.
	refresh(ctx context.Context, rejected string) (string, error)
	// scopes returns the scope of the last token used, for diagnosing a 403.
	scopes() []string
}

// tokenManager fetches, caches and refreshes access tokens.
//
// Safe for concurrent use: refreshes are guarded so that a burst of concurrent requests arriving
// on an expired token produces one token request, not one per caller.
type tokenManager struct {
	config *Config
	http   *http.Client

	mu    sync.Mutex
	token *tokenSet
}

func newTokenManager(cfg *Config, httpClient *http.Client) *tokenManager {
	return &tokenManager{config: cfg, http: httpClient}
}

func (m *tokenManager) apply(ctx context.Context, req *http.Request) (string, error) {
	t, err := m.current(ctx)
	if err != nil {
		return "", err
	}
	setBearer(req, t.accessToken)
	return t.accessToken, nil
}

func (m *tokenManager) current(ctx context.Context) (*tokenSet, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.token != nil && !m.token.needsRefresh(m.config.RefreshAheadRatio, m.config.RefreshAheadMin) {
		return m.token, nil
	}
	return m.fetchLocked(ctx)
}

func (m *tokenManager) refresh(ctx context.Context, rejected string) (string, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	// Another caller may have replaced the token while this request was in flight; reusing theirs
	// avoids a redundant token request under a concurrent 401 burst.
	if m.token != nil && m.token.accessToken != rejected {
		return m.token.accessToken, nil
	}
	t, err := m.fetchLocked(ctx)
	if err != nil {
		return "", err
	}
	return t.accessToken, nil
}

func (m *tokenManager) scopes() []string {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.token == nil {
		return nil
	}
	return m.token.scope
}

// Claims returns the claims of the cached token, or the zero value if none has been obtained.
func (m *tokenManager) claims() TokenClaims {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.token == nil {
		return TokenClaims{}
	}
	return DecodeClaims(m.token.accessToken)
}

func (m *tokenManager) fetchLocked(ctx context.Context) (*tokenSet, error) {
	if m.config.ClientID == "" || m.config.ClientSecret == "" {
		return nil, fmt.Errorf("%w: this client has no credentials; supply ClientID and ClientSecret, or a TokenProvider", ErrAuthTransport)
	}

	form := url.Values{
		"grant_type":    {"client_credentials"},
		"client_id":     {m.config.ClientID},
		"client_secret": {m.config.ClientSecret},
	}
	if m.config.Scope != "" {
		form.Set("scope", m.config.Scope)
	}

	ctx, cancel := context.WithTimeout(ctx, m.config.Timeouts.Auth)
	defer cancel()

	// Captured before the request is sent, so the round trip is charged against the token's life
	// instead of being granted as extra margin.
	issuedAt := time.Now()

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, m.config.AuthBaseURL+tokenEndpoint, strings.NewReader(form.Encode()))
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrAuthTransport, err)
	}
	// The endpoint is form-encoded, not JSON.
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("Accept", "application/json")

	resp, err := m.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("%w: could not reach the auth-service: %v", ErrAuthTransport, err)
	}
	defer drainAndClose(resp.Body)

	body, _ := decodeJSONObject(resp.Body)

	if resp.StatusCode != http.StatusOK {
		return nil, oauthErrorFromBody(resp.StatusCode, body)
	}
	if body == nil {
		return nil, fmt.Errorf("%w: the auth-service returned a non-JSON %d response", ErrAuthTransport, resp.StatusCode)
	}

	accessToken := stringOr(body["access_token"])
	if accessToken == "" {
		return nil, fmt.Errorf("%w: the auth-service response contained no access_token", ErrAuthTransport)
	}
	expiresIn, ok := numeric(body["expires_in"])
	if !ok || expiresIn <= 0 {
		return nil, fmt.Errorf("%w: the auth-service returned an unusable expires_in: %v", ErrAuthTransport, body["expires_in"])
	}

	lifetime := effectiveLifetime(resp.Header, accessToken, time.Duration(expiresIn*float64(time.Second)))
	if lifetime > maxPlausibleTTL {
		lifetime = maxPlausibleTTL
	}

	// Always the granted scope, never the requested one.
	var scope []string
	if granted, ok := body["scope"].(string); ok {
		scope = strings.Fields(granted)
	}

	m.token = &tokenSet{
		accessToken: accessToken,
		expiresAt:   issuedAt.Add(lifetime),
		lifetime:    lifetime,
		scope:       scope,
	}
	return m.token, nil
}

// effectiveLifetime decides how long a token is really good for.
//
// expires_in is the server's own answer and is normally used as-is. When both the response's Date
// header and the token's exp claim are present, the difference between them is a second,
// independent reading of the same lifetime -- and a skew-free one, because both values come from
// the server's clock rather than being compared against ours.
//
// The shorter of the two wins. Disagreement should not happen, but treating a token as expiring
// sooner only causes an early refresh, whereas treating it as living longer causes a request to
// fail on an expired token.
func effectiveLifetime(headers http.Header, accessToken string, expiresIn time.Duration) time.Duration {
	serverNow := headers.Get("Date")
	exp := DecodeClaims(accessToken).ExpiresAt
	if serverNow == "" || exp == 0 {
		return expiresIn
	}
	issued, err := http.ParseTime(serverNow)
	if err != nil {
		return expiresIn
	}

	serverRemaining := time.Duration(exp-issued.Unix()) * time.Second
	if serverRemaining <= 0 {
		// Already expired by the server's own reckoning; fail fast rather than spend a request
		// discovering it.
		return 0
	}
	if serverRemaining < expiresIn {
		return serverRemaining
	}
	return expiresIn
}

// setBearer attaches the token as a Bearer header.
//
// The header is the only accepted transport; the gateway rejects a token passed as a query
// parameter outright, specifically to keep credentials out of server, proxy and browser logs.
func setBearer(req *http.Request, token string) {
	req.Header.Set("Authorization", "Bearer "+token)
}
