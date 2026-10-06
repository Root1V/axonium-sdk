<!-- translated-from: 05-composed.md sha256:716b53a7c6 -->
# Operaciones compuestas

> **¿Y cuando una llamada no basta?**

Tres patrones que salen en toda integración real, y el detalle de cada uno que es fácil equivocar.

## Un bucle de uso de herramientas

El modelo pide una herramienta, tú la ejecutas, le devuelves el resultado. Lo que lo vuelve incómodo
en la mayoría de SDK es que la forma que *recibes* no es la forma que *envías*, así que todo bucle
lleva una conversión en medio. Aquí es el mismo tipo en las dos direcciones:

```python
messages = [{"role": "user", "content": "What is the weather in Lima?"}]

while True:
    completion = client.chat.completions.create(
        model="qwen3-0.6b", messages=messages, tools=TOOLS,
    )
    calls = completion.tool_calls
    if not calls:
        break

    messages.append(completion.choices[0].message)      # sent straight back
    for call in calls:
        result = run_tool(call.name, call.parse_arguments())
        messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

print(completion.content)
```

```go
for {
	completion, err := client.Chat.Create(ctx, axonium.ChatRequest{
		Model: "qwen3-0.6b", Messages: messages, Tools: tools,
	})
	if err != nil {
		return err
	}
	calls := completion.ToolCalls()
	if len(calls) == 0 {
		break
	}
	messages = append(messages, completion.Choices[0].Message)
	for _, call := range calls {
		args, err := call.ParseArguments()
		if err != nil {
			return err
		}
		messages = append(messages, axonium.Message{
			Role:       "tool",
			ToolCallID: call.ID,
			Content:    run(call.Function.Name, args),
		})
	}
}
```

```rust
loop {
    let completion = client
        .chat(&ChatRequest {
            model: "qwen3-0.6b".into(),
            messages: messages.clone(),
            tools: tools.clone(),
            ..Default::default()
        })
        .await?;

    let calls = completion.tool_calls();
    if calls.is_empty() {
        break;
    }
    messages.push(completion.choices[0].message.clone());
    for call in calls {
        let result = run(call.name(), call.parse_arguments()?);
        messages.push(Message {
            role: "tool".into(),
            tool_call_id: call.id.clone(),
            content: Some(result.into()),
            ..Default::default()
        });
    }
}
```

```swift
var messages: [Message] = [.user("What is the weather in Lima?")]

while true {
    var request = ChatRequest(model: "qwen3-0.6b", messages: messages)
    request.tools = tools
    let completion = try await client.chat(request)

    let calls = completion.choices.first?.message.toolCalls ?? []
    if calls.isEmpty { break }

    messages.append(completion.choices[0].message)      // sent straight back
    for call in calls {
        let result = run(call.name, try call.decodedArguments())
        messages.append(Message(role: "tool", content: .text(result), toolCallID: call.id))
    }
}
```

```typescript
import { decodedArguments } from "axonium";

const messages = [{ role: "user", content: "What is the weather in Lima?" }];

for (;;) {
  const completion = await client.chat.completions.create({
    model: "qwen3-0.6b",
    messages,
    tools: TOOLS,
  });

  const calls = completion.toolCalls;
  if (calls.length === 0) break;

  messages.push(completion.choices[0]!.message); // sent straight back
  for (const call of calls) {
    const result = await runTool(call.name, decodedArguments(call));
    messages.push({ role: "tool", tool_call_id: call.id, content: result });
  }
}
```

Dos cosas que conviene saber:

**`arguments` sigue siendo la cadena JSON del modelo.** Decodificarla con ansia significaría un
modelo de respuesta que revienta desde dentro cuando `max_tokens` parte una llamada por la mitad.
`parse_arguments()` la decodifica y falla con su propio error, dejando la cadena cruda accesible —
porque una llamada a herramienta truncada es algo que quieres *ver*, no algo que quieres que se
convierta en un objeto vacío. Un objeto vacío ejecutaría la herramienta sin argumentos, que es peor
que fallar.

**Las llamadas a herramientas en streaming llegan en fragmentos** que individualmente son JSON
inválido, indexados por `index`, con la identidad solo en el primero. Los SDK los reensamblan en
exactamente la forma no-streaming, así que el bucle de arriba es el mismo bucle hayas hecho streaming
o no.

## Recuperación: embeber, ordenar, responder

```python
query_vector = client.embeddings.create(model="qwen3-embedding", input=query)

candidates = vector_store.nearest(query_vector.data[0].embedding, k=50)

ranked = client.rerank.create(
    model="qwen3-reranker",
    query=query,
    documents=[c.text for c in candidates],
)
best = [candidates[r.index] for r in ranked.results[:5]]

answer = client.chat.completions.create(
    model="qwen3-0.6b",
    messages=[{"role": "user", "content": prompt_with(best, query)}],
)
```

```go
vector, err := client.Embeddings.Create(ctx, axonium.EmbeddingRequest{
	Model: "qwen3-embedding", Input: []string{query},
})
candidates := store.Nearest(vector.Data[0].Embedding, 50)

ranked, err := client.Rerank.Create(ctx, axonium.RerankRequest{
	Model: "qwen3-reranker", Query: query, Documents: texts(candidates),
})
best := pick(candidates, ranked.Ranking()[:5])

answer, err := client.Chat.Create(ctx, axonium.ChatRequest{
	Model:    "qwen3-0.6b",
	Messages: []axonium.Message{axonium.TextMessage("user", promptWith(best, query))},
})
```

```rust
let vector = client
    .embeddings(&EmbeddingRequest {
        model: "qwen3-embedding".into(),
        input: vec![query.clone()],
        ..Default::default()
    })
    .await?;
let candidates = store.nearest(&vector.data[0].embedding, 50);

let ranked = client
    .rerank(&RerankRequest {
        model: "qwen3-reranker".into(),
        query: query.clone(),
        documents: texts(&candidates),
        ..Default::default()
    })
    .await?;
let best = pick(&candidates, &ranked.ranking()[..5]);

let answer = client
    .chat(&ChatRequest {
        model: "qwen3-0.6b".into(),
        messages: vec![Message::text("user", &prompt_with(&best, &query))],
        ..Default::default()
    })
    .await?;
```

```swift
let vector = try await client.embeddings(
    EmbeddingRequest(model: "qwen3-embedding", input: [query]))
let candidates = store.nearest(vector.data[0].embedding, k: 50)

let ranked = try await client.rerank(
    RerankRequest(
        model: "qwen3-reranker", query: query, documents: candidates.map(\.text)))
let best = ranked.ranking.prefix(5).map { candidates[$0] }

let answer = try await client.chat(
    ChatRequest(model: "qwen3-0.6b", messages: [.user(promptWith(best, query))]))
```

```typescript
const queryVector = await client.embeddings.create({
  model: "qwen3-embedding",
  input: query,
});
const candidates = vectorStore.nearest(queryVector.data[0]!.embedding, 50);

const ranked = await client.rerank.create({
  model: "qwen3-reranker",
  query,
  documents: candidates.map((c) => c.text),
});
const best = ranked.ranking.slice(0, 5).map((index) => candidates[index]!);

const answer = await client.chat.completions.create({
  model: "qwen3-0.6b",
  messages: [{ role: "user", content: promptWith(best, query) }],
});
```

Tres llamadas, tres presupuestos de rate-limit **separados**: cada endpoint tiene el suyo, y la
respuesta dice cuál está reportando en `rate_limit.scope`. Así que esta tubería cuesta una unidad de
cada uno, no tres de uno, y el presupuesto que hay que vigilar es el del endpoint que vas a llamar:

```python
budget = client.rate_limits.get("embeddings")
if budget and budget.remaining_requests == 0:
    ...
```

```go
if budget := client.RateLimits()["embeddings"]; budget != nil &&
	budget.RemainingRequests != nil && *budget.RemainingRequests == 0 {
	// ...
}
```

```rust
if let Some(budget) = client.rate_limits().get("embeddings") {
    if budget.remaining_requests == Some(0) {
        // ...
    }
}
```

```swift
// No client-held map here: key the snapshot off the response you just got.
if vector.meta.rateLimit?.remainingRequests == 0 {
    // ...
}
```

```typescript
// Likewise -- `client.lastRateLimit` is the most recent reading from ANY endpoint, so for a
// specific budget read it off that endpoint's own response.
if (queryVector.meta.rateLimit?.remainingRequests === 0) {
  // ...
}
```

No leas `client.last_rate_limit` aquí. Después de la llamada de chat describe el presupuesto de
**chat**, y nada en los números lo dice.

El reranker puntúa los 50 candidatos en **una** petición. Hacer lo mismo por chat serían 50.

## Conciliar lo que te cobraron

```python
completion = client.chat.completions.create(model="qwen3-0.6b", messages=[...])

row = client.usage.retrieve(completion.meta.request_id)
row.usage.total_tokens
row.cost_usd
row.termination_reason      # "complete", or why it stopped early
```

```go
completion, err := client.Chat.Create(ctx, request)

row, err := client.Usage.Retrieve(ctx, completion.Meta.RequestID)
row.Usage.TotalTokens
row.CostUSD
row.TerminationReason       // "complete", or why it stopped early
```

```rust
let completion = client.chat(&request).await?;

let row = client.usage(&completion.meta.request_id).await?;
row.usage.total_tokens;
row.cost_usd;
row.termination_reason;     // "complete", or why it stopped early
```

```swift
let completion = try await client.chat(request)

let row = try await client.usage(requestID: completion.meta.requestID)
row.usage.totalTokens
row.costUSD
row.terminationReason       // "complete", or why it stopped early
```

```typescript
const completion = await client.chat.completions.create(request);

const row = await client.usage.get(completion.meta.requestId!);
row.usage.totalTokens;
row.costUsd;
row.terminationReason; // "complete", or why it stopped early
```

No hace falta `admin:read` — esto es por petición, y es tu petición.

Dos trampas:

**Una repetición no tiene fila de uso.** Si `meta.idempotent_replay` es cierto, `retrieve` sobre ese
`request_id` devuelve `404` — correctamente, porque repetir no alcanzó ningún modelo. Usa
`meta.idempotent_replay_of` en su lugar. Ver [Fallos](03-failure.md).

**`cost_usd` puede ser nulo, y nulo no es cero.** Es `None` donde no hay precio configurado. Si lo
guardas en una columna `NOT NULL DEFAULT 0`, una llamada sin tarificar y una llamada gratis acaban
siendo la misma fila, y un panel las suma en un total equivocado en la dirección que nadie comprueba.

## Lanzar llamadas en paralelo

Un cliente, muchas tareas. El token se pide una vez — el gestor usa double-checked locking, así que
una ráfaga de primeras llamadas produce una petición de token y no una por cada una.

```python
import asyncio
from axonium import AsyncAxonium

async def main():
    async with AsyncAxonium(client_id="...", client_secret="...") as client:
        results = await asyncio.gather(*(
            client.chat.completions.create(model="qwen3-0.6b",
                                           messages=[{"role": "user", "content": q}])
            for q in questions
        ))
```

```go
var wg sync.WaitGroup
results := make([]*axonium.ChatCompletion, len(questions))

for i, q := range questions {
	wg.Add(1)
	go func(i int, q string) {
		defer wg.Done()
		results[i], _ = client.Chat.Create(ctx, axonium.ChatRequest{
			Model:    "qwen3-0.6b",
			Messages: []axonium.Message{axonium.TextMessage("user", q)},
		})
	}(i, q)
}
wg.Wait()
```

```rust
let results = futures::future::join_all(questions.iter().map(|q| {
    client.chat(&ChatRequest {
        model: "qwen3-0.6b".into(),
        messages: vec![Message::text("user", q)],
        ..Default::default()
    })
}))
.await;
```

```swift
let results = try await withThrowingTaskGroup(of: ChatCompletion.self) { group in
    for question in questions {
        group.addTask {
            try await client.chat(
                ChatRequest(model: "qwen3-0.6b", messages: [.user(question)]))
        }
    }
    return try await group.reduce(into: []) { $0.append($1) }
}
```

```typescript
const results = await Promise.all(
  questions.map((question) =>
    client.chat.completions.create({
      model: "qwen3-0.6b",
      messages: [{ role: "user", content: question }],
    }),
  ),
);
```

La concurrencia la acota tu presupuesto de rate-limit, no el cliente. Sesenta peticiones por minuto
contra un endpoint son sesenta, por muchas tareas que arranques.

Siguiente: [Probar contra Axonium](06-testing.md).
