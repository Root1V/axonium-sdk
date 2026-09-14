# axonium (Go)

Go SDK for the Prometheus Gateway inference API.

> **Status: v0.2.0.** Streaming with real cancellation, both credential modes, idempotency keys,
> instance pinning, the full error taxonomy and the shared contract corpus — all implemented and
> tested, with 25 of 25 contract cases replaying the same recorded wire bytes the Python SDK does.
>
> The version is `0.x` rather than `1.0.0` deliberately. The surface is complete against the
> current gateway contract and is not going to churn for its own sake, but the tri-party
> coordination this SDK is built inside keeps surfacing things — two error types were split and
> streaming idempotency arrived in a single week. If something there requires changing this
> surface, doing it before `1.0.0` costs a consumer far less than doing it after.

## Installation

```bash
go get github.com/Root1V/axonium-sdk/go@latest
```

Requires Go 1.22+. **No third-party dependencies** — standard library only, so vendoring this adds
nothing to your dependency tree and nothing to your supply-chain surface.

The module path includes the `go/` subdirectory because this is a monorepo, and release tags are
correspondingly prefixed: `go/v0.1.0` publishes version `v0.1.0` of this module.

## Quick start

```go
client, err := axonium.New(axonium.Config{
	ClientID:     "...",
	ClientSecret: "...",
})
if err != nil {
	return err
}
defer client.Close()

completion, err := client.Chat.Create(ctx, axonium.ChatRequest{
	Model:    "qwen3-0.6b",
	Messages: []axonium.Message{axonium.TextMessage("user", "Hello")},
})
fmt.Println(completion.Content())
```

Only the credentials are required. The gateway address defaults to the official platform and
serves **both** the inference API and the token endpoint, so there is a single address to know
— usually none to supply.

The model name is a **slug**. A slug never changes and is never reused, so pinning one in code
is safe — but which slugs exist depends on the deployment and on what your token is granted, so
`client.Models(ctx)` is the source of truth rather than anything written here.

Streaming is a separate method, because it needs a different scope, is never retried automatically,
and returns a different type:

```go
stream, err := client.Chat.Stream(ctx, req)
if err != nil {
	return err
}
defer stream.Close()

for stream.Next() {
	fmt.Print(stream.Current().Content())
}
return stream.Err()
```

### Streamed tool calls

Tool calls arrive split across as many deltas as it takes — `{`, `"`, `city` — and the fragments
are individually invalid JSON. The SDK reassembles them, keyed by the wire `index` (the identity
arrives only in the first fragment, and `id` never repeats), and hands back **exactly the shape a
non-streaming completion returns**:

```go
for stream.Next() {
}
if err := stream.Err(); err != nil {
	return err
}
for _, call := range stream.ToolCalls() {
	args, err := call.ParseArguments()
	if err != nil {
		return err // ErrToolCallArguments: the generation was cut off mid-call
	}
	fmt.Println(call.Function.Name, args)
}
```

The same `ToolCall` comes back from `completion.ToolCalls()` on a non-streaming call, and
`Message.ToolCalls` takes it straight back, so a tool-use loop converts in neither direction.

`call.Function.Arguments` stays the model's own JSON *string*; `ParseArguments` decodes it and
returns an error wrapping `ErrToolCallArguments` if it will not parse. That happens when a
generation stopped on a `FinishReason` of `length` partway through writing the call — the raw
string stays readable, so you can still see what the model was trying to call.


**Always close the stream.** Closing is what propagates cancellation to the gateway and on to
Prometheus, which stops generating and frees the backend slot. A stream that is merely abandoned
keeps the GPU busy and keeps billing. Cancelling the context does the same thing, so
`defer stream.Close()` and `ctx` cancellation are interchangeable — a consumer who wrote one does
not have to rewrite it to get the other.

This is verified rather than asserted: the test suite measures how many chunks the upstream
produced after the client went away, because a local loop exiting proves nothing on its own.

## What the client does for you

**Authentication, in two modes.** See below.

**Typed errors.** Every gateway error maps to a sentinel you match with `errors.Is`, so you branch
on the error rather than string-matching a human-readable message:

```go
switch {
case errors.Is(err, axonium.ErrRateLimit):
	// apiErr.RetryAfter says how long
case errors.Is(err, axonium.ErrForbidden):
	// the error names the scopes the token actually holds
case errors.Is(err, axonium.ErrServer):
	// any 5xx, including ones this SDK does not know about yet
}
```

An unrecognized error code falls back to its status class rather than failing to parse — the
catalog will grow. Token-endpoint failures are a separate type (`*OAuthError`) and deliberately do
**not** match the gateway sentinels, so a handler for "the gateway is unhappy" cannot silently
swallow "your credentials are wrong".

**Retries that will not double-bill you.** This API has no idempotency mechanism: a retried
generation is a new billable one, not a replay. So retries happen only where the platform tells us
no generation occurred — a rate limit, or a circuit breaker that fast-failed without reaching a
model. A `502` may have reached one, so retrying it is opt-in. Client-side timeouts are never
retried. A server-supplied `Retry-After` is honored but capped: a wait longer than `MaxBackoff` is
handed back to you rather than slept through, because blocking your caller for an hour is worse
than telling them.

**Correlation on every response.** `X-Request-ID` and `X-Trace-ID` are parsed onto `Meta` for
successes as well as failures, and onto every error. `client.LastRateLimit()` exposes the budget so
you can slow down before a `429` rather than only reacting to one.

**Reasoning models.** Chain of thought arrives in its own field, before any answer token.
`Content()` therefore stays empty until the model stops thinking — with a small `max_tokens` it can
stay empty for the whole response, with `FinishReason` `"length"` and usage to pay for. Both fields
are exposed and neither is inferred from the other.

**Optional modality preflight.** The gateway's check is one-directional: calling `/v1/embeddings`
with a text model is rejected, but calling `/v1/chat/completions` with an *embedding* model is not
— it returns `200` with degenerate output that you pay for. `VerifyModality: true` catches that,
and typos, before the request is sent. Off by default because it costs one catalog request. If the
catalog cannot be loaded the check is skipped rather than failing your request: a guard rail should
not become a new way for inference to break.

## Observability

The platform owns tracing. This SDK provides only the complementary piece: enough for your traces
and logs to line up with the platform's, and nothing that duplicates what it already records.

**Structured logging** through `log/slog`, silent until you supply a logger:

```go
client, err := axonium.New(axonium.Config{
	Logger: slog.New(slog.NewJSONHandler(os.Stderr, nil)),
})
```

Each completed attempt logs `method`, `path`, `model`, `status`, `duration_ms`, `attempt` and the
gateway's `request_id`, `trace_id` and `instance_id`.

**Prompts, completions and credentials are never logged, and there is no flag to enable it.**
Correlating a request with the platform's traces needs the IDs, not the content — and a library
that can be configured to log prompts is how prompts end up in an aggregator nobody audited.
Whatever you need to log about the content, you have at the call site.

**Tracing through an interface**, not a dependency:

```go
type Tracer interface {
	StartSpan(ctx context.Context, name string, attrs map[string]any) (context.Context, Span)
}
```

This is deliberately not an OpenTelemetry import. Zero third-party dependencies is a property
worth keeping for something other people vendor, and importing an OTel SDK here would put it in
every consumer's tree — including the ones tracing with something else, or not at all. Wiring it
is a few lines on your side, and the attribute names follow the GenAI semantic conventions so the
spans are readable by tooling that already understands LLM traffic.

Spans carry `gen_ai.system`, `gen_ai.request.model`, and on completion the gateway's
`prometheus.request_id`, `prometheus.trace_id` and `prometheus.instance_id` — which is the whole
reason the span exists.

**Trace context is not propagated outbound, and this is measured rather than assumed.** The
platform runs in what its guide calls OTEL mode: it starts its own trace and returns that id,
specifically so a client cannot forge trace context. Verified against the deployment — a valid
UUID4 sent as `X-Trace-ID` is discarded, and `traceparent` is never read in either mode. So the SDK
offers no way to supply one: a parameter that silently does nothing is worse than its absence.
Correlation runs the other way, through the `X-Trace-ID` the gateway returns on every response.

**Scope diagnostics.** A `403` is matched against the scopes your token actually holds, so the
error says what is missing rather than only that access was refused. `client.TokenClaims()` answers
the same question before a call rather than after one.

## Credential modes

Two modes, permanent and mutually exclusive. Which one you use follows from how the SDK is
embedded, not from preference.

**Autonomous** — the SDK mints and refreshes its own tokens. For development, tests, and any
process that owns its credential.

```go
client, err := axonium.New(axonium.Config{ClientID: ..., ClientSecret: ...})
```

**Governed** — a host that already owns the credential supplies tokens, and the SDK never holds a
secret. The provider is the sole authority: it owns caching, refresh and rotation, and Axonium does
no refresh-ahead of its own, because two caches for one token is how a client ends up sending a
token its owner already retired.

```go
client, err := axonium.New(axonium.Config{
	TokenProvider: func(ctx context.Context, rejected string) (string, error) {
		// rejected is the token the gateway just refused, or "" on the first call.
		return host.Token(ctx, rejected)
	},
})
```

**The rejected token comes back, not a flag.** With a boolean a provider cannot tell whether two
concurrent refreshes concern the same dead token or different ones, so it must mint twice or guess
with a time window. Given the token, the answer is exact: if what it holds already differs, it
refreshed already. Ten concurrent callers rejecting one token cause one minting, and there is a
test that says so.

Asking for both modes by name is refused. Credentials that merely happen to be in the environment
are not — the provider wins, and the credentials are **discarded** with a warning, so
`client.Config().ClientSecret` really is empty. Refusing to start there would make the governed mode
the hardest one to deploy, which is backwards: a host process almost always has those variables set.

## Configuration

**You should only need your credentials.** The SDK points at the official Prometheus platform by
default, so `axonium.New(axonium.Config{ClientID: ..., ClientSecret: ...})` is the common case.

> **The default addresses are provisional.** The platform has not moved to its cloud host yet, so
> they currently point at a local deployment. Upgrading picks up the new address automatically; a
> pinned version will not, and the release that changes them will say so.

Override them for a self-hosted deployment, one or both. Unset fields fall back to the
environment, then to the official default:

| Environment variable | Purpose |
|---|---|
| `AXONIUM_GATEWAY_BASE_URL` | gateway base URL (`/v1/` inference API) |
| `AXONIUM_CLIENT_ID` | OAuth2 client ID issued by the platform operator |
| `AXONIUM_CLIENT_SECRET` | OAuth2 client secret |
| `AXONIUM_SCOPE` | Optional space-separated scope request |
| `AXONIUM_CA_BUNDLE` | Path to a CA bundle, for self-signed deployments |
| `AXONIUM_VERIFY_MODALITY` | Enable the modality preflight |

A missing required setting fails at construction, naming both the field and the variable that can
supply it.

## Scope

This SDK is **pure transport**. It speaks the gateway's contract faithfully and does not reshape
responses into a vocabulary of its own — normalization belongs above it, so there is one
implementation of that vocabulary rather than one per language SDK. Unmodeled fields stay reachable
through `Raw` rather than being dropped.

## Development

```bash
cd go
go test -race ./...
go vet ./...
gofmt -l .
```

Behaviour is pinned to the other SDKs by the shared contract cases in [`../spec/`](../spec/), which
Python, Go and Rust all replay against the same recorded wire bytes.
