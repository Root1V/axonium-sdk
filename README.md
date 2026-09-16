# Axonium

Client SDKs for the **Prometheus Gateway** inference API, in Python, Go and Rust.

Axonium wraps the platform's `/v1/` inference API and everything around it that a production
integration needs: OAuth2 `client_credentials` authentication with refresh-ahead token caching,
a typed error hierarchy mapped to the gateway's RFC 9457 problem-details catalog, SSE streaming
with in-band failure detection, retry behavior that complements (rather than fights) the
gateway's own server-side retries and circuit breaker, and rate-limit visibility on every
response.

## Language matrix

| Language | Install | Version |
|---|---|---|
| Python | `pip install axonium` | [`1.0.0rc4`](https://pypi.org/project/axonium/) on PyPI |
| Go | `go get github.com/Root1V/axonium-sdk/go@v0.3.0` | `v0.3.0` |
| Rust | `axonium = "0.3"` | [`0.3.0`](https://crates.io/crates/axonium) on crates.io |

All three are published and implement the same surface. Python was built first as the reference
implementation; every behaviour the three share is pinned by the contract corpus in
[`spec/`](spec/) — one manifest, one set of recorded wire bytes, three runners that share no code,
so identical behaviour is verified rather than intended.

Backlog and delivered work: [ROADMAP.md](ROADMAP.md).

## Repository layout

```
spec/       The API contract: vendored integration guide, error catalog,
            and language-neutral contract-test fixtures shared by all SDKs
python/     Python SDK  (source of truth for behavior)
go/         Go SDK
rust/       Rust SDK
```

Run everything CI runs, in one command:

```bash
./scripts/verify.sh
```

It mirrors the workflow files deliberately — a local check that is *nearly* the CI check reports
green and hides the difference.


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
