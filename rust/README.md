# axonium (Rust)

Rust SDK for the Prometheus Gateway inference API.

> **Status: 0.1.0, in progress.** Chat, streaming with cancellation, embeddings, images, both
> credential modes, idempotency keys, instance pinning and the full error taxonomy are implemented,
> and all 24 shared contract cases replay against the same recorded wire bytes the Python and Go
> SDKs use. Not yet published to crates.io.

Requires Rust 1.75+. Async, on any runtime — `tokio` is used for timers only.

## Quick start

```rust
use axonium::{ChatRequest, Client, Config, Message};

let client = Client::new(Config {
    auth_base_url: "https://auth.example".into(),
    gateway_base_url: "https://gateway.example".into(),
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

Streaming is a separate method, because it needs a different scope, is never retried
automatically, and returns a different type:

```rust
let mut stream = client.chat_stream(&request).await?;
while let Some(chunk) = stream.next().await? {
    print!("{}", chunk.content());
}
println!("{:?}", stream.usage());
```

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
nothing complete to replay.

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

Unset fields fall back to `AXONIUM_AUTH_BASE_URL`, `AXONIUM_GATEWAY_BASE_URL`,
`AXONIUM_CLIENT_ID`, `AXONIUM_CLIENT_SECRET`, `AXONIUM_SCOPE`, `AXONIUM_CA_BUNDLE` and
`AXONIUM_VERIFY_MODALITY`. A missing required setting fails at construction, naming both the field
and the variable that can supply it.

## Still to come

Structured logging and a tracing hook (the Go SDK has both), a published crate, and the remaining
polish that would justify calling it 1.0.

## Development

```bash
cd rust
cargo test
cargo clippy --all-targets -- -D warnings
cargo fmt --check
```

Behaviour is pinned to the other SDKs by the shared contract cases in [`../spec/`](../spec/), which
Python, Go and Rust all replay against the same recorded wire bytes.
