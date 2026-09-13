# Changelog

Each language SDK versions independently. Entries are grouped by language and use tags of the
form `python/vX.Y.Z`, `go/vX.Y.Z`, `rust/vX.Y.Z`.

## Python

### 1.0.0rc3

`rc2` reached TestPyPI missing six error exports and was replaced rather than patched, since a
version is never reusable. Everything learned from running `rc1` against a live deployment and from three rounds of
coordination with the platform team. No breaking change to code written against `rc1`; one
behaviour change worth reading.

**Credentials are now the only required setting**

- `auth_base_url` and `gateway_base_url` default to the official Prometheus platform. Precedence is
  unchanged — explicit argument, then environment, then the default — and overriding one does not
  force restating the other.
- **The default addresses are provisional.** The platform has not moved to its cloud host yet, so
  they currently point at a local deployment. Upgrading will pick up the new address automatically;
  **a pinned version will not**, and the release that changes them will say so prominently.

**Idempotency**

- `idempotency_key` on `chat.completions.create()`, `.stream()`, `embeddings.create()` and
  `images.generate()`. A repeat with the same key and body returns the stored result without
  reaching a model, recording usage, or counting against the spend cap.
- **A client-side timeout is now retried — but only under a key.** Without one the old rule stands:
  the backend is probably still generating, so a retry would be a second billable generation.
- Four typed refusals, only one of them retryable: `InvalidIdempotencyKeyError`,
  `IdempotencyKeyReuseError`, `IdempotencyInProgressError` (retryable, carries `retry_after`) and
  `IdempotencyResponseNotRetainedError` — which is proof the original succeeded.
- Key length is checked before the wire, so an over-long key names its own problem.

**Multi-instance deployments**

- `instance` pins a call to one replica, by label (`"#2"`) or full id, and is kept across retries.
  It opts out of load balancing *and* failover, so it is for reproducing a problem rather than for
  normal traffic.
- `meta.instance` and `meta.instance_id` on every response — the values to quote when reporting a
  slow or odd one.
- `UnknownInstanceError` for a pin that names something not serving the model.

**Usage and correlation**

- `usage.cache_read_tokens`: how much of the input came from cache, read from the reported
  `prompt_tokens_details` where the gateway supplies it and derived from `timings` otherwise.
  `input` **includes** the cached prefix, which is the convention the three fronts settled on.
- `meta.idempotent_replay` says whether a response was replayed rather than generated — so a
  `usage` on a replay is not added to a running total by mistake.
- `ValidationError` for `422`, which now arrives in the same problem-details envelope as every
  other error.

**Fixed**

- Six error classes added after `rc1` were exported from `axonium.errors` but not from the package
  itself, so `from axonium import IdempotencyInProgressError` failed. All of them are importable
  from the package now, and a test pins the invariant -- nothing failed in CI before, because every
  test imported from the submodule.
- `cache_n` present with a null value was reported as a measured zero rather than as unmeasured,
  making an unknown cache indistinguishable from a cold one.

### 1.0.0rc1

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

### 0.2.0

- **Credentials are the only required setting.** `AuthBaseURL` and `GatewayBaseURL` default to the
  official Prometheus platform, with the same precedence as everywhere else. **The defaults are
  provisional** until the platform moves to its cloud host; a pinned version will keep the old
  address after it moves.
- Contract corpus re-recorded after the platform fixed a defect that left streaming generations
  unbilled. Streams now carry one terminal frame rather than two.

### 0.1.0

First release. Streaming with cancellation that reaches Prometheus — measured by counting the
chunks the upstream produced after the client went away, not asserted. Both credential modes,
idempotency keys, instance pinning, the full error taxonomy from `spec/errors.json`, structured
logging through `log/slog`, and a tracing hook that is an interface rather than an OpenTelemetry
dependency. All 24 shared contract cases replay the same recorded wire bytes as the Python SDK.

No third-party dependencies: standard library only.

## Rust

### 0.1.0 — unreleased

Core implemented: chat, streaming with cancellation on drop, embeddings, images, both credential
modes, idempotency keys, instance pinning and the full error taxonomy. All 24 shared contract cases
pass. Structured logging, a tracing hook and publication to crates.io remain.

`Config` and `Client` redact the client secret from `Debug`, which a derived implementation printed
in full.

---

## Legacy (pre-rewrite, single-package Python SDK)

Versions `v0.1.0` through `v0.6.0` targeted the first-generation Prometheus platform and are
documented in the [v0.6.0 release notes](https://github.com/Root1V/axonium-sdk/releases/tag/v0.6.0).
That code is preserved at tag `v0.6.0`.
