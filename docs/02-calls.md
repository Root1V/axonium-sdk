# Making calls

> **I have a client. What can I ask it for?**

Six resources. Each returns a typed object that also keeps the raw payload, so a field the SDK does
not model is still reachable.

## Chat completions

```python
completion = client.chat.completions.create(
    model="qwen3-0.6b",
    messages=[{"role": "user", "content": "Summarise this in one line: ..."}],
    max_tokens=200,
)
print(completion.content)          # the first choice's text
print(completion.usage.total_tokens)
```

```go
maxTokens := 200
completion, err := client.Chat.Create(ctx, axonium.ChatRequest{
	Model:     "qwen3-0.6b",
	Messages:  []axonium.Message{axonium.TextMessage("user", "Summarise this in one line: ...")},
	MaxTokens: &maxTokens,
})
fmt.Println(completion.Content())
```

```rust
let completion = client
    .chat(&ChatRequest {
        model: "qwen3-0.6b".into(),
        messages: vec![Message::text("user", "Summarise this in one line: ...")],
        max_tokens: Some(200),
        ..Default::default()
    })
    .await?;
println!("{}", completion.content());
```

`content` is an accessor, not a field. It reaches into the first choice's message, and returns
empty rather than panicking when a response has no choices — which happens on a generation stopped
before it produced any.

### Fields the gateway does not support

The gateway silently discards request fields it does not implement. Silently is the problem: you
set `frequency_penalty`, nothing complains, and nothing applies it. The SDKs check the request
against an allowlist and emit a **warning** naming each dropped field — a warning rather than an
error, so a gateway that later adds a field does not break callers who were ahead of it.

## Streaming

A separate method, not a flag. That keeps the return type honest, makes the `inference:stream`
scope check explicit, and gives one place to say that **streams are never retried**.

```python
with client.chat.completions.stream(
    model="qwen3-0.6b",
    messages=[{"role": "user", "content": "Count to five"}],
) as stream:
    for chunk in stream:
        print(chunk.content, end="", flush=True)
    print(stream.usage)
```

```go
stream, err := client.Chat.Stream(ctx, req)
if err != nil {
	return err
}
defer stream.Close()

for stream.Next() {
	fmt.Print(stream.Current().Content())
}
if err := stream.Err(); err != nil {
	return err
}
```

```rust
let mut stream = client.chat_stream(&request).await?;
while let Some(chunk) = stream.next().await? {
    print!("{}", chunk.content());
}
```

Three things the stream handles that a naive SSE reader does not:

- **The `[DONE]` sentinel** ends iteration; it is not delivered as a chunk.
- **An in-band error** — a decoded event carrying a top-level `error` key — raises
  `StreamInterruptedError` **with the text accumulated so far**, so a partial answer is not lost to
  the exception. Detection is by key presence, not by matching the message string, because only one
  message is documented and there is no reason to believe it is the only one.
- **A stream cut short without `[DONE]`** ends iteration with what arrived, rather than hanging.

Dropping the stream cancels the request. In Rust that is `Drop`; in Go it is the `context`; in
Python it is leaving the `with` block.

## Embeddings, images, rerank

```python
vectors = client.embeddings.create(model="qwen3-embedding", input=["first", "second"])

image = client.images.generate(model="sd-turbo", prompt="a lighthouse at dusk")
open("out.png", "wb").write(image.data[0].to_bytes())

ranked = client.rerank.create(
    model="qwen3-reranker",
    query="annual membership fee",
    documents=["Rates schedule", "Opening hours", "Card benefits"],
)
print(ranked.ranking)   # indices into the documents you sent, best first
```

```go
vectors, err := client.Embeddings.Create(ctx, axonium.EmbeddingRequest{
	Model: "qwen3-embedding",
	Input: []string{"first", "second"},
})

ranked, err := client.Rerank.Create(ctx, axonium.RerankRequest{
	Model:     "qwen3-reranker",
	Query:     "annual membership fee",
	Documents: []string{"Rates schedule", "Opening hours", "Card benefits"},
})
fmt.Println(ranked.Ranking())
```

```rust
let ranked = client
    .rerank(&RerankRequest {
        model: "qwen3-reranker".into(),
        query: "annual membership fee".into(),
        documents: vec!["Rates schedule".into(), "Opening hours".into()],
        ..Default::default()
    })
    .await?;
println!("{:?}", ranked.ranking());
```

**Rerank scores the whole document set in one request.** Against a 60 RPM budget, scoring 50
candidates costs one unit rather than fifty. Each result's `index` points into the array **you**
sent, never into the results, which is what keeps a reordered result attributable to its input.

## The catalog

```python
client.models.list()    # everything the deployment serves; no token needed
client.models.mine()    # the subset your token is scoped to, cached
```

Access is deny-by-default and granted per model, and streaming needs a different scope from
non-streaming: holding `inference:read` does not grant `inference:stream`. When a `403` arrives and
`models.mine()` has been read, the SDK says which of the two you are missing rather than only that
access was refused.

## Usage for one request

```python
row = client.usage.retrieve(completion.meta.request_id)
row.usage.total_tokens
row.cost_usd            # None where no price is configured — not 0.0
row.termination_reason
```

This needs no `admin:read`. `cost_usd` is nullable on purpose: "nobody priced this" and "it cost
nothing" are different facts, and a column that cannot tell them apart reports `$0.00` for traffic
that was never free.

Next: [Failure, retries and idempotency](03-failure.md).
