# Getting started

**Axonium** is a set of client SDKs for the **Prometheus** inference platform, in Python, Go,
Rust, Swift and TypeScript.

**Prometheus** is a self-hosted inference platform: a catalog of small language, embedding,
reranking and image models, served across managed instances behind one authenticated API, with
per-model access control, rate limits, spend caps and per-request usage accounting.

Your client talks to exactly one of its components — the **gateway**, which serves inference and
issues tokens at a single address. The rest of the platform sits behind it: the model registry and
the instances it schedules across, the auth service, the rate limiter and per-backend circuit
breaker, and the usage store. These SDKs never address any of those directly, which is why there is
one host to configure and not six.

Five SDKs, one contract. They are separate implementations that share no code — what keeps them
identical is a corpus of recorded wire bytes that all five replay. Behaviour that matches is
verified, not intended.

**This site is also available [in Spanish](es/index.md).** Every example on it is shown in all five languages. If a tab is missing, that is a bug in the site and a
test fails for it.

## Install

```bash
pip install axonium
```

```bash
go get github.com/Root1V/axonium-sdk/go
```

```bash
cargo add axonium
```

```bash
# Package.swift: .package(url: "https://github.com/Root1V/axonium-sdk-swift", from: "0.3.0")
swift package add-dependency https://github.com/Root1V/axonium-sdk-swift --from 0.3.0
```

```bash
npm install axonium
```

## The smallest thing that works

Credentials are the only setting you must supply. The gateway issues its own tokens, so there is
one address, and usually none to give:

```python
from axonium import Axonium

with Axonium(client_id="...", client_secret="...") as client:
    completion = client.chat.completions.create(
        model="qwen3-0.6b",
        messages=[{"role": "user", "content": "Say hello"}],
    )
    print(completion.content)
```

```go
client, err := axonium.New(axonium.Config{ClientID: "...", ClientSecret: "..."})
if err != nil {
	return err
}
defer client.Close()

completion, err := client.Chat.Create(ctx, axonium.ChatRequest{
	Model:    "qwen3-0.6b",
	Messages: []axonium.Message{axonium.TextMessage("user", "Say hello")},
})
if err != nil {
	return err
}
fmt.Println(completion.Content())
```

```rust
let client = Client::new(Config {
    client_id: "...".into(),
    client_secret: "...".into(),
    ..Default::default()
})?;

let completion = client
    .chat(&ChatRequest {
        model: "qwen3-0.6b".into(),
        messages: vec![Message::text("user", "Say hello")],
        ..Default::default()
    })
    .await?;
println!("{}", completion.content());
```

```swift
let client = try AxoniumClient(
    configuration: AxoniumConfiguration(
        gatewayBaseURL: "...", clientID: "...", clientSecret: "..."))

let completion = try await client.chat(
    ChatRequest(model: "qwen3-0.6b", messages: [.user("Say hello")]))
print(completion.content ?? "")
```

```typescript
import { Axonium } from "axonium";

const client = new Axonium({ clientId: "...", clientSecret: "..." });

const completion = await client.chat.completions.create({
  model: "qwen3-0.6b",
  messages: [{ role: "user", content: "Say hello" }],
});
console.log(completion.content);
```

Credentials can come from the environment instead — `AXONIUM_CLIENT_ID` and
`AXONIUM_CLIENT_SECRET`. The SDK never reads a `.env` file itself; loading one is the
application's job, and [Configuration](04-configuration.md) says why.

## What you get that a plain HTTP call does not give you

A `POST` to the gateway is not hard. What takes time to get right is everything around it, and
that is what these SDKs are:

- **Tokens** fetched, cached and refreshed ahead of expiry, with a single retry on a `401`.
- **Typed errors**, one class per entry in the gateway's error catalog, so `except
  SpendCapExceededError` is a thing you can write.
- **Retries that cannot double-bill.** A retry is attempted only where the platform states no
  generation occurred. See [Failure](03-failure.md) — this is the part worth reading.
- **Streaming** with in-band error detection, real cancellation, and tool calls reassembled into
  the same shape non-streaming returns.
- **Rate-limit visibility** on every response, per budget.

## Where to go next

- [Concepts](01-concepts.md) — the pieces and what each one is for.
- [Making calls](02-calls.md) — the client and the six things it can ask for.
- [Failure, retries and idempotency](03-failure.md) — the expensive part.
- [Configuration and transport](04-configuration.md) — pointing this at your deployment.
- [Composed operations](05-composed.md) — when one call is not enough.
- [Testing against Axonium](06-testing.md) — building on top without spending anything.
- [What it does not do](07-limits.md) — the limits, said out loud.
- [Python API reference](08-reference.md) — every exported symbol.

Reference documentation for the other four is generated by their own ecosystems:
[pkg.go.dev](https://pkg.go.dev/github.com/Root1V/axonium-sdk/go) for Go,
[docs.rs](https://docs.rs/axonium/latest/axonium/) for Rust, DocC in the
[Swift repository](https://github.com/Root1V/axonium-sdk-swift), and the `.d.ts` files plus
[`docs/api.md`](https://github.com/Root1V/axonium-sdk/blob/main/typescript/docs/api.md) for
TypeScript, which also ships a
[migration guide from Python](https://github.com/Root1V/axonium-sdk/blob/main/typescript/docs/migrating-from-python.md).

---

*This page and the rest of this site are generated from `docs/*.md`. A test fails if the rendered
HTML stops matching its source, and another fails if the reference stops matching the package.*
