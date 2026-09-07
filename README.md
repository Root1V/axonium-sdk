# Axonium

Client SDKs for the **Prometheus Gateway** inference API, in Python, Go and Rust.

Axonium wraps the platform's `/v1/` inference API and everything around it that a production
integration needs: OAuth2 `client_credentials` authentication with refresh-ahead token caching,
a typed error hierarchy mapped to the gateway's RFC 9457 problem-details catalog, SSE streaming
with in-band failure detection, retry behavior that complements (rather than fights) the
gateway's own server-side retries and circuit breaker, and rate-limit visibility on every
response.

## Language matrix

| Language | Package | Status |
|---|---|---|
| Python | [`axonium`](python/) on PyPI | ✅ Release candidate — reference implementation |
| Go | `github.com/Root1V/axonium-sdk/go` | 📋 Planned — see [go/README.md](go/) |
| Rust | [`axonium`](rust/) on crates.io | 📋 Planned — see [rust/README.md](rust/) |

Python is built first as the reference implementation. Go and Rust follow, validated against
the same shared contract fixtures in [`spec/`](spec/) so all three behave identically.

Backlog and delivered work: [ROADMAP.md](ROADMAP.md).

## Repository layout

```
spec/       The API contract: vendored integration guide, error catalog,
            and language-neutral contract-test fixtures shared by all SDKs
python/     Python SDK  (source of truth for behavior)
go/         Go SDK
rust/       Rust SDK
```

## Configuration

No SDK in this repo hardcodes a host, port, or certificate. Base URLs, credentials and TLS
trust are supplied per deployment through constructor arguments or environment variables, and
a missing required setting fails immediately with a message naming the setting and its
environment variable. Ask your platform operator for the base URLs, TLS trust chain and client
credentials for your target environment.

## Versioning and releases

Each language releases independently under a prefixed tag:

| Tag | Publishes |
|---|---|
| `python/vX.Y.Z` | PyPI |
| `go/vX.Y.Z` | Go module proxy |
| `rust/vX.Y.Z` | crates.io |

Bare `vX.Y.Z` tags belong to the legacy SDK described below and are not used for new releases.

## Legacy SDK (v0.6.0 and earlier)

Versions up to [`v0.6.0`](https://github.com/Root1V/axonium-sdk/releases/tag/v0.6.0) were a
single Python package built for the **first generation** of the Prometheus platform, with a
different authentication scheme, different endpoints, and Langfuse-coupled observability. That
API is gone; the current SDKs are a ground-up rewrite against the current gateway contract and
are not backward compatible.

The legacy code remains available at tag `v0.6.0` and in the git history.

## License

MIT — see [LICENSE](LICENSE).
