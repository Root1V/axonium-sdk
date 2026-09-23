# Changelog

Each language SDK versions independently. Entries are grouped by language and use tags of the
form `python/vX.Y.Z`, `go/vX.Y.Z`, `rust/vX.Y.Z`.

## Python

### Unreleased

Documentation only, and it matters because it had become wrong.

The rate-limit envelope no longer omits `trace_id`, and `X-RateLimit-Scope` now reaches the `429`
as well (platform guide `2026-09-19b`). Four statements in this SDK still described the old
behaviour. They now describe the current one, and say which deployments still behave the old way.

The body fallback for `scope` stays. It is no longer needed against a current deployment and is
kept for one predating the fix — which the platform team explicitly recommended.

Also documented: what happens to an `Idempotency-Key` whose request *failed* is undefined, is the
gateway's decision rather than this SDK's, and is being asked.

### 1.0.0rc5 — 2026-09-20

No change to the public surface beyond the additions above. Verified rather than asserted: the
release rehearsal now compares this tree against the wheel PyPI actually served, and reports every
symbol and field that was added, removed, or changed type.

`1.0.0rc5` is additive — three new fields, nothing removed, **nothing re-typed**. That last part is
the one that matters to a consumer reading through a tolerant accessor, since a type change there
neither fails nor warns.

`RateLimitSnapshot.scope`, and `client.rate_limits` keyed by it.

The platform gave `/v1/embeddings`, `/v1/rerank` and `/v1/chat/completions` separate rate-limit
budgets. That made `client.last_rate_limit` a number from whichever endpoint answered last, with
nothing in the numbers saying so — a dashboard drawing "requests remaining" kept drawing a
plausible figure from another bucket. Ask about a particular budget by scope instead.

Measured against a deployment rather than taken from the announcement: `X-RateLimit-Scope` is on
successful responses and **absent on the 429**, where the body carries `"scope"` instead. So the
one response whose budget most needs attributing — the one telling you a bucket is exhausted — is
read from the body when the header is missing. The header wins when both are present.

A retried call can now explain its own duration without anyone reading a log.

A `Retry-After` of 0–60s, respected as it should be, looks from outside like one slow call among
fast ones. Three separate teams have reported that as a hang. `1.0.0rc4` began logging the wait at
INFO, which was necessary and not sufficient: this SDK does not configure the host application's
logging, so the line is invisible until somebody opts in — at every entry point, and again at the
next one added. A latency metric cannot read a log line at all, and a latency metric is where this
keeps being seen.

`meta.waited_s` and `meta.attempts` on every response now carry it as data.

Subtract it from a wall-clock reading to get what the platform actually spent: the wait is
deliberately excluded from every duration this SDK reports, because sleeping is not service time.

Not covered: a call that waited and then failed anyway, which is the one whose duration most needs
explaining. Errors carry no response metadata today.

### 1.0.0rc4 — 2026-09-16

Five error types the platform ships and nobody had mapped: `inconsistent-model-group`,
`unauthorized`, `invalid-date`, `invalid-range` and `range-too-large`.

They surfaced from the other side of a gap we reported. We told the platform that five suffixes
they send appear nowhere in their guide; they wrote a test comparing every `type` the gateway
raises against that table, and it found ten. Five were ours, and these five had been there longer
and nobody had noticed.

Three of them belong to the admin-scoped usage export, which this SDK does not call. They are
mapped anyway so the taxonomy is complete and an unknown suffix means what it says.

`POST /v1/rerank`, for models with `rerank` modality.

A reranker is a cross-encoder: it scores a query against each document and returns them ordered. It
generates nothing, so there are no completion tokens and billing is prompt-only.

Two things matter if you were doing this through the chat endpoint. The **whole document set is one
request**, not one per document — against a 60 RPM budget, scoring 50 candidates costs 1 unit rather
than 50. And each result's `index` points into the `documents` you sent, never into the results, so
a reordered result stays attributable to its input.

- `client.rerank.create(...)` (and the async mirror), returning `RerankResponse`, whose `.ranking` gives the input indices best-first. An empty `documents` list is refused before the wire, which the gateway would answer
  `400 validation-error`.

The SDK now says when it is waiting, and for how long.

The platform's `Retry-After` on a `429` is seconds until the window resets, so it runs 0–60. An SDK
that respects it — as it should — looks from outside like one slow call among fast ones, and that
arrives as a latency bug report. A wait long enough for a person to notice is now reported at INFO
with `delay_s`; sub-second backoff stays at DEBUG, because the noise worry is frequent small retries
rather than the rare long one.

The wait is reported as a wait, never folded into `duration_ms` — that is measured per attempt and
deliberately excludes time spent sleeping. Time waiting is not time the gateway took.

A token response in neither documented envelope is now a transport failure rather than an OAuth2
one.

A `4xx` carrying `error` is an OAuth2 outcome; a `5xx` carrying `type` is the gateway's own
problem+json. A body with neither — an HTML error page from a proxy or load balancer that answered
instead of the gateway — used to be reported as an OAuth2 failure, which tells a caller their
credentials are the problem. That is both wrong and the most expensive wrong answer available here:
the obvious next step is rotating a perfectly good secret.

`spec/errors.json` and this SDK's error mapping are now held together by a test (`tests/test_catalog_parity.py`): every
catalogued error must map to a class, and its retryability must match the catalog.

Go has had this from the start and it earned its keep the day the platform added two token errors —
it refused the change until both had a mapping, then refused again until their retryability matched.
This SDK had no equivalent, and shipped one of them with the wrong retryability until a hand-written
test caught it.

A failed token request is now typed by its envelope rather than read as OAuth2 unconditionally.

A `4xx` is an OAuth2 outcome in the RFC 6749 shape — wrong credentials, a scope the client does not
hold — and is never worth retrying. A `5xx` is the gateway failing to reach the auth-service,
arrives as problem+json, and `upstream-unavailable` **is** worth retrying. Reading both as OAuth2
left the 5xx with no type and no retryability, so a momentary blip looked exactly like bad
credentials and the request was abandoned rather than retried.

`not-configured` shares that status and is deliberately *not* retryable, which is why the suffix
drives the decision rather than the status.

**One address, not two.** The gateway serves both the inference API and `/oauth2/token`, so the
auth-service address is **gone from this SDK** rather than defaulted.

Keeping it as a field that defaults to the gateway would still have taught every consumer that a
second address exists. It does not, for them: the platform's auth-service is now reachable only
from inside the deployment, which is what it was always for. Removing the field removes the failure
it enabled — pointing the SDK at a self-hosted deployment used to mean changing two addresses, and
forgetting the second left the client asking the **official platform** for a token to use somewhere
else, with nothing erroring.

**Breaking**, and deliberately so while the surface is pre-1.0: `auth_base_url` and `DEFAULT_AUTH_BASE_URL` are **removed**, along with `AXONIUM_AUTH_BASE_URL`. A deployment whose gateway
has no token endpoint wired up answers `not-configured`, which says exactly that rather than
failing obscurely.

Per-request usage lookup: what one of your own requests was charged, and why it stopped.

Both aggregate usage endpoints require `admin:read`, which a normal client neither has nor should
have — so `termination_reason` existed for callers who could not read it. The platform shipped this
after we made that case; this is the client half.

A replay has its own request id and no row of its own, so looking that id up is a `not-found` —
correctly, since a replay is not billed. `meta.idempotent_replay_of` names the generation that was
charged; look *that* up. The round trip is verified live in all three languages.

`termination_reason` is a plain string, not an enum. The platform proposed a fourth value this week
and withdrew it; the next one may not be withdrawn, and a closed set would turn a new value into a
parse failure for a caller who only wanted the token counts.

- `client.usage.retrieve(request_id)` (and the async mirror), returning `RequestUsage`. `NotFoundError` is new.

`meta.idempotent_replay_of` carries, on a replay, the request id of the generation that was
actually billed.

A replay has its own request id and no usage row of its own, so looking that id up returns `404` —
correctly, since replaying reaches no model and is not billed. This header names the id that does
resolve, which makes it the only path from the response a caller received to the charge it
corresponds to. `None`/empty on anything that is not a replay.

The quickstart in the README used a model name that is not registered, so copying it produced
`400 unknown-model` rather than a completion. Examples and doc comments now use a real slug, and
each README says what a slug is: it never changes and is never reused, so pinning one is safe, but
which ones exist depends on the deployment and on what the token is granted — the catalog endpoint
is the source of truth, not the README.

Tool calls are now typed like everything around them.

They used to arrive as raw dicts while the message carrying them was a model. The asymmetry cost a
consumer real work twice over: reaching in by hand to read a name, and converting back to dicts to
feed a call into the next request. Both directions are now the same type.

`arguments` deliberately stays the model's own JSON **string** rather than a decoded object.
Decoding it at parse time would raise from inside a response model, for a caller who only wanted to
see what the model had managed to say — a generation stopped by `max_tokens` leaves a string that
was never going to parse. Decoding is a separate, explicit call that fails loudly, and the raw
string stays reachable either way.

**Breaking**, and deliberately so while the surface is still pre-1.0: anything indexing a tool call
as a dict/map/`Value` needs the field instead.

- `ToolCall` and `FunctionCall`, returned by `completion.tool_calls` and `stream.tool_calls`, and
  accepted by `Message.tool_calls` on the request side. Plain dicts are still validated into the
  model there, so request-building code written before this keeps working.
- `call.parse_arguments()` decodes the arguments, raising `ToolCallArgumentsError` — which carries
  the offending call — rather than letting `json`'s own `ValueError` escape. `call.name` reads the
  function name without reaching through `call.function`.
- Streamed *fragments* stay raw dicts on `chunk.tool_call_fragments`. A fragment is not a call: it
  carries `index`, which the complete shape has no field for, and only a slice of the arguments.

Streamed tool calls are now reassembled for you.

A tool call arrives split across as many deltas as it takes — `{`, `"`, `city` — and the fragments
are individually invalid JSON. Only the first carries the identity, and **`index` is the
correlation key**, because `id` never repeats. Every consumer was writing that join by hand.

The assembled call is **byte-for-byte the shape a non-streaming completion returns**, `arguments`
included: still a JSON *string*, not a decoded object. That is deliberate — the same caller code
handles both, and a stream cut short by `max_tokens` hands back the fragment that did arrive
instead of raising or dropping the call. Check `finish_reason` before decoding.

A second contract case was recorded live for this: a single-call recording cannot tell `index`
correlation apart from any other strategy, so a stream with two concurrent calls was recorded to
give the case teeth. The manifest is now 25 cases, and all three SDKs replay both.

- `stream.tool_calls` on `ChatCompletionStream` and `AsyncChatCompletionStream`, populated as the
  stream runs and complete once it ends.
- `chunk.tool_call_fragments` exposes the raw fragments for a caller who wants to watch them
  arrive. They remain unusable on their own; this is not the accessor to reach for.

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

### Unreleased

Documentation only, and it matters because it had become wrong.

The rate-limit envelope no longer omits `trace_id`, and `X-RateLimit-Scope` now reaches the `429`
as well (platform guide `2026-09-19b`). Four statements in this SDK still described the old
behaviour. They now describe the current one, and say which deployments still behave the old way.

The body fallback for `scope` stays. It is no longer needed against a current deployment and is
kept for one predating the fix — which the platform team explicitly recommended.

Also documented: what happens to an `Idempotency-Key` whose request *failed* is undefined, is the
gateway's decision rather than this SDK's, and is being asked.

### 0.4.0 — 2026-09-20

`Version` reported `0.2.0` from the module published as `v0.3.0`.

It feeds the `User-Agent`, so every request this SDK made identified itself as `axonium-go/0.2.0`
to the platform — a field used to correlate client versions during an incident. Nothing failed; the
answer was simply wrong.

Python and Rust check the tag against the packaged version inside the job that publishes, and
refuse to publish on a mismatch. Go has no such job: pushing the tag *is* the release. So the check
now runs on every push instead, and refuses a constant that has fallen behind the newest `go/v*`
tag — which is the state that produced this.

`RateLimitSnapshot.Scope`, and `Client.RateLimits()` keyed by it.

The platform gave `/v1/embeddings`, `/v1/rerank` and `/v1/chat/completions` separate rate-limit
budgets. That made `Client.LastRateLimit()` a number from whichever endpoint answered last, with
nothing in the numbers saying so — a dashboard drawing "requests remaining" kept drawing a
plausible figure from another bucket. Ask about a particular budget by scope instead.

Measured against a deployment rather than taken from the announcement: `X-RateLimit-Scope` is on
successful responses and **absent on the 429**, where the body carries `"scope"` instead. So the
one response whose budget most needs attributing — the one telling you a bucket is exhausted — is
read from the body when the header is missing. The header wins when both are present.

A retried call can now explain its own duration without anyone reading a log.

A `Retry-After` of 0–60s, respected as it should be, looks from outside like one slow call among
fast ones. Three separate teams have reported that as a hang. `v0.3.0` began logging the wait at
INFO, which was necessary and not sufficient: this SDK does not configure the host application's
logging, so the line is invisible until somebody opts in — at every entry point, and again at the
next one added. A latency metric cannot read a log line at all, and a latency metric is where this
keeps being seen.

`Meta.WaitedFor` and `Meta.Attempts` on every response now carry it as data.

Subtract it from a wall-clock reading to get what the platform actually spent: the wait is
deliberately excluded from every duration this SDK reports, because sleeping is not service time.

Not covered: a call that waited and then failed anyway, which is the one whose duration most needs
explaining. Errors carry no response metadata today.

### 0.3.0 — 2026-09-16

Five error types the platform ships and nobody had mapped: `inconsistent-model-group`,
`unauthorized`, `invalid-date`, `invalid-range` and `range-too-large`.

They surfaced from the other side of a gap we reported. We told the platform that five suffixes
they send appear nowhere in their guide; they wrote a test comparing every `type` the gateway
raises against that table, and it found ten. Five were ours, and these five had been there longer
and nobody had noticed.

Three of them belong to the admin-scoped usage export, which this SDK does not call. They are
mapped anyway so the taxonomy is complete and an unknown suffix means what it says.

`POST /v1/rerank`, for models with `rerank` modality.

A reranker is a cross-encoder: it scores a query against each document and returns them ordered. It
generates nothing, so there are no completion tokens and billing is prompt-only.

Two things matter if you were doing this through the chat endpoint. The **whole document set is one
request**, not one per document — against a 60 RPM budget, scoring 50 candidates costs 1 unit rather
than 50. And each result's `index` points into the `documents` you sent, never into the results, so
a reordered result stays attributable to its input.

- `client.Rerank.Create(ctx, RerankRequest)`, returning `*RerankResponse`, whose `Ranking()` gives the input indices best-first. An empty `documents` list is refused before the wire, which the gateway would answer
  `400 validation-error`.

The SDK now says when it is waiting, and for how long.

The platform's `Retry-After` on a `429` is seconds until the window resets, so it runs 0–60. An SDK
that respects it — as it should — looks from outside like one slow call among fast ones, and that
arrives as a latency bug report. A wait long enough for a person to notice is now reported at INFO
with `delay_s`; sub-second backoff stays at DEBUG, because the noise worry is frequent small retries
rather than the rare long one.

The wait is reported as a wait, never folded into `duration_ms` — that is measured per attempt and
deliberately excludes time spent sleeping. Time waiting is not time the gateway took.

A token response in neither documented envelope is now a transport failure rather than an OAuth2
one.

A `4xx` carrying `error` is an OAuth2 outcome; a `5xx` carrying `type` is the gateway's own
problem+json. A body with neither — an HTML error page from a proxy or load balancer that answered
instead of the gateway — used to be reported as an OAuth2 failure, which tells a caller their
credentials are the problem. That is both wrong and the most expensive wrong answer available here:
the obvious next step is rotating a perfectly good secret.

A failed token request is now typed by its envelope rather than read as OAuth2 unconditionally.

A `4xx` is an OAuth2 outcome in the RFC 6749 shape — wrong credentials, a scope the client does not
hold — and is never worth retrying. A `5xx` is the gateway failing to reach the auth-service,
arrives as problem+json, and `upstream-unavailable` **is** worth retrying. Reading both as OAuth2
left the 5xx with no type and no retryability, so a momentary blip looked exactly like bad
credentials and the request was abandoned rather than retried.

`not-configured` shares that status and is deliberately *not* retryable, which is why the suffix
drives the decision rather than the status.

**One address, not two.** The gateway serves both the inference API and `/oauth2/token`, so the
auth-service address is **gone from this SDK** rather than defaulted.

Keeping it as a field that defaults to the gateway would still have taught every consumer that a
second address exists. It does not, for them: the platform's auth-service is now reachable only
from inside the deployment, which is what it was always for. Removing the field removes the failure
it enabled — pointing the SDK at a self-hosted deployment used to mean changing two addresses, and
forgetting the second left the client asking the **official platform** for a token to use somewhere
else, with nothing erroring.

**Breaking**, and deliberately so while the surface is pre-1.0: `Config.AuthBaseURL` and `DefaultAuthBaseURL` are **removed**, along with `AXONIUM_AUTH_BASE_URL`. A deployment whose gateway
has no token endpoint wired up answers `not-configured`, which says exactly that rather than
failing obscurely.

Per-request usage lookup: what one of your own requests was charged, and why it stopped.

Both aggregate usage endpoints require `admin:read`, which a normal client neither has nor should
have — so `termination_reason` existed for callers who could not read it. The platform shipped this
after we made that case; this is the client half.

A replay has its own request id and no row of its own, so looking that id up is a `not-found` —
correctly, since a replay is not billed. `meta.idempotent_replay_of` names the generation that was
charged; look *that* up. The round trip is verified live in all three languages.

`termination_reason` is a plain string, not an enum. The platform proposed a fourth value this week
and withdrew it; the next one may not be withdrawn, and a closed set would turn a new value into a
parse failure for a caller who only wanted the token counts.

- `client.Usage.Retrieve(ctx, requestID)`, returning `*RequestUsage`. `ErrNotFound` is new.

`ResponseMeta.IdempotentReplayOf` carries, on a replay, the request id of the generation that was
actually billed.

A replay has its own request id and no usage row of its own, so looking that id up returns `404` —
correctly, since replaying reaches no model and is not billed. This header names the id that does
resolve, which makes it the only path from the response a caller received to the charge it
corresponds to. `None`/empty on anything that is not a replay.

The quickstart in the README used a model name that is not registered, so copying it produced
`400 unknown-model` rather than a completion. Examples and doc comments now use a real slug, and
each README says what a slug is: it never changes and is never reused, so pinning one is safe, but
which ones exist depends on the deployment and on what the token is granted — the catalog endpoint
is the source of truth, not the README.

Tool calls are now typed like everything around them.

They used to arrive as raw dicts while the message carrying them was a model. The asymmetry cost a
consumer real work twice over: reaching in by hand to read a name, and converting back to dicts to
feed a call into the next request. Both directions are now the same type.

`arguments` deliberately stays the model's own JSON **string** rather than a decoded object.
Decoding it at parse time would raise from inside a response model, for a caller who only wanted to
see what the model had managed to say — a generation stopped by `max_tokens` leaves a string that
was never going to parse. Decoding is a separate, explicit call that fails loudly, and the raw
string stays reachable either way.

**Breaking**, and deliberately so while the surface is still pre-1.0: anything indexing a tool call
as a dict/map/`Value` needs the field instead.

- `ToolCall` and `FunctionCall`, returned by `(*ChatCompletion).ToolCalls()` and
  `(*ChatCompletionStream).ToolCalls()`, and accepted by `Message.ToolCalls`.
- `call.ParseArguments()` decodes the arguments, returning an error wrapping the new
  `ErrToolCallArguments` sentinel.
- `(*ChatCompletionChunk).ToolCallFragments()` now reads from the chunk's `Raw` rather than the
  decoded delta. `Message` is the same struct for a message and a delta, so decoding a fragment
  into the typed shape would have silently dropped `index` — the one field the reassembler
  correlates on.

Streamed tool calls are now reassembled for you.

A tool call arrives split across as many deltas as it takes — `{`, `"`, `city` — and the fragments
are individually invalid JSON. Only the first carries the identity, and **`index` is the
correlation key**, because `id` never repeats. Every consumer was writing that join by hand.

The assembled call is **byte-for-byte the shape a non-streaming completion returns**, `arguments`
included: still a JSON *string*, not a decoded object. That is deliberate — the same caller code
handles both, and a stream cut short by `max_tokens` hands back the fragment that did arrive
instead of raising or dropping the call. Check `finish_reason` before decoding.

A second contract case was recorded live for this: a single-call recording cannot tell `index`
correlation apart from any other strategy, so a stream with two concurrent calls was recorded to
give the case teeth. The manifest is now 25 cases, and all three SDKs replay both.

- `(*ChatCompletionStream).ToolCalls()`, alongside `Content()` and `Usage()`.
- `(*ChatCompletionChunk).ToolCallFragments()` exposes the raw fragments for a caller who wants to
  watch them arrive. They remain unusable on their own.

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

### Unreleased

Documentation only, and it matters because it had become wrong.

The rate-limit envelope no longer omits `trace_id`, and `X-RateLimit-Scope` now reaches the `429`
as well (platform guide `2026-09-19b`). Four statements in this SDK still described the old
behaviour. They now describe the current one, and say which deployments still behave the old way.

The body fallback for `scope` stays. It is no longer needed against a current deployment and is
kept for one predating the fix — which the platform team explicitly recommended.

Also documented: what happens to an `Idempotency-Key` whose request *failed* is undefined, is the
gateway's decision rather than this SDK's, and is being asked.

### 0.4.0 — 2026-09-20

`Message` gains `tool_call_id` and `name`, and derives `Default`.

Without `tool_call_id` a tool result cannot be matched to the call that asked for it, which means a
tool-use loop could not be closed in this SDK at all: it could read a tool call and had no way to
send the answer back. Python and Go have carried the field since the beginning.

Both fields are omitted from the wire when empty, so an ordinary turn is unchanged.

**Source-breaking, not behaviour-breaking**: a `Message { .. }` literal that names every field now
misses two. Add `..Default::default()`.

`RateLimit::scope`, and `Client::rate_limits()` keyed by it. `ApiError` also gains `rate_limit`, which Python and Go have always carried and this SDK did not.

The platform gave `/v1/embeddings`, `/v1/rerank` and `/v1/chat/completions` separate rate-limit
budgets. That made `Client::last_rate_limit()` a number from whichever endpoint answered last, with
nothing in the numbers saying so — a dashboard drawing "requests remaining" kept drawing a
plausible figure from another bucket. Ask about a particular budget by scope instead.

Measured against a deployment rather than taken from the announcement: `X-RateLimit-Scope` is on
successful responses and **absent on the 429**, where the body carries `"scope"` instead. So the
one response whose budget most needs attributing — the one telling you a bucket is exhausted — is
read from the body when the header is missing. The header wins when both are present.

A retried call can now explain its own duration without anyone reading a log.

A `Retry-After` of 0–60s, respected as it should be, looks from outside like one slow call among
fast ones. Three separate teams have reported that as a hang. `0.3.0` began logging the wait at
INFO, which was necessary and not sufficient: this SDK does not configure the host application's
logging, so the line is invisible until somebody opts in — at every entry point, and again at the
next one added. A latency metric cannot read a log line at all, and a latency metric is where this
keeps being seen.

`meta.waited_for` and `meta.attempts` on every response now carry it as data.

Subtract it from a wall-clock reading to get what the platform actually spent: the wait is
deliberately excluded from every duration this SDK reports, because sleeping is not service time.

Not covered: a call that waited and then failed anyway, which is the one whose duration most needs
explaining. Errors carry no response metadata today.

### 0.3.0 — 2026-09-16

Five error types the platform ships and nobody had mapped: `inconsistent-model-group`,
`unauthorized`, `invalid-date`, `invalid-range` and `range-too-large`.

They surfaced from the other side of a gap we reported. We told the platform that five suffixes
they send appear nowhere in their guide; they wrote a test comparing every `type` the gateway
raises against that table, and it found ten. Five were ours, and these five had been there longer
and nobody had noticed.

Three of them belong to the admin-scoped usage export, which this SDK does not call. They are
mapped anyway so the taxonomy is complete and an unknown suffix means what it says.

`POST /v1/rerank`, for models with `rerank` modality.

A reranker is a cross-encoder: it scores a query against each document and returns them ordered. It
generates nothing, so there are no completion tokens and billing is prompt-only.

Two things matter if you were doing this through the chat endpoint. The **whole document set is one
request**, not one per document — against a 60 RPM budget, scoring 50 candidates costs 1 unit rather
than 50. And each result's `index` points into the `documents` you sent, never into the results, so
a reordered result stays attributable to its input.

- `client.rerank(&RerankRequest).await`, returning `RerankResponse`, whose `ranking()` gives the input indices best-first. An empty `documents` list is refused before the wire, which the gateway would answer
  `400 validation-error`.

The SDK now says when it is waiting, and for how long.

The platform's `Retry-After` on a `429` is seconds until the window resets, so it runs 0–60. An SDK
that respects it — as it should — looks from outside like one slow call among fast ones, and that
arrives as a latency bug report. A wait long enough for a person to notice is now reported at INFO
with `delay_s`; sub-second backoff stays at DEBUG, because the noise worry is frequent small retries
rather than the rare long one.

The wait is reported as a wait, never folded into `duration_ms` — that is measured per attempt and
deliberately excludes time spent sleeping. Time waiting is not time the gateway took.

A token response in neither documented envelope is now a transport failure rather than an OAuth2
one.

A `4xx` carrying `error` is an OAuth2 outcome; a `5xx` carrying `type` is the gateway's own
problem+json. A body with neither — an HTML error page from a proxy or load balancer that answered
instead of the gateway — used to be reported as an OAuth2 failure, which tells a caller their
credentials are the problem. That is both wrong and the most expensive wrong answer available here:
the obvious next step is rotating a perfectly good secret.

`spec/errors.json` and this SDK's error mapping are now held together by a test (a unit test in `src/error.rs`): every
catalogued error must map to a class, and its retryability must match the catalog.

Go has had this from the start and it earned its keep the day the platform added two token errors —
it refused the change until both had a mapping, then refused again until their retryability matched.
This SDK had no equivalent, and shipped one of them with the wrong retryability until a hand-written
test caught it.

A failed token request is now typed by its envelope rather than read as OAuth2 unconditionally.

A `4xx` is an OAuth2 outcome in the RFC 6749 shape — wrong credentials, a scope the client does not
hold — and is never worth retrying. A `5xx` is the gateway failing to reach the auth-service,
arrives as problem+json, and `upstream-unavailable` **is** worth retrying. Reading both as OAuth2
left the 5xx with no type and no retryability, so a momentary blip looked exactly like bad
credentials and the request was abandoned rather than retried.

`not-configured` shares that status and is deliberately *not* retryable, which is why the suffix
drives the decision rather than the status.

**One address, not two.** The gateway serves both the inference API and `/oauth2/token`, so the
auth-service address is **gone from this SDK** rather than defaulted.

Keeping it as a field that defaults to the gateway would still have taught every consumer that a
second address exists. It does not, for them: the platform's auth-service is now reachable only
from inside the deployment, which is what it was always for. Removing the field removes the failure
it enabled — pointing the SDK at a self-hosted deployment used to mean changing two addresses, and
forgetting the second left the client asking the **official platform** for a token to use somewhere
else, with nothing erroring.

**Breaking**, and deliberately so while the surface is pre-1.0: `Config::auth_base_url` and `DEFAULT_AUTH_BASE_URL` are **removed**, along with `AXONIUM_AUTH_BASE_URL`. A deployment whose gateway
has no token endpoint wired up answers `not-configured`, which says exactly that rather than
failing obscurely.

Per-request usage lookup: what one of your own requests was charged, and why it stopped.

Both aggregate usage endpoints require `admin:read`, which a normal client neither has nor should
have — so `termination_reason` existed for callers who could not read it. The platform shipped this
after we made that case; this is the client half.

A replay has its own request id and no row of its own, so looking that id up is a `not-found` —
correctly, since a replay is not billed. `meta.idempotent_replay_of` names the generation that was
charged; look *that* up. The round trip is verified live in all three languages.

`termination_reason` is a plain string, not an enum. The platform proposed a fourth value this week
and withdrew it; the next one may not be withdrawn, and a closed set would turn a new value into a
parse failure for a caller who only wanted the token counts.

- `client.usage(request_id).await`, returning `RequestUsage`. `ErrorKind::NotFound` is new.

`ResponseMeta::idempotent_replay_of` carries, on a replay, the request id of the generation that was
actually billed.

A replay has its own request id and no usage row of its own, so looking that id up returns `404` —
correctly, since replaying reaches no model and is not billed. This header names the id that does
resolve, which makes it the only path from the response a caller received to the charge it
corresponds to. `None`/empty on anything that is not a replay.

The quickstart in the README used a model name that is not registered, so copying it produced
`400 unknown-model` rather than a completion. Examples and doc comments now use a real slug, and
each README says what a slug is: it never changes and is never reused, so pinning one is safe, but
which ones exist depends on the deployment and on what the token is granted — the catalog endpoint
is the source of truth, not the README.

Tool calls are now typed like everything around them.

They used to arrive as raw dicts while the message carrying them was a model. The asymmetry cost a
consumer real work twice over: reaching in by hand to read a name, and converting back to dicts to
feed a call into the next request. Both directions are now the same type.

`arguments` deliberately stays the model's own JSON **string** rather than a decoded object.
Decoding it at parse time would raise from inside a response model, for a caller who only wanted to
see what the model had managed to say — a generation stopped by `max_tokens` leaves a string that
was never going to parse. Decoding is a separate, explicit call that fails loudly, and the raw
string stays reachable either way.

**Breaking**, and deliberately so while the surface is still pre-1.0: anything indexing a tool call
as a dict/map/`Value` needs the field instead.

- `ToolCall` and `FunctionCall`, returned by `ChatCompletion::tool_calls()` and
  `ChatStream::tool_calls()`, and accepted by `Message::tool_calls`. `type` is spelled `kind` on
  the struct and still serialises as `type`.
- `call.parse_arguments()` decodes the arguments, failing with the new `Error::ToolCallArguments`,
  which carries the call id and the raw string. `call.name()` reads the function name directly.

Streamed tool calls are now reassembled for you.

A tool call arrives split across as many deltas as it takes — `{`, `"`, `city` — and the fragments
are individually invalid JSON. Only the first carries the identity, and **`index` is the
correlation key**, because `id` never repeats. Every consumer was writing that join by hand.

The assembled call is **byte-for-byte the shape a non-streaming completion returns**, `arguments`
included: still a JSON *string*, not a decoded object. That is deliberate — the same caller code
handles both, and a stream cut short by `max_tokens` hands back the fragment that did arrive
instead of raising or dropping the call. Check `finish_reason` before decoding.

A second contract case was recorded live for this: a single-call recording cannot tell `index`
correlation apart from any other strategy, so a stream with two concurrent calls was recorded to
give the case teeth. The manifest is now 25 cases, and all three SDKs replay both.

- `ChatStream::tool_calls()`, alongside `content()` and `usage()`.
- **Breaking:** `Chunk::tool_calls()` is renamed `Chunk::tool_call_fragments()`. It always returned
  fragments rather than calls, and the name said otherwise at exactly the moment a real
  `tool_calls()` appeared one level up. Renamed now, while the crate is `0.x` and a consumer pays
  a compile error rather than a silent wrong result.

### 0.2.0

- **Spans and events behind a `tracing` feature**, off by default so the crate stays free of the
  dependency for anyone tracing with something else. Each attempt carries method, path, model,
  status, attempt, duration and the gateway's correlation IDs. Prompts, completions and credentials
  are never emitted, pinned by a test that fails if the crate is changed to emit any.

### 0.1.0

First release. Core implemented: chat, streaming with cancellation on drop, embeddings, images, both credential
modes, idempotency keys, instance pinning and the full error taxonomy. All 24 shared contract cases
pass. Structured logging, a tracing hook and publication to crates.io remain.

`Config` and `Client` redact the client secret from `Debug`, which a derived implementation printed
in full.

**Not in 0.1.0, and a crates.io version cannot be replaced:** structured logging, a tracing hook,
and reassembly of streamed tool calls. The first two arrived in `0.2.0`.

---

## Legacy (pre-rewrite, single-package Python SDK)

Versions `v0.1.0` through `v0.6.0` targeted the first-generation Prometheus platform and are
documented in the [v0.6.0 release notes](https://github.com/Root1V/axonium-sdk/releases/tag/v0.6.0).
That code is preserved at tag `v0.6.0`.
