# Roadmap

Backlog catalog. One line per item — details live in the code, the commits and `spec/`.

**Status:** ✅ done · 🚧 in progress · 📋 planned · 🚫 discarded · ⛔ blocked

**Codes:** `AXO-xx`, renamed from `RM-xx` on 2026-09-09 by tri-party agreement — one to one, the number is unchanged, so an older `RM-19` reference resolves as `AXO-19`. Cross-project references carry the owning prefix.

| ID | Item | Status | Notes |
|---|---|---|---|
| AXO-01 | Freeze the legacy SDK at `v0.6.0` | ✅ | Tag + GitHub release |
| AXO-02 | Monorepo restructure (`spec/`, `python/`, `go/`, `rust/`, CI) | ✅ | Legacy removed from `main` |
| AXO-03 | Configuration layer, zero hardcoded network defaults | ✅ | Resolves at construction, not import |
| AXO-04 | Typed error taxonomy + shared `spec/errors.json` | ✅ | OAuth errors kept a separate branch |
| AXO-05 | OAuth2 `client_credentials` with refresh-ahead tokens | ✅ | Expiry anchored to the server `Date` header |
| AXO-06 | HTTP transport, sync + async, TLS trust config | ✅ | Timeouts per §4 |
| AXO-07 | Model catalog: `models.list()` / `models.mine()` | ✅ | `list()` skips auth |
| AXO-08 | Chat completions, non-streaming | ✅ | |
| AXO-09 | Embeddings | ✅ | |
| AXO-10 | Image generation | ✅ | Base64 decode + `save()` |
| AXO-11 | Request allowlist with dropped-field warnings | ✅ | Rejects remote image URLs client-side |
| AXO-12 | Retry policy + `Retry-After` cooldown registry | ✅ | Never retries what may have reached a model |
| AXO-13 | SSE streaming with in-band failure detection | ✅ | Detects by key presence, keeps partial output |
| AXO-14 | Usage derived from `timings` when no usage chunk | ✅ | Flagged `estimated` |
| AXO-15 | Structured logging | ✅ | Never logs prompts, outputs or credentials |
| AXO-16 | OpenTelemetry spans behind `axonium[otel]` | ✅ | Off by default, no outbound propagation |
| AXO-17 | Scope diagnostics on `403` | ✅ | Names the missing scope |
| AXO-18 | Cross-language contract manifest + fixtures | ✅ | Go/Rust will replay the same files |
| AXO-19 | `spec-lint` CI for the shared contract | ✅ | Guards SSE wire framing |
| AXO-20 | Runnable examples | ✅ | Verified against a mocked gateway |
| AXO-21 | Release workflow via PyPI trusted publishing | ✅ | Checks tag matches packaged version |
| AXO-22 | Single-source package version | ✅ | Prevents the drift the legacy SDK had |
| AXO-23 | PyPI + TestPyPI pending publishers, GitHub environments | ⛔ | Owner action; needs the PyPI/GitHub accounts |
| AXO-24 | TestPyPI dry run | 📋 | After AXO-23 |
| AXO-25 | Publish `python/v1.0.0rc1` | 📋 | After AXO-24; a PyPI version cannot be reused |
| AXO-26 | Integration tests against a real deployment | ✅ | Validated by hand against a local gateway; not yet automated |
| AXO-27 | Go SDK | 📋 | Contract already defined by AXO-18 |
| AXO-28 | Rust SDK | 📋 | Contract already defined by AXO-18 |
| AXO-29 | Optional caller-supplied `X-Trace-ID` | 📋 | Only adopted in legacy-mode deployments, and only as UUID4 |
| AXO-30 | Error-catalog cases in the contract manifest | 📋 | Today asserted against `errors.json` instead |
| AXO-31 | Refresh GitHub Actions off deprecated Node 20 | 📋 | `checkout@v4`, `setup-uv@v5` |
| AXO-32 | Langfuse integration | 🚫 | Vendor observability belongs to the platform |
| AXO-33 | `llm-guard` PII masking | 🚫 | Heavyweight, English-only, and was broken |
| AXO-34 | LangChain / LangGraph bridges | 🚫 | Out of scope for an API client |
| AXO-35 | `MiniAgent` / `LLMRunnable` workflow abstractions | 🚫 | May return later as a separate package |
| AXO-36 | Response normalizers | 🚫 | Spanish-heuristic hacks tied to one backend's output |
| AXO-37 | Client-side circuit breaker | 🚫 | The gateway runs one with better information |
| AXO-38 | `v0.6` compatibility layer | 🚫 | No production consumers to migrate |
| AXO-39 | Automate the integration suite behind `AXONIUM_INTEGRATION=1` | ✅ | Platform security probes in `tests/integration/`; kept out of default CI |
| AXO-40 | Client-side modality check before sending | ✅ | Opt-in `verify_modality`; gateway accepts chat on an embedding model and bills for garbage |
| AXO-41 | First-class access to `reasoning_content` | ✅ | `.reasoning` on completions, chunks and streams; kept separate from `.content` |
| AXO-42 | Security suite: credential leaks and hostile responses | ✅ | Found and fixed a secret visible in `repr(config)` |
| AXO-43 | Platform security probes | ✅ | 28/28 pass against a live deployment |
| AXO-44 | Unified error surface: everything raises an `AxoniumError` | ✅ | Request validation no longer leaks pydantic's exception |
| AXO-45 | Ask the platform team about the 422 envelope | 📋 | Not RFC 9457, no request_id, absent from the catalog |
| AXO-46 | Injected token provider as an alternative to client credentials | ✅ | Rejected token passed back, not a flag, so a provider can deduplicate exactly |
| AXO-47 | Re-record contract fixtures from a live deployment | 📋 | Today's are authored, so they pin the SDKs to each other, not to the gateway |
| AXO-48 | Map `Usage` onto the agreed H3 vocabulary | 📋 | Prometheus cannot source `reasoning` or `cache_write`; nullability must be explicit |
| AXO-49 | Two permanent credential modes with construction-time validation | ✅ | Environment credentials are discarded, not merely unused |
