# Roadmap

Backlog catalog. One line per item — details live in the code, the commits and `spec/`.

**Status:** ✅ done · 🚧 in progress · 📋 planned · 🚫 discarded · ⛔ blocked

| ID | Item | Status | Notes |
|---|---|---|---|
| RM-01 | Freeze the legacy SDK at `v0.6.0` | ✅ | Tag + GitHub release |
| RM-02 | Monorepo restructure (`spec/`, `python/`, `go/`, `rust/`, CI) | ✅ | Legacy removed from `main` |
| RM-03 | Configuration layer, zero hardcoded network defaults | ✅ | Resolves at construction, not import |
| RM-04 | Typed error taxonomy + shared `spec/errors.json` | ✅ | OAuth errors kept a separate branch |
| RM-05 | OAuth2 `client_credentials` with refresh-ahead tokens | ✅ | Expiry anchored to the server `Date` header |
| RM-06 | HTTP transport, sync + async, TLS trust config | ✅ | Timeouts per §4 |
| RM-07 | Model catalog: `models.list()` / `models.mine()` | ✅ | `list()` skips auth |
| RM-08 | Chat completions, non-streaming | ✅ | |
| RM-09 | Embeddings | ✅ | |
| RM-10 | Image generation | ✅ | Base64 decode + `save()` |
| RM-11 | Request allowlist with dropped-field warnings | ✅ | Rejects remote image URLs client-side |
| RM-12 | Retry policy + `Retry-After` cooldown registry | ✅ | Never retries what may have reached a model |
| RM-13 | SSE streaming with in-band failure detection | ✅ | Detects by key presence, keeps partial output |
| RM-14 | Usage derived from `timings` when no usage chunk | ✅ | Flagged `estimated` |
| RM-15 | Structured logging | ✅ | Never logs prompts, outputs or credentials |
| RM-16 | OpenTelemetry spans behind `axonium[otel]` | ✅ | Off by default, no outbound propagation |
| RM-17 | Scope diagnostics on `403` | ✅ | Names the missing scope |
| RM-18 | Cross-language contract manifest + fixtures | ✅ | Go/Rust will replay the same files |
| RM-19 | `spec-lint` CI for the shared contract | ✅ | Guards SSE wire framing |
| RM-20 | Runnable examples | ✅ | Verified against a mocked gateway |
| RM-21 | Release workflow via PyPI trusted publishing | ✅ | Checks tag matches packaged version |
| RM-22 | Single-source package version | ✅ | Prevents the drift the legacy SDK had |
| RM-23 | PyPI + TestPyPI pending publishers, GitHub environments | ⛔ | Owner action; needs the PyPI/GitHub accounts |
| RM-24 | TestPyPI dry run | 📋 | After RM-23 |
| RM-25 | Publish `python/v1.0.0rc1` | 📋 | After RM-24; a PyPI version cannot be reused |
| RM-26 | Integration tests against a real deployment | ⛔ | Needs §9 base URLs, TLS chain and test credentials |
| RM-27 | Go SDK | 📋 | Contract already defined by RM-18 |
| RM-28 | Rust SDK | 📋 | Contract already defined by RM-18 |
| RM-29 | Optional caller-supplied `X-Trace-ID` | 📋 | Only adopted in legacy-mode deployments, and only as UUID4 |
| RM-30 | Error-catalog cases in the contract manifest | 📋 | Today asserted against `errors.json` instead |
| RM-31 | Refresh GitHub Actions off deprecated Node 20 | 📋 | `checkout@v4`, `setup-uv@v5` |
| RM-32 | Langfuse integration | 🚫 | Vendor observability belongs to the platform |
| RM-33 | `llm-guard` PII masking | 🚫 | Heavyweight, English-only, and was broken |
| RM-34 | LangChain / LangGraph bridges | 🚫 | Out of scope for an API client |
| RM-35 | `MiniAgent` / `LLMRunnable` workflow abstractions | 🚫 | May return later as a separate package |
| RM-36 | Response normalizers | 🚫 | Spanish-heuristic hacks tied to one backend's output |
| RM-37 | Client-side circuit breaker | 🚫 | The gateway runs one with better information |
| RM-38 | `v0.6` compatibility layer | 🚫 | No production consumers to migrate |
