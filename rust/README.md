# axonium (Rust)

Rust SDK for the Prometheus inference platform.

Prometheus is a self-hosted inference platform — a catalog of small language, embedding, reranking
and image models across managed instances, behind one authenticated API with per-model access
control, rate limits, spend caps and usage accounting. This SDK talks to one of its components, the
**gateway**, which serves inference and issues tokens at a single address.

> **Status: 0.4.0, published to crates.io.** Chat, streaming with cancellation, embeddings,
> images, both credential modes, idempotency keys, instance pinning and the full error taxonomy are
> implemented, and all 25 shared contract cases replay against the same recorded wire bytes the
> Python and Go SDKs use. Structured logging and an optional tracing hook are in.

Requires Rust 1.75+. Async, on any runtime — `tokio` is used for timers only.

## Quick start

```rust
use axonium::{ChatRequest, Client, Config, Message};

let client = Client::new(Config {
    client_id: "...".into(),
    client_secret: "...".into(),
    ..Default::default()
})?;

let completion = client.chat(&ChatRequest {
    model: "qwen3-0.6b".into(),
    messages: vec![Message::text("user", "Hello")],
    ..Default::default()
}).await?;
println!("{}", completion.content());
```

Only the credentials are required. The gateway address defaults to the official platform and
serves **both** the inference API and the token endpoint, so there is a single address to know
— usually none to supply.

The model name is a **slug**. A slug never changes and is never reused, so pinning one in code
is safe — but which slugs exist depends on the deployment and on what your token is granted, so
`client.models().await` is the source of truth rather than anything written here.

Streaming is a separate method, because it needs a different scope, is never retried
automatically, and returns a different type:

```rust
let mut stream = client.chat_stream(&request).await?;
while let Some(chunk) = stream.next().await? {
    print!("{}", chunk.content());
}
println!("{:?}", stream.usage());
```

### Streamed tool calls

Tool calls arrive split across as many deltas as it takes — `{`, `"`, `city` — and the fragments
are individually invalid JSON. The SDK reassembles them, keyed by the wire `index` (the identity
arrives only in the first fragment, and `id` never repeats), and hands back **exactly the shape a
non-streaming completion returns**:

```rust
let mut stream = client.chat_stream(&request).await?;
while stream.next().await?.is_some() {}
for call in stream.tool_calls() {
    // Err(Error::ToolCallArguments) if the generation was cut off mid-call.
    println!("{} {:?}", call.name(), call.parse_arguments()?);
}
```

The same `ToolCall` comes back from `completion.tool_calls()` on a non-streaming call, and
`Message::tool_calls` takes it straight back, so a tool-use loop converts in neither direction.

`call.function.arguments` stays the model's own JSON *string*; `parse_arguments()` decodes it and
fails with `Error::ToolCallArguments` if it will not parse. That happens when a generation stopped
on a `finish_reason` of `length` partway through writing the call — the raw string stays readable,
so you can still see what the model was trying to call.

**Dropping the stream cancels the request**, which the gateway passes to Prometheus, which stops
generating and frees the backend slot. An abandoned stream stops costing money — but only if you
drop it.

## Dependencies, and why there are any

The Go SDK has none: its standard library has HTTP and JSON. Rust's does not, so this takes the
minimum — `reqwest` (with `rustls`, so building never needs a system OpenSSL), `serde`,
`serde_json`, and `tokio` for timers. Nothing is pulled in for tracing or logging.

## What the client does for you

**Typed errors.** Every gateway error maps to an `ErrorKind` you match on, never on `detail` —
that is prose the platform may reword. An unrecognised code falls back to its status class rather
than failing to parse, because the catalogue grows. Token-endpoint failures are a separate variant
so a handler for "the gateway is unhappy" cannot swallow "your credentials are wrong".

**Retries that will not double-bill you.** There is no idempotency by default: a retried
generation is a new billable one. So retries happen only where the platform says no generation
occurred. A client-side timeout is retried **only** when an `idempotency_key` was supplied, because
that is the one thing that makes the repeat free.

**Idempotency keys**, on every endpoint including streaming. On a stream a key replays one the
gateway *finished* and whose delivery your connection dropped; one the model itself broke has
nothing complete to replay. What happens to a key whose request *failed* is **not
specified** — that is the gateway's decision, not this SDK's, and assuming a failure frees the key
has already cost one consumer a day of confusing retries.

**Instance pinning** through `instance`, by label (`#2`) or full id — never through `model`, since
a grant covers a model and billing attributes to a model. A pin opts out of load balancing *and*
failover, so it is for reproducing a problem rather than for normal traffic, and it is kept across
retries.

**Correlation on every response.** `meta` carries the gateway's request, trace and instance ids on
successes as well as failures, plus the rate-limit budget so you can slow down before a `429`.

**Reasoning models.** Chain of thought arrives in its own field before any answer token, so
`content()` stays empty until the model stops thinking — with a small `max_tokens` it can stay
empty for the whole response. Both are exposed and neither is inferred from the other.

## Configuration

**You should only need your credentials**: the base URLs default to the official Prometheus
platform, and those defaults are provisional until it moves to its cloud host.

Unset fields fall back to `AXONIUM_GATEWAY_BASE_URL`,
`AXONIUM_CLIENT_ID`, `AXONIUM_CLIENT_SECRET`, `AXONIUM_SCOPE`, `AXONIUM_CA_BUNDLE` and
`AXONIUM_VERIFY_MODALITY`. A missing required setting fails at construction, naming both the field
and the variable that can supply it.

## Observability

Off by default, behind a feature, so the crate stays free of a tracing dependency for anyone who
traces with something else or not at all:

```toml
axonium = { version = "0.3", features = ["tracing"] }
```

With it on, each operation opens a span and each attempt emits an event carrying `method`, `path`,
`model`, `status`, `attempt`, `duration_ms` and the gateway's `request_id`, `trace_id` and
`instance_id`. Attribute names follow the GenAI semantic conventions, so the spans are readable by
tooling that already understands LLM traffic.

**Prompts, completions and credentials are never emitted, and there is no option to enable it.**
Correlating a request with the platform's traces needs the IDs, not the content — and a library
that can be configured to log prompts is how prompts reach a collector nobody audited. A test
fails if the crate is changed to emit any.

## What this release does not have

Stated here rather than discovered, because a crates.io version can be yanked but never replaced.

- **The surface is still `0.x`.** It is complete against the current gateway contract, but the
  tri-party coordination this SDK is built inside keeps surfacing things, and changing shape before
  `1.0` costs a consumer far less than after.

## Development

```bash
cd rust
cargo test
cargo clippy --all-targets -- -D warnings
cargo fmt --check
```

Behaviour is pinned to the other SDKs by the shared contract cases in [`../spec/`](../spec/), which
Python, Go and Rust all replay against the same recorded wire bytes.
