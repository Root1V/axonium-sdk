// Package axonium is the Go SDK for the Prometheus Gateway inference API.
//
// The SDK is pure transport. It speaks the gateway's contract faithfully -- authentication,
// retries that cannot double-bill, typed errors, streaming with real cancellation -- and does not
// reshape responses into a vocabulary of its own. Normalization belongs above it, in whatever
// framework is consuming the models, so that there is one implementation of that vocabulary rather
// than one per language SDK.
//
// No host, port, or certificate is baked in: every deployment supplies its own, by field or by
// AXONIUM_* environment variable.
//
//	client, err := axonium.New(axonium.Config{
//		AuthBaseURL:    "https://auth.example",
//		GatewayBaseURL: "https://gateway.example",
//		ClientID:       "...",
//		ClientSecret:   "...",
//	})
//	if err != nil { return err }
//	defer client.Close()
//
//	completion, err := client.Chat.Create(ctx, axonium.ChatRequest{
//		Model:    "llama3-8b-q4",
//		Messages: []axonium.Message{axonium.TextMessage("user", "Hello")},
//	})
package axonium

import (
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"net"
	"net/http"
	"os"
	"strings"
	"sync"
)

const userAgent = "axonium-go/" + Version

// Version is this SDK's version.
const Version = "0.1.0"

// Client is a Prometheus Gateway client. Safe for concurrent use.
type Client struct {
	config   *Config
	http     *http.Client
	auth     authenticator
	cooldown *cooldownRegistry

	Chat       *ChatService
	Models     *ModelsService
	Embeddings *EmbeddingsService
	Images     *ImagesService

	mu            sync.RWMutex
	lastRateLimit *RateLimitSnapshot
}

// New builds a client, resolving unset fields from AXONIUM_* environment variables.
//
// Two credential modes exist, permanent and mutually exclusive. Which one applies follows from how
// the SDK is embedded, not from preference.
//
// Autonomous: the SDK mints and refreshes its own tokens from ClientID and ClientSecret. For
// development, notebooks and tests, where there is no host to ask.
//
// Governed: a host that already owns the credential supplies tokens through TokenProvider, and the
// SDK never holds a secret. The provider is the sole authority -- it owns caching, refresh and
// rotation, and Axonium does no refresh-ahead of its own, because two caches for one token is how
// a client ends up sending a token its owner already retired.
//
// Asking for both by name is a contradiction and is refused. Credentials that merely happen to be
// in the environment are not: the provider wins and the credentials are discarded with a warning,
// so the secret really does leave the process rather than sitting unused. Refusing to start there
// would make the governed mode the hardest one to deploy, which is backwards -- a host process
// almost always has those variables set.
func New(cfg Config) (*Client, error) {
	resolved, err := cfg.resolve()
	if err != nil {
		return nil, err
	}

	if resolved.TokenProvider != nil && resolved.credentialsWereExplicit {
		return nil, fmt.Errorf("%w: both a TokenProvider and explicit credentials were supplied, and they are mutually exclusive modes; pass one or the other", ErrConfiguration)
	}

	httpClient, err := buildHTTPClient(resolved)
	if err != nil {
		return nil, err
	}

	c := &Client{config: resolved, http: httpClient, cooldown: newCooldownRegistry()}

	if resolved.TokenProvider != nil {
		if resolved.ClientID != "" || resolved.ClientSecret != "" {
			// Discarded rather than merely ignored, so that "the SDK never holds a long-lived
			// secret" is a checkable fact about the object instead of a claim about which branch
			// of code reads what.
			resolved.ClientID, resolved.ClientSecret = "", ""
			fmt.Fprintln(os.Stderr, "axonium: credentials found in the environment were discarded; the supplied TokenProvider is the sole authority in governed mode")
		}
		c.auth = newProvidedTokenAuth(resolved.TokenProvider)
	} else {
		if resolved.ClientID == "" || resolved.ClientSecret == "" {
			return nil, fmt.Errorf("%w: no credentials. Set ClientID and ClientSecret (or %sCLIENT_ID and %sCLIENT_SECRET) for the autonomous mode, or supply a TokenProvider for the governed one", ErrConfiguration, envPrefix, envPrefix)
		}
		c.auth = newTokenManager(resolved, httpClient)
	}

	c.Chat = &ChatService{client: c}
	c.Models = &ModelsService{client: c}
	c.Embeddings = &EmbeddingsService{client: c}
	c.Images = &ImagesService{client: c}
	return c, nil
}

func buildHTTPClient(cfg *Config) (*http.Client, error) {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.DialContext = (&net.Dialer{Timeout: cfg.Timeouts.Connect}).DialContext

	if cfg.CABundle != "" {
		pem, err := os.ReadFile(cfg.CABundle)
		if err != nil {
			return nil, fmt.Errorf("%w: could not read CABundle %q: %v", ErrConfiguration, cfg.CABundle, err)
		}
		pool := x509.NewCertPool()
		if !pool.AppendCertsFromPEM(pem) {
			return nil, fmt.Errorf("%w: CABundle %q contained no usable certificates", ErrConfiguration, cfg.CABundle)
		}
		transport.TLSClientConfig = &tls.Config{RootCAs: pool, MinVersion: tls.VersionTLS12}
	}

	// No client-level timeout: it would apply to the whole exchange including the body, which
	// would cut streams off mid-generation. Per-call deadlines are set on the context instead.
	return &http.Client{Transport: transport}, nil
}

// Close releases pooled connections.
func (c *Client) Close() error {
	c.http.CloseIdleConnections()
	return nil
}

// Config returns the resolved configuration. In governed mode the credential fields are empty,
// which is the point: the SDK holds no secret there.
func (c *Client) Config() Config { return *c.config }

// LastRateLimit returns the rate-limit budget from the most recent response that carried one, so a
// caller can slow down before a 429 rather than only reacting to one.
func (c *Client) LastRateLimit() *RateLimitSnapshot {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.lastRateLimit
}

func (c *Client) rememberRateLimit(rl *RateLimitSnapshot) {
	if rl == nil {
		return
	}
	c.mu.Lock()
	c.lastRateLimit = rl
	c.mu.Unlock()
}

// explainForbidden adds the missing scope to a 403, so the error says what is wrong rather than
// only that access was refused.
//
// Access is deny-by-default and granted per model, and streaming and non-streaming take different
// scopes: holding inference:read does not grant inference:stream. That is the mistake this turns
// from a puzzle into a sentence.
func (c *Client) explainForbidden(err *APIError) {
	if err.Status != http.StatusForbidden {
		return
	}
	held := c.auth.scopes()
	if len(held) == 0 {
		return
	}
	err.Hint = fmt.Sprintf("Scope check: the token holds %s.", strings.Join(held, " "))
}
