# Composed operations

> **What about when one call is not enough?**

Three patterns that come up in every real integration, and the detail in each that is easy to get
wrong.

## A tool-use loop

The model asks for a tool, you run it, you send the result back. What makes this awkward in most
SDKs is that the shape you *receive* is not the shape you *send*, so every loop has a conversion in
the middle. Here it is the same type in both directions:

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

Two things to know:

**`arguments` stays the model's JSON string.** Decoding it eagerly would mean a response model that
blows up from the inside when `max_tokens` cuts a call in half. `parse_arguments()` decodes it and
fails with its own error, leaving the raw string reachable — because a truncated tool call is a
thing you want to *see*, not a thing you want to have become an empty object. An empty object would
run the tool with no arguments, which is worse than failing.

**Streamed tool calls arrive in fragments** that are individually invalid JSON, keyed by `index`,
with the identity only in the first. The SDKs reassemble them into exactly the non-streaming shape,
so the loop above is the same loop whether you streamed or not.

## Retrieval: embed, rank, answer

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

Three calls, three **separate** rate-limit budgets — `embeddings`, `rerank` and `chat_completions`
each hold their own. So this pipeline costs one unit from each, not three from one, and the budget
you need to watch is the one for the endpoint you are about to call:

```python
budget = client.rate_limits.get("embeddings")
if budget and budget.remaining_requests == 0:
    ...
```

Do not read `client.last_rate_limit` here. After the chat call it describes the **chat** budget,
and nothing in the numbers says so.

The reranker scores all 50 candidates in **one** request. Doing the same through chat would be 50.

## Reconciling what you were charged

```python
completion = client.chat.completions.create(model="qwen3-0.6b", messages=[...])

row = client.usage.retrieve(completion.meta.request_id)
row.usage.total_tokens
row.cost_usd
row.termination_reason      # "complete", or why it stopped early
```

No `admin:read` needed — this is per-request, and it is your request.

Two traps:

**A replay has no usage row.** If `meta.idempotent_replay` is true, `retrieve` on that
`request_id` returns `404` — correctly, since replaying reached no model. Use
`meta.idempotent_replay_of` instead. See [Failure](03-failure.md).

**`cost_usd` can be null, and null is not zero.** It is `None` where no price is configured. If you
store it in a column that is `NOT NULL DEFAULT 0`, an unpriced call and a free call become the same
row, and a dashboard sums them into a total that is wrong in the direction nobody checks.

## Running calls concurrently

One client, many tasks. The token is fetched once — the manager uses double-checked locking, so a
burst of first calls produces one token request rather than one each.

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

Concurrency is bounded by your rate-limit budget, not by the client. Sixty requests a minute
against one endpoint is sixty, however many tasks you start.

Next: [Testing against Axonium](06-testing.md).
