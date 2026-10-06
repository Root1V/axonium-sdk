# Configuration and transport

> **How do I point this at my deployment?**

## One address

There is **one** address, and usually none to supply. The gateway issues its own tokens and serves
inference, so `gateway_base_url` is the only host setting that exists.

It did not always work that way, and the reason it changed is worth keeping: an auth address and a
gateway address meant that pointing the SDK at your own deployment required changing **two**
things. Forgetting the second left the client asking the official platform for a token and using it
somewhere else. **Nothing failed.** The token was simply issued by the wrong party. A setting whose
misuse is silent is worse than a setting that is missing, so the second address was removed
entirely rather than given a good default — an optional field still teaches consumers that a second
address exists.

If you have `AXONIUM_AUTH_BASE_URL` left in an environment, it is ignored.

## Precedence

First one wins:

1. A per-call argument
2. A constructor argument
3. An environment variable
4. Otherwise: `ConfigurationError`, naming the setting **and** its environment variable

| Setting | Environment variable | Default |
|---|---|---|
| `gateway_base_url` | `AXONIUM_GATEWAY_BASE_URL` | `http://127.0.0.1:8020` |
| `client_id` | `AXONIUM_CLIENT_ID` | — required |
| `client_secret` | `AXONIUM_CLIENT_SECRET` | — required |
| `scope` | `AXONIUM_SCOPE` | unset: the gateway decides |
| `ca_bundle` | `AXONIUM_CA_BUNDLE` | system trust store |

Requesting no `scope` is not the same as requesting all of them. Leave it unset unless you want a
**narrower** token than your client is entitled to; the granted scope is always read back from the
response rather than assumed.

## The SDK does not read `.env`

Loading a `.env` file is the application's decision, not a library's. A library that reads one
changes behaviour based on a file the caller did not mention, and in a server process that is a
surprise nobody asked for.

```bash
uv run --env-file .env python my_script.py
```

Python resolves the environment **at construction time**, not at import — which removes a class of
bug the previous generation of this SDK had, where a field defaulted from `os.getenv` in a class
body was evaluated once, at import, before anything had been configured.

## Timeouts

```python
from axonium import Axonium, Timeouts

client = Axonium(
    client_id="...",
    client_secret="...",
    timeouts=Timeouts(connect=10, read=600, write=600, pool=10, stream_read=180),
)
```

```go
client, err := axonium.New(axonium.Config{
	ClientID:     "...",
	ClientSecret: "...",
	Timeouts: axonium.Timeouts{
		Connect: 10 * time.Second,
		Request: 600 * time.Second,
		Stream:  180 * time.Second,
		Auth:    15 * time.Second,
	},
})
```

```rust
let client = Client::new(Config {
    client_id: "...".into(),
    client_secret: "...".into(),
    timeouts: Timeouts {
        connect: Duration::from_secs(10),
        request: Duration::from_secs(600),
        stream: Duration::from_secs(180),
        auth: Duration::from_secs(15),
    },
    ..Default::default()
})?;
```

```swift
let client = try AxoniumClient(
    configuration: AxoniumConfiguration(
        gatewayBaseURL: "...",
        clientID: "...",
        clientSecret: "...",
        timeouts: Timeouts(connect: 10, request: 600, streamRead: 180, auth: 30)))
```

```typescript
const client = new Axonium({
  clientId: "...",
  clientSecret: "...",
  // Milliseconds, because that is what every timer in this runtime takes and converting at the
  // boundary is one more place to be wrong by a factor of a thousand.
  timeouts: { connect: 10_000, request: 600_000, stream: 180_000 },
});
```

The five do not carve the budget up identically, and the docs will not pretend they do. Python
exposes httpx's four phases (`connect`, `read`, `write`, `pool`); Go, Rust and Swift bound the whole
call with `request`; TypeScript counts in milliseconds because that is what its runtime's timers
take. What matters is the same everywhere: the read budget is generous because generation is slow,
and the streaming one is separate and shorter.

Read timeouts are generous because generation is slow. The streaming read timeout is separate and
shorter: it bounds the gap **between chunks**, not the length of the whole stream, so a stalled
stream is detected without capping a long one.

Any call can override:

```python
client.chat.completions.create(model="qwen3-0.6b", messages=[...], timeout=30)
```

```go
ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
defer cancel()

completion, err := client.Chat.Create(ctx, request)
```

```rust
let completion = tokio::time::timeout(
    Duration::from_secs(30),
    client.chat(&request),
).await??;
```

```swift
// No per-call timeout: cancel the enclosing Task, which tears down the request.
let task = Task { try await client.chat(request) }
Task { try await Task.sleep(for: .seconds(30)); task.cancel() }
let completion = try await task.value
```

```typescript
// Either a per-call timeout, or your own AbortSignal -- whichever fires first wins.
await client.chat.completions.create(request, { timeout: 30_000 });
await client.chat.completions.create(request, { signal: AbortSignal.timeout(30_000) });
```

**Python and TypeScript take a per-call `timeout`; Go, Rust and Swift deliberately do not.** A
deadline on a single call is already what `context.Context`, `tokio::time::timeout` and task
cancellation are, and a second mechanism beside them is one more place for the two to disagree.
TypeScript has no such built-in, so it carries its own — and also accepts your `AbortSignal`, with
whichever fires first winning.

**A client-side timeout is not retried**, and the error says why — see [Failure](03-failure.md).

## Retry policy

```python
from axonium import RetryPolicy

client = Axonium(
    client_id="...",
    client_secret="...",
    retry=RetryPolicy(max_attempts=3, initial_backoff=0.5, max_backoff=60.0, jitter=True),
)
```

```go
client, err := axonium.New(axonium.Config{
	ClientID:     "...",
	ClientSecret: "...",
	Retry: &axonium.RetryPolicy{
		MaxAttempts:    3,
		InitialBackoff: 500 * time.Millisecond,
		MaxBackoff:     60 * time.Second,
		Jitter:         true,
	},
})
```

```rust
let client = Client::new(Config {
    client_id: "...".into(),
    client_secret: "...".into(),
    retry: RetryPolicy {
        max_attempts: 3,
        initial_backoff: Duration::from_millis(500),
        max_backoff: Duration::from_secs(60),
        jitter: true,
        ..Default::default()
    },
    ..Default::default()
})?;
```

```swift
let client = try AxoniumClient(
    configuration: AxoniumConfiguration(
        gatewayBaseURL: "...",
        clientID: "...",
        clientSecret: "...",
        retry: RetryPolicy(maxAttempts: 3, initialBackoff: 0.5, maxBackoff: 60, jitter: true)))
```

```typescript
const client = new Axonium({
  clientId: "...",
  clientSecret: "...",
  retry: { maxAttempts: 3, initialBackoff: 500, maxBackoff: 60_000, jitter: true },
});

// Or turn it off entirely, which is a policy and not an absence of one.
import { NO_RETRY } from "axonium";
const strict = new Axonium({ clientId: "...", clientSecret: "...", retry: NO_RETRY });
```

`max_backoff` does double duty: it is the backoff ceiling **and** the longest server-supplied
`Retry-After` the SDK will sit through. Setting it to zero turns every wait into an immediate
error carrying `retry_after` — which is what you want in a request handler that must not block.

## TLS

Point `ca_bundle` at your deployment's trust chain. There is no flag to disable verification, and
there will not be one: the failure mode of that flag is that it gets set during an incident and
never unset.

Swift takes `additionalTrustAnchors` as **data** rather than a path, because an app bundles a
certificate as a resource or pulls it from an MDM profile, and a path into a sandboxed container is
not something a caller can usefully name.

**TypeScript has no equivalent and cannot have one** without breaking its zero-dependency promise:
native `fetch` does not expose TLS trust. On Node, set `NODE_EXTRA_CA_CERTS` on the process. That is
a real gap rather than a design preference, and it is said here instead of being discovered.

## Connection reuse

One client per process. The connection pool and the cached token live on it, so constructing one
per request throws both away — and re-fetches a token you already had.

All five clients are safe for concurrent use.

Next: [Composed operations](05-composed.md).
