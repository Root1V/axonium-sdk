# Roadmap

Backlog catalog. One line per item — details live in the code, the commits and `spec/`.

**Status:** ✅ done · 🚧 in progress · 📋 planned · 🚫 discarded · ⛔ blocked

**Codes:** `AXO-xx`, renamed from `RM-xx` on 2026-09-09 by tri-party agreement — one to one, the number is unchanged, so an older `RM-19` reference resolves as `AXO-19`. Cross-project references carry the owning prefix.

| ID | Item | Status | Notes |
|---|---|---|---|
| AXO-01 | Freeze the legacy SDK at `v0.6.0` | ✅ | Tag + GitHub release |
| AXO-02 | Monorepo restructure (`spec/`, `python/`, `go/`, `rust/`, CI) | ✅ | Legacy removed from `main` |
| AXO-03 | Configuration layer, zero hardcoded network defaults | ✅ | Superseded by AXO-76: an official SDK should know where the official platform is |
| AXO-76 | Default to the official platform URLs | ✅ | Credentials are the only required setting; provisional until the cloud migration |
| AXO-81 | Pin the cached-token lift in the contract corpus | ✅ | The platform warned we might be reading a field they never send. We were not — all three lift `prompt_tokens_details.cached_tokens` — but nothing in the corpus asserted the lift, and the Go/Rust runners built their view from the raw payload, so every *derived* value was structurally unassertable. Both runners now overlay the decoded value |
| AXO-79 | Re-vendor the integration guide | ✅ | Revision `2026-09-14 · 55c2174`, which now carries a revision line we asked for. Brought two contract changes beyond the slug fix: `422 validation-error` in the envelope (we had already found and handled it live on 09-11) and a broadened `modality-mismatch`, verified live |
| AXO-80 | Consume `GET /v1/usage/{request_id}` when the platform ships it | 📋 | Accepted by the platform as RM-100, no date. Blocked on them storing `request_id` in the usage row. Asked for the token breakdown and for a replay id to resolve to the original's row |
| AXO-77 | Change the defaults when Prometheus moves to its cloud host | 📋 | Three constants, one per SDK. The release must be loud: a pinned version keeps the old address |
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
| AXO-23 | PyPI + TestPyPI pending publishers, GitHub environments | ✅ | Trusted Publishing on both; no API token stored anywhere |
| AXO-24 | TestPyPI dry run | ✅ | `1.0.0rc1` published, installed from the index and run against the live gateway |
| AXO-25 | Publish `python/v1.0.0rc1` | ✅ | Live on PyPI; verified by `pip install axonium` against the running gateway |
| AXO-78 | Publish `python/v1.0.0rc3` and `go/v0.2.0` | ✅ | Both live and verified from their public indexes with no URLs configured |
| AXO-26 | Integration tests against a real deployment | ✅ | Validated by hand against a local gateway; not yet automated |
| AXO-27 | Go SDK | ✅ | v0.2.0 published; 25/25 contract cases, 94% coverage, zero dependencies |
| AXO-28 | Rust SDK | ✅ | 0.2.0 published to crates.io via Trusted Publishing; 25/25 contract cases, structured logging and a tracing hook |
| AXO-74 | Rust: structured logging and a tracing hook | ✅ | Behind a `tracing` feature, the Cargo equivalent of Python's `[otel]` extra |
| AXO-75 | Publish the Rust crate to crates.io | ✅ | `0.1.0` published with a one-shot token; Trusted Publishing configured after |
| AXO-73 | Ask for a per-request usage lookup without `admin:read` | ✅ | Asked in the channel 2026-09-13, with the concrete shape (`GET /v1/usage/{request_id}`, own rows only). Theirs to answer now, not ours to build |
| AXO-29 | Optional caller-supplied `X-Trace-ID` | 🚫 | Measured: the official platform is in OTEL mode and discards it. A parameter that does nothing is worse than none |
| AXO-30 | Error-catalog cases in the contract manifest | ✅ | 4 recorded failures, asserting suffix, retryability and correlation IDs |
| AXO-31 | Refresh GitHub Actions off deprecated Node 20 | ✅ | Zero deprecation warnings across all four workflows, verified by reading their logs |
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
| AXO-45 | Ask the platform team about the 422 envelope | ✅ | Fixed on the platform: full RFC 9457 with correlation IDs, now catalogued |
| AXO-46 | Injected token provider as an alternative to client credentials | ✅ | Rejected token passed back, not a flag, so a provider can deduplicate exactly |
| AXO-47 | Re-record contract fixtures from a live deployment | ✅ | Superseded by AXO-60, which re-recorded against the slug catalog |
| AXO-48 | Map `Usage` onto the agreed H3 vocabulary | ✅ | `cache_read` exposed as a subset of input; `reasoning`/`cache_write` stay null, unsourceable |
| AXO-49 | Two permanent credential modes with construction-time validation | ✅ | Environment credentials are discarded, not merely unused |
| AXO-50 | Go: raise coverage to the Python bar | ✅ | 79% -> 94%, and the new tests found a real divergence from Python |
| AXO-51 | Go: structured logging and optional OTel spans | ✅ | `log/slog` plus a Tracer interface, so zero dependencies stay zero |
| AXO-52 | Tag and publish `go/v0.1.0` | ✅ | Live on the module proxy; verified by installing it and running against the gateway |
| AXO-53 | Go cannot express an absent `content` | 🚫 | Closed: the real gateway sends "", never null. The divergence was a fixture I invented |
| AXO-72 | Go: expose `TokenClaims()` | ✅ | Was dead code; Python exposed it and Go did not |
| AXO-54 | `response.model` is not `request.model` | ✅ | Was a platform bug; fixed. An alias request now answers with the canonical slug |
| AXO-55 | Type tool calls like the rest of the message | ✅ | `ToolCall` in all three, used by streaming and non-streaming alike, and accepted back on the request side so a tool-use loop needs no conversion in either direction |
| AXO-56 | Reassemble streamed tool calls | ✅ | All three, keyed by `index`, into the exact non-streaming shape. Recorded a live two-call stream because the single-call recording could not tell the correlation key apart from anything else |
| AXO-57 | Expose `X-Prometheus-Instance*` on `ResponseMeta` | ✅ | Both SDKs; key on the id, the `#N` label can be reused |
| AXO-58 | Catalogue and map `400 unknown-instance` | ✅ | In `spec/errors.json`, mapped in both SDKs |
| AXO-59 | Per-call instance pinning | ✅ | Header-based, kept across retries as the platform confirmed |
| AXO-62 | Revisit retry and cooldown for multi-instance models | ✅ | Answered: cooldown by model, no mid-stream failover, pin kept. Go was keyed by gateway; fixed |
| AXO-63 | Go: model the catalog fields and keep per-model `Raw` | ✅ | `context_length`/`served_by` as pointers; `Raw` was never populated per model |
| AXO-64 | `RemoteProtocolError` can double-bill | 🚫 | Withdrawn: the platform corrected us — usage is recorded per response returned, never per attempt |
| AXO-65 | `Idempotency-Key` support | ✅ | Makes a keyed client-timeout retryable, the one case that did double-bill |
| AXO-66 | Ask for a machine-readable 409 discriminator | ✅ | Answered with four distinct types; `idempotency-conflict` retired |
| AXO-68 | Idempotency on streaming | ✅ | The platform reversed its refusal; the key now replays a stream it finished |
| AXO-69 | Verify the four idempotency types against a deployment | ✅ | All four recorded; the envelopes matched what we had implemented blind |
| AXO-70 | Tell the fronts that streaming billing changed | ✅ | All streaming was unbilled, not just broken streams; announced with the mechanism |
| AXO-71 | Streamed replay is not reproducible | ✅ | Fixed upstream: 6/6 now, with no wait. It was the visible end of unbilled streaming |
| AXO-67 | Validate idempotency key length client-side | ✅ | 255 max; the gateway reports over-length as a *conflict*, which misdirects |
| AXO-60 | Re-record fixtures against the slug catalog | ✅ | 16 of 20 recorded; the other 4 say why they cannot be produced here |
| AXO-61 | Migrate examples and docs to model slugs | ✅ | Not tidiness after all: the documented name was not registered, so every README quickstart failed with `400 unknown-model` on copy-paste. Verified against the deployment before and after |
