# Changelog

Each language SDK versions independently. Entries are grouped by language and use tags of the
form `python/vX.Y.Z`, `go/vX.Y.Z`, `rust/vX.Y.Z`.

## Python

### 1.0.0rc1 — unreleased

Ground-up rewrite targeting the Prometheus Gateway API. Not backward compatible with `v0.6.0`,
which spoke to a platform generation that no longer exists.

**Client**

- `Axonium` and `AsyncAxonium`, mirroring each other exactly. Every behavior is tested against
  both, so async paths cannot quietly diverge.
- Resources: `models.list()` / `models.mine()`, `chat.completions.create()` /
  `chat.completions.stream()`, `embeddings.create()`, `images.generate()`.
- Python 3.10+ (down from 3.13, which excluded most enterprise environments).

**Authentication**

- OAuth2 `client_credentials`, form-encoded, with refresh-ahead caching so a request never fails
  merely to discover its token expired. The reactive `401` path remains as a fallback.
- Token lifetime is anchored to the server's clock via the response `Date` header and the token's
  `exp` claim, which removes client/server clock skew from the calculation.
- Concurrent callers share a single refresh instead of each triggering one.

**Errors**

- A typed exception per entry in the gateway's error catalog, selected by the `type` suffix.
  Unrecognized suffixes fall back by status rather than raising, since the catalog will grow.
- The token endpoint's RFC 6749 errors are a separate branch of the hierarchy: `except APIError`
  cannot accidentally swallow an authentication failure.
- A `403` is diagnosed against the scopes the token actually holds, naming the missing scope.

**Resilience**

- Retries only where the platform reports that no generation happened. `502 upstream-error` is
  opt-in, since the request may have reached a model and this API has no idempotency mechanism.
- Client-side timeouts are never retried.
- A server-supplied `Retry-After` is honored but capped by `max_backoff`; a longer wait is
  surfaced to the caller rather than slept through inside one call.
- Cooldowns are recorded only from waits the platform supplied, never inferred locally.

**Streaming**

- Mid-stream failures are detected in-band, since the `200` and headers are already committed by
  the time a backend fails. Partial output is preserved on the raised error.
- Token counts are reconstructed from the final chunk's `timings` when a backend sends no `usage`
  chunk, and flagged `estimated` so a derived figure is never mistaken for a reported one.

**Observability**

- Correlation IDs and rate-limit budget on every response, successes included.
- Structured logging under the `axonium` logger with a `NullHandler`. Prompts, completions and
  credentials are never logged and there is no flag to enable it.
- OpenTelemetry spans behind the `axonium[otel]` extra, off by default. Trace context is not
  propagated outbound, because the gateway does not read it.

**Removed from the legacy SDK**

- Langfuse coupling and the `llm-guard` PII masking layer. Vendor observability belongs to the
  platform; the masking layer was also heavyweight, English-only, and provably broken.
- LangChain and LangGraph bridges, and the `MiniAgent` / `LLMRunnable` workflow abstractions —
  out of scope for an API client.
- Spanish-heuristic response normalizers, which were model-output-shape hacks.

## Go

Not yet released. See [`go/`](go/).

## Rust

Not yet released. See [`rust/`](rust/).

---

## Legacy (pre-rewrite, single-package Python SDK)

Versions `v0.1.0` through `v0.6.0` targeted the first-generation Prometheus platform and are
documented in the [v0.6.0 release notes](https://github.com/Root1V/axonium-sdk/releases/tag/v0.6.0).
That code is preserved at tag `v0.6.0`.
