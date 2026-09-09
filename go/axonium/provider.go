package axonium

import (
	"context"
	"fmt"
	"net/http"
	"sync"
)

// The governed path runs Axonium inside a host that already owns the Prometheus credential.
// Handing that host's long-lived secret to the SDK as well would put it in two places in one
// process for no gain, so in that mode the SDK never sees a secret: it asks the host for a token
// and the host remains the sole authority over how one is minted, cached and rotated.
//
// Because the provider is the authority, Axonium keeps no cache of its own here and does no
// refresh-ahead. Two caches for one token is how a client ends up sending a token its owner
// already retired. What remains is the reactive path: a 401 means "this one is dead", and the SDK
// says so.

// TokenProvider supplies access tokens for the governed credential mode.
//
// It is called with the token that was just rejected, or "" when none has been obtained yet, and
// returns a token to use. The provider owns caching, refresh and rotation.
//
// The rejected token is passed back rather than a boolean. With a flag, a provider receiving two
// concurrent refresh requests cannot tell whether they concern the same dead token or two
// different ones, so it must either mint twice or guess with a time window. Given the token itself
// the answer is exact: if what it holds already differs from the rejected one, it refreshed
// already and returns what it has; only if they match does it mint, once, under its own lock.
//
// The context is the caller's, so a provider that makes its own network call inherits the caller's
// deadline and cancellation rather than outliving the request it exists to serve.
type TokenProvider func(ctx context.Context, rejected string) (string, error)

// providedTokenAuth attaches tokens obtained from a caller-supplied provider.
type providedTokenAuth struct {
	provider TokenProvider

	mu sync.RWMutex
	// scope of the last token applied, kept so a 403 can still be diagnosed. Only the scope
	// strings are retained, never the token: holding the token would recreate the second cache
	// this mode exists to avoid, whereas holding the scopes it carried is metadata, and it is what
	// turns a bare "forbidden" into the missing scope's name.
	scope []string
}

func newProvidedTokenAuth(provider TokenProvider) *providedTokenAuth {
	return &providedTokenAuth{provider: provider}
}

func (p *providedTokenAuth) apply(ctx context.Context, req *http.Request) (string, error) {
	token, err := p.call(ctx, "")
	if err != nil {
		return "", err
	}
	setBearer(req, token)
	p.remember(token)
	return token, nil
}

func (p *providedTokenAuth) refresh(ctx context.Context, rejected string) (string, error) {
	token, err := p.call(ctx, rejected)
	if err != nil {
		return "", err
	}
	p.remember(token)
	return token, nil
}

func (p *providedTokenAuth) call(ctx context.Context, rejected string) (string, error) {
	token, err := p.provider(ctx, rejected)
	if err != nil {
		return "", fmt.Errorf("%w: the token provider failed: %w", ErrAuthTransport, err)
	}
	if token == "" {
		// Left empty it would produce an "Authorization: Bearer " header and a 401 whose cause is
		// invisible at the call site.
		return "", fmt.Errorf("%w: the token provider returned an empty token", ErrAuthTransport)
	}
	return token, nil
}

func (p *providedTokenAuth) remember(token string) {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.scope = DecodeClaims(token).Scope
}

func (p *providedTokenAuth) scopes() []string {
	p.mu.RLock()
	defer p.mu.RUnlock()
	return p.scope
}
