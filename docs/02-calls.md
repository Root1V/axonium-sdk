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

```swift
let completion = try await client.chat(
    ChatRequest(
        model: "qwen3-0.6b",
        messages: [.user("Summarise this in one line: ...")],
        maxTokens: 200))
print(completion.content ?? "")        // the first choice's text
print(completion.usage?.totalTokens ?? 0)
```

```typescript
const completion = await client.chat.completions.create({
  model: "qwen3-0.6b",
  messages: [{ role: "user", content: "Summarise this in one line: ..." }],
  max_tokens: 200,
});
console.log(completion.content); // the first choice's text
console.log(completion.usage?.totalTokens);
```

`content` is an accessor, not a field. It reaches into the first choice's message, and returns
empty rather than panicking when a response has no choices — which happens on a generation stopped
before it produced any.

### Fields the gateway does not support

The gateway accepts an allowlisted subset and **names back** what it ignored. It did discard in
silence until `PRM-127`, which is the problem the header exists to end: you set `frequency_penalty`,
nothing complains, and nothing applies it.

**The five SDKs do three different things with a field they do not recognise**, which is worth
knowing before you rely on one:

| | an unrecognised field |
|---|---|
| Python | **not sent**, with an `UnsupportedFieldWarning` naming it |
| Go, Rust, Swift | sent, through an explicit `Extra` / `extra` / `extraFields` channel |
| TypeScript | sent, silently — the request type accepts any key |

So an engine-specific parameter such as `chat_template_kwargs` reaches llama.cpp from TypeScript and
cannot from Python. That divergence is `AXO-154` and is not yet decided.

That allowlist is the SDK's *model* of what the gateway accepts, and a model can go stale: when the
platform started honouring `response_format`, this SDK kept warning that it would be dropped for
five days. The gateway reports its own verdict in `X-Prometheus-Ignored-Parameters`, which is the
authoritative answer because it is the gateway's and not a guess about it.

**TypeScript surfaces it as `meta.ignoredParameters`** — `undefined` when the header is absent,
because the header is present only when there is something to report, and an empty array would
claim the gateway looked and found nothing. The other four read the header and discard it; the name
here is the one they will copy.

Pass `require_parameters: true` to turn a silent drop into a `400 unknown-parameter` instead, when
being quietly given less than you asked for is worse than failing.

### Structured output

```python
schema = {"type": "json_schema", "json_schema": {"name": "capital", "schema": {
    "type": "object", "properties": {"capital": {"type": "string"}}, "required": ["capital"]}}}

completion = client.chat.completions.create(
    model="qwen3-0.6b",
    messages=[{"role": "user", "content": "Capital of Peru?"}],
    response_format=schema,
)
answer = json.loads(completion.content)      # a JSON string, not a nested object
```

```go
completion, err := client.Chat.Create(ctx, axonium.ChatRequest{
	Model:          "qwen3-0.6b",
	Messages:       []axonium.Message{axonium.TextMessage("user", "Capital of Peru?")},
	ResponseFormat: schema,
})

var answer map[string]any
err = json.Unmarshal([]byte(completion.Content()), &answer)
```

```rust
let completion = client
    .chat(&ChatRequest {
        model: "qwen3-0.6b".into(),
        messages: vec![Message::text("user", "Capital of Peru?")],
        response_format: Some(schema),
        ..Default::default()
    })
    .await?;

let answer: serde_json::Value = serde_json::from_str(&completion.content())?;
```

```swift
var request = ChatRequest(model: "qwen3-0.6b", messages: [.user("Capital of Peru?")])
request.responseFormat = schema

let completion = try await client.chat(request)
let answer = try JSONSerialization.jsonObject(with: Data((completion.content ?? "").utf8))
```

```typescript
import { jsonSchema } from "axonium";

const completion = await client.chat.completions.create({
  model: "qwen3-0.6b",
  messages: [{ role: "user", content: "Capital of Peru?" }],
  // Accepts a plain JSON Schema, or anything with a toJSONSchema() method -- a Zod schema goes in
  // without this package ever importing Zod, which is what keeps it at zero dependencies.
  response_format: jsonSchema("capital", { type: "object", properties: { capital: { type: "string" } } }),
});
const answer = JSON.parse(completion.content); // a JSON string, not a nested object
```

The grammar is the engine's, and the schema is forwarded verbatim — validating it here would be a
second copy of the engine's rules, drifting from the first.

### How confident the model was

`logprobs` returns the chosen token's own probability; `top_logprobs` adds the N most likely
alternatives at each position. The point is an agent deciding when to **escalate to a person**
instead of acting on a guess.

```python
completion = client.chat.completions.create(
    model="qwen3-0.6b",
    messages=[{"role": "user", "content": "yes or no?"}],
    logprobs=True,
    top_logprobs=3,
)
for token in completion.choices[0].logprobs.content:
    print(token.token, token.probability)     # 0.99946, not -0.00054
```

```go
yes := true
three := 3
completion, err := client.Chat.Create(ctx, axonium.ChatRequest{
	Model:       "qwen3-0.6b",
	Messages:    []axonium.Message{axonium.TextMessage("user", "yes or no?")},
	Logprobs:    &yes,
	TopLogprobs: &three,
})
for _, token := range completion.Choices[0].Logprobs.Content {
	fmt.Println(token.Token, token.Probability())
}
```

```rust
let completion = client
    .chat(&ChatRequest {
        model: "qwen3-0.6b".into(),
        messages: vec![Message::text("user", "yes or no?")],
        logprobs: Some(true),
        top_logprobs: Some(3),
        ..Default::default()
    })
    .await?;
for token in &completion.choices[0].logprobs.as_ref().unwrap().content {
    println!("{} {}", token.token, token.probability());
}
```

```swift
var request = ChatRequest(model: "qwen3-0.6b", messages: [.user("yes or no?")])
request.logprobs = true
request.topLogprobs = 3

let completion = try await client.chat(request)
for token in completion.choices[0].logprobs ?? [] {
    print(token.token ?? "", token.probability ?? 0)
}
```

```typescript
const completion = await client.chat.completions.create({
  model: "qwen3-0.6b",
  messages: [{ role: "user", content: "yes or no?" }],
  logprobs: true,
  top_logprobs: 3,
});
for (const token of completion.logprobs ?? []) {
  console.log(token.token, token.probability); // 0.99946, not -0.00054
}
```

**These are natural logarithms.** `-0.00054` is about 99.95% and `-7.6` is about 0.05%. Read as a
probability it looks like a number near zero meaning "unlikely", and the mistake is silent — so all
five expose `probability` rather than making you remember to call `exp`. It is absent rather than
zero when the backend sent no `logprob`: a token it said nothing about is a different fact from one
it said was impossible.

**`top_logprobs` requires `logprobs`.** Sending it alone, or beside `logprobs: false`, is a `422`
the gateway raises before the engine sees it — the rule is llama.cpp's and the gateway enforces it so
the refusal reaches you in the usual envelope. All five SDKs refuse it at the call site instead,
because a round trip to be told that buys nothing.

**Absent `logprobs` on the response means the engine does not have the feature**, not that the model
was uncertain. The response simply carries no key. Send `require_parameters` to be told rather than
inferring it from an absence.

**The answer arrives as a JSON string in the content, not as a nested object**, and the SDKs do not
parse it for you. Same reason tool-call `arguments` stays a string: a generation stopped by
`max_tokens` leaves it truncated, and a response model that raises from the inside is worse than
one that hands you what arrived. That is not hypothetical — the first structured response measured
while writing this came back as `{\n  "capital": "Lima"\n` with `finish_reason: length`.

## Streaming

A separate method, not a flag. That keeps the return type honest, makes the `inference:stream`
scope check explicit, and gives one place to say which of a stream's two failures is retried.

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

```swift
let stream = try await client.chatStream(
    ChatRequest(model: "qwen3-0.6b", messages: [.user("Count to five")]))

for try await chunk in stream {
    print(chunk.content ?? "", terminator: "")
}
print(await stream.usage() as Any)
```

```typescript
const stream = await client.chat.completions.stream({
  model: "qwen3-0.6b",
  messages: [{ role: "user", content: "Count to five" }],
});

for await (const chunk of stream) {
  process.stdout.write(chunk.content ?? "");
}
console.log(stream.usage);
```

Four things the stream handles that a naive SSE reader does not:

- **The `[DONE]` sentinel** ends iteration; it is not delivered as a chunk.
- **A rejection that arrives instead of the stream** is a real HTTP status, not an SSE frame — the
  gateway reads the engine's status before the `200`/`text/event-stream` headers exist. It raises the
  same typed error the non-streaming call would, and **it is retried like any other request**. See
  [Failure](03-failure.md).
- **An in-band error** — a decoded event carrying a top-level `error` key — raises
  `StreamInterruptedError` **with the text accumulated so far**, so a partial answer is not lost to
  the exception. Detection is by key presence, not by matching the message string, because only one
  message is documented and there is no reason to believe it is the only one. This one is **never
  retried**: part of the answer was delivered and part was billed.
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

```swift
let vectors = try await client.embeddings(
    EmbeddingRequest(model: "qwen3-embedding", input: ["first", "second"]))

let ranked = try await client.rerank(
    RerankRequest(
        model: "qwen3-reranker",
        query: "annual membership fee",
        documents: ["Rates schedule", "Opening hours", "Card benefits"]))
print(ranked.ranking)
```

```typescript
const vectors = await client.embeddings.create({
  model: "qwen3-embedding",
  input: ["first", "second"],
});

const image = await client.images.generate({ model: "sd-turbo", prompt: "a lighthouse at dusk" });
await writeFile("out.png", image.data[0]!.toBytes());

const ranked = await client.rerank.create({
  model: "qwen3-reranker",
  query: "annual membership fee",
  documents: ["Rates schedule", "Opening hours", "Card benefits"],
});
console.log(ranked.ranking); // indices into the documents you sent, best first
```

**`raw_scores` asks for the logit instead of the probability.** A reranker's probabilities saturate
near 1.0 — 0.99 was measured for a document only loosely related to its query — and a saturated
probability cannot be calibrated while the logit behind it can. Not every engine has it; where it
does not, the request still succeeds and the field comes back named in
`X-Prometheus-Ignored-Parameters`, so sending it unconditionally is safe and being dropped is
discoverable rather than silent.

**Rerank scores the whole document set in one request.** Against a 60 RPM budget, scoring 50
candidates costs one unit rather than fifty. Each result's `index` points into the array **you**
sent, never into the results, which is what keeps a reordered result attributable to its input.

## Predict — the tasks OpenAI has no shape for

Three modalities route here and nowhere else: `classification`, `zero_shot` and `typed_decision`.
Every other endpoint is OpenAI-shaped because every task it serves has an OpenAI endpoint to be
shaped like. These do not.

```python
result = client.predict.create("sst2-clf", {"inputs": "El servicio ha sido excelente"})
result.value        # [{"label": "POSITIVE", "score": 0.9783}]  -- a LIST, not a dict

result = client.predict.create(
    "von-decide",
    {"inputs": "Me cobraron dos veces la misma factura",
     "parameters": {"candidate_labels": ["cargo duplicado", "cliente satisfecho"]}},
)
result.value["labels"][0]   # "cargo duplicado"
```

```go
result, err := client.Predict.Create(ctx, "sst2-clf",
	map[string]any{"inputs": "El servicio ha sido excelente"}, axonium.PredictOptions{})

var labels []struct {
	Label string  `json:"label"`
	Score float64 `json:"score"`
}
err = result.Into(&labels)
```

```rust
let result = client
    .predict(
        "sst2-clf",
        &serde_json::json!({"inputs": "El servicio ha sido excelente"}),
        &PredictOptions::default(),
    )
    .await?;
let labels: Vec<Label> = result.decode()?;
```

```swift
let result = try await client.predict(
    model: "sst2-clf", body: ["inputs": "El servicio ha sido excelente"])

// A top-level ARRAY from this engine, an object from the next one. Decode what you expect from
// the payload_schema the catalog gave you, not from the modality.
let labels: [[String: JSONValue]] = try result.decode()
```

```typescript
const result = await client.predict.create("sst2-clf", {
  inputs: "El servicio ha sido excelente",
});
result.value; // [{ label: "POSITIVE", score: 0.9783 }] -- an ARRAY, not an object

const decided = await client.predict.create("von-decide", {
  inputs: "Me cobraron dos veces la misma factura",
  parameters: { candidate_labels: ["cargo duplicado", "cliente satisfecho"] },
});
```

**The body goes to the engine verbatim and its answer comes back verbatim.** Inventing a body for
these would be the gateway deciding, on the engine's behalf, what the engine's API should look like.

**So the answer is handed back undecoded, and that is not defensive typing.** `sst2-clf` answers a
top-level **array**; `von-decide` and `laya-decide` answer objects. A client that modelled this as a
dictionary would report "this is not JSON" about valid JSON, for the first engine the platform
shipped on the route.

**Dispatch on `payload_schema`, not on `modality`.** `sst2-clf` and `von-decide` are both
classifiers and want different bodies, so there is no `classify(text)` here — a typed method would
promise a stability the endpoint does not offer. The catalog says which contract a model speaks:

| model | `modality` | `payload_schema` |
|---|---|---|
| `sst2-clf` | `classification` | `hf-inference.text-classification.v1` |
| `von-decide` | `zero_shot` | `hf-inference.zero-shot-classification.v1` |
| `laya-decide` | `typed_decision` | `typed-decision.v1` |
| `nli-tei` | `zero_shot` | `tei.predict.v1` |
| `emotions-tei` | `classification` | `tei.predict.v1` |

**The last two rows are why that table matters.** `von-decide` and `nli-tei` are both `zero_shot`
and answer *different things that both sum to 1*: the first normalises across the candidate labels
**you** supplied, the second across the **model's own** classes and has no notion of candidate
labels at all. Dispatch on `modality` and you read one as the other, silently.

`tei.predict.v1` also carries a trap worth knowing before you batch:

```text
inputs: "a text"                     → one flat list of {label, score}
inputs: ["premise", "hypothesis"]    → ONE PAIR, not a batch of two texts
inputs: ["a", "b", "c"]              → 422
inputs: [["a"], ["b"]]               → a batch of two single texts → two lists
```

**A batch is always a list of lists.** The obvious "send my N texts as an array" is the one form
that quietly returns a single wrong answer. It is also why the result is handed back undecoded: one
input returns a flat list and a batch returns a list of lists, from the same model and endpoint.

**What does not pass through is the policy.** The model still resolves, `inference:read` plus the
specific `model:<id>` scope is still required, a dead replica is still skipped, and the request is
still metered and still counts against a spend cap. All three modalities share **one** rate-limit
budget, named `predict` — so classification requests and typed decisions eat the same 60 RPM.

**A model that has an OpenAI endpoint is refused here** with `400 modality-mismatch`, the inverse of
every other handler's check. Without it the same model would be reachable two ways, with two billing
paths, and the one that billed correctly would be whichever you did not use.

## The catalog

```python
client.models.list()    # what this token may call
client.models.mine()    # the subset your token is scoped to, cached
```

```go
client.Models.List(ctx)   // what this token may call
client.Models.Mine(ctx)   // the subset your token is scoped to, cached
```

```rust
client.models().await?;        // what this token may call
client.models_mine().await?;   // the subset your token is scoped to, cached
```

```swift
try await client.models()       // what this token may call
try await client.modelsMine()   // the subset your token is scoped to, cached
```

```typescript
await client.models.list(); // what this token may call
await client.models.mine(); // the subset your token is scoped to, cached
```

**The catalog lists running instances intersected with your scopes**, measured 2026-10-05. A model
that is registered but stopped disappears exactly like one that does not exist, and like one your
token cannot call — three different facts behind one absence, and only an operator can tell them
apart. So an empty list is never evidence that the deployment has no models.

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

```go
row, err := client.Usage.Retrieve(ctx, completion.Meta.RequestID)
row.Usage.TotalTokens
row.CostUSD             // nil where no price is configured — not 0
row.TerminationReason
```

```rust
let row = client.usage(&completion.meta.request_id).await?;
row.usage.total_tokens;
row.cost_usd;           // None where no price is configured -- not 0.0
row.termination_reason;
```

```swift
let row = try await client.usage(requestID: completion.meta.requestID)
row.usage.totalTokens
row.costUSD             // nil where no price is configured -- not 0
row.terminationReason
```

```typescript
const row = await client.usage.get(completion.meta.requestId!);
row.usage.totalTokens;
row.costUsd; // undefined where no price is configured -- not 0
row.terminationReason;
```

This needs no `admin:read`. `cost_usd` is nullable on purpose: "nobody priced this" and "it cost
nothing" are different facts, and a column that cannot tell them apart reports `$0.00` for traffic
that was never free.

Next: [Failure, retries and idempotency](03-failure.md).
