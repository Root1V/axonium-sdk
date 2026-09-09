package axonium

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

// No host, port, or certificate is baked into this SDK. Base URLs, credentials and TLS trust are
// deployment-specific and always supplied by the caller, either as fields on Config or through
// AXONIUM_* environment variables.

const envPrefix = "AXONIUM_"

const (
	// gatewayNonStreamingTimeout is the gateway's own backend-forwarding timeout for
	// non-streaming requests. A client-side read timeout below this is a known failure mode: the
	// backend keeps computing after the client gives up, and a retry queues a second expensive
	// generation on top of the first.
	gatewayNonStreamingTimeout = 600 * time.Second

	// gatewayStreamingTimeout is the gateway's read timeout on its own connection to the backend
	// when streaming. The client's SSE read timeout sits above this so the SDK never gives up
	// before the gateway would.
	gatewayStreamingTimeout = 120 * time.Second
)

// Timeouts are per-phase HTTP timeouts.
//
// Defaults follow the gateway's own limits. Image generation legitimately takes minutes, so the
// non-streaming timeout is deliberately long; override it per call with a context deadline where a
// faster failure is preferable.
type Timeouts struct {
	Connect time.Duration
	// Request bounds a whole non-streaming call.
	Request time.Duration
	// Stream bounds a streaming call, kept above the gateway's own 120s backend read timeout.
	// Applied as an idle deadline between events rather than to the whole stream, since a long
	// generation is not a stalled one.
	Stream time.Duration
	// Auth bounds a token request. Deliberately short: the auth-service does no inference, so it
	// must not inherit the long timeout that image generation needs.
	Auth time.Duration
}

// DefaultTimeouts returns the per-phase defaults.
func DefaultTimeouts() Timeouts {
	return Timeouts{
		Connect: 10 * time.Second,
		Request: gatewayNonStreamingTimeout,
		Stream:  gatewayStreamingTimeout + 60*time.Second,
		Auth:    30 * time.Second,
	}
}

// Config is the resolved client configuration.
//
// Zero-valued fields fall back to the corresponding AXONIUM_* environment variable. A missing
// required setting is reported as ErrConfiguration naming both the field and the variable that can
// supply it.
type Config struct {
	// AuthBaseURL is the base URL of the auth-service that issues OAuth2 tokens.
	AuthBaseURL string
	// GatewayBaseURL is the base URL of the gateway serving the /v1/ inference API.
	GatewayBaseURL string

	// ClientID and ClientSecret are required in autonomous mode, where the SDK mints its own
	// tokens. Both are absent in governed mode, where a caller-supplied TokenProvider is the
	// authority and the SDK never sees a secret.
	ClientID     string
	ClientSecret string

	// Scope is an optional space-separated scope request. When omitted the token receives the
	// account's full allowed scopes; when supplied the effective scope is the intersection with
	// what the account is allowed, and requesting a scope the account lacks is an error rather
	// than a downgrade. Always read the granted scope back from the token response.
	Scope string

	// CABundle is a path to a CA bundle, for deployments fronted by a self-signed certificate.
	// Production deployments with a CA-signed certificate need nothing here.
	CABundle string

	// VerifyModality checks a model's modality against the endpoint before sending. Costs one
	// catalog request per client, which is why it is opt-in: the SDK otherwise makes no request a
	// caller did not ask for.
	VerifyModality bool

	// RefreshAheadRatio refreshes the token once this fraction of its lifetime has elapsed.
	RefreshAheadRatio float64
	// RefreshAheadMin refreshes once less than this long remains, whichever comes first.
	// Short-lived app-role tokens can default to a 300s TTL, where a ratio alone cuts it too fine.
	RefreshAheadMin time.Duration

	Timeouts Timeouts

	// TokenProvider selects the governed credential mode. See the TokenProvider docs.
	TokenProvider TokenProvider

	// Retry governs when a failed request is retried. The zero value means DefaultRetryPolicy.
	Retry *RetryPolicy

	// credentialsWereExplicit records whether ClientID/ClientSecret were set on the struct rather
	// than read from the environment. Asking for both modes by name is a contradiction and is
	// refused; credentials that merely happen to be in the environment are discarded instead, so
	// the governed mode does not become the hardest one to deploy.
	credentialsWereExplicit bool
}

func env(name string) string { return os.Getenv(envPrefix + name) }

// resolve fills unset fields from the environment and validates the result.
func (c Config) resolve() (*Config, error) {
	out := c
	out.credentialsWereExplicit = c.ClientID != "" || c.ClientSecret != ""

	if out.AuthBaseURL == "" {
		out.AuthBaseURL = env("AUTH_BASE_URL")
	}
	if out.GatewayBaseURL == "" {
		out.GatewayBaseURL = env("GATEWAY_BASE_URL")
	}
	if out.ClientID == "" {
		out.ClientID = env("CLIENT_ID")
	}
	if out.ClientSecret == "" {
		out.ClientSecret = env("CLIENT_SECRET")
	}
	if out.Scope == "" {
		out.Scope = env("SCOPE")
	}
	if out.CABundle == "" {
		out.CABundle = env("CA_BUNDLE")
	}
	if !out.VerifyModality {
		out.VerifyModality = envBool("VERIFY_MODALITY")
	}

	if out.RefreshAheadRatio == 0 {
		out.RefreshAheadRatio = 0.8
	}
	if out.RefreshAheadMin == 0 {
		out.RefreshAheadMin = 30 * time.Second
	}
	out.Timeouts = out.Timeouts.withDefaults()

	var problems []string
	var err error

	if out.AuthBaseURL, err = normalizeURL(out.AuthBaseURL, "AuthBaseURL", "AUTH_BASE_URL"); err != nil {
		problems = append(problems, err.Error())
	}
	if out.GatewayBaseURL, err = normalizeURL(out.GatewayBaseURL, "GatewayBaseURL", "GATEWAY_BASE_URL"); err != nil {
		problems = append(problems, err.Error())
	}
	if out.RefreshAheadRatio <= 0 || out.RefreshAheadRatio > 1 {
		problems = append(problems, "RefreshAheadRatio must be in (0, 1]")
	}

	if len(problems) > 0 {
		return nil, fmt.Errorf("%w: invalid Axonium configuration: %s", ErrConfiguration, strings.Join(problems, "; "))
	}
	return &out, nil
}

func (t Timeouts) withDefaults() Timeouts {
	d := DefaultTimeouts()
	if t.Connect == 0 {
		t.Connect = d.Connect
	}
	if t.Request == 0 {
		t.Request = d.Request
	}
	if t.Stream == 0 {
		t.Stream = d.Stream
	}
	if t.Auth == 0 {
		t.Auth = d.Auth
	}
	return t
}

func normalizeURL(value, field, envVar string) (string, error) {
	if value == "" {
		return "", fmt.Errorf("%s is required (set it or export %s%s)", field, envPrefix, envVar)
	}
	if !strings.HasPrefix(value, "http://") && !strings.HasPrefix(value, "https://") {
		return "", fmt.Errorf("%s must start with http:// or https:// (from field or %s%s)", field, envPrefix, envVar)
	}
	return strings.TrimRight(value, "/"), nil
}

func envBool(name string) bool {
	v, err := strconv.ParseBool(env(name))
	return err == nil && v
}

// Scopes returns the requested scope as a slice, empty when no scope was requested.
func (c *Config) Scopes() []string {
	if c.Scope == "" {
		return nil
	}
	return strings.Fields(c.Scope)
}
