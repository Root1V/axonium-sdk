# Failure, retries and idempotency

> **When is it safe to try again?**

This is the page worth reading. Everything else here is convenience; this is the part that is
expensive to get right and costs real money to get wrong.

## The rule

**A retry is attempted only where the platform states that no generation occurred.**

That is a short sentence hiding the whole problem. An inference request that fails after the model
started producing tokens has already been billed. Retrying it does not resume anything — it queues
a second generation and you pay twice. So the SDKs do not retry on "it failed"; they retry on a
specific list of failures the gateway documents as having fast-failed before reaching a model:

| Retried | Why it is safe |
|---|---|
| `429 rate-limit-exceeded-requests` | Refused at the door |
| `503 backend-unavailable` | The circuit breaker was open; nothing was dispatched |
| `503 rate-limiting-unavailable` | The limiter's store was down; refused fail-closed |
| `503 usage-store-unavailable` | Refused before dispatch |

Everything else raises. A `502 upstream-error` is **not** retried by default: it means the
gateway's own attempts already failed, so a client retry is a fourth attempt at something that
failed three times. You can opt in (`retry_upstream_errors`), and even then it is capped at one
extra attempt regardless of `max_attempts`.

**A client-side timeout is never retried by default either**, and that one surprises people. The
backend is probably still generating. The error message says so rather than leaving you to work it
out.

### A stream fails in two ways, and only one is retried

The rule above decides this too, but a stream makes the two halves look alike when they are not.

**Rejected before the stream begins → retried.** The gateway opens the connection to the engine and
reads its status *before* the `200`/`text/event-stream` headers exist, so a refusal comes back as an
ordinary error response — the same status and body the non-streaming form of the endpoint returns.
Nothing was generated and nothing was billed, so reopening is a first generation rather than a
second, and it goes through the table above unchanged, `Retry-After` included. It is also the only
retry there is: **the gateway performs no internal retries on a streamed request**, so a `503
backend-unavailable` reaches you after one attempt rather than three. The backoff does not change;
the time you waited before seeing it does.

**Failed after the stream began → never retried.** Once one chunk exists the headers are committed,
so the failure arrives in band instead, as a chunk carrying `error`. Part of the answer was delivered
and part was billed, so a repeat is a fresh generation rather than a resumption. The SDK raises with
the partial text attached and lets you decide, because only you know what the partial output was used
for.

An `Idempotency-Key` does not change either half. On a stream it replays one the gateway *finished*
and whose delivery your connection dropped — never one the model itself broke.

> Until 2026-09-27 the first half could not be expressed at all: a stream rejected before it began
> arrived as a `200` whose body was nothing but `data: [DONE]`, indistinguishable from a legitimately
> empty answer. There was no visible rejection to retry. The platform now returns the engine's real
> status.

## Waiting

When the gateway sends `Retry-After`, the SDK uses it. It is server-supplied and authoritative: for
an open circuit breaker it is the real expected recovery time, which no local heuristic improves
on.

With one exception. **A wait longer than `max_backoff` is handed back rather than slept through**,
because blocking a caller for minutes inside one call is worse than telling them. The error carries
`retry_after`, so you can schedule the work yourself.

Without a `Retry-After`, exponential backoff with jitter — jitter so that callers recovering from
one outage do not resynchronise into a second one.

### A wait is not a hang

`Retry-After` on a `429` is seconds until the window resets, so it runs 0–60. An SDK that respects
it looks, from outside, like one slow call among fast ones. That has now been filed as a hang three
times by three different teams.

So the wait is reported twice. Once in the log, at `INFO` when it is long enough for a person to
notice — sub-second backoff stays at `DEBUG`, because the noise worry is frequent small retries,
not the rare long one. And once as **data on the response**:

```python
completion = client.chat.completions.create(model="qwen3-0.6b", messages=[...])

completion.meta.waited_s    # 36.0
completion.meta.attempts    # 2
```

```go
completion, err := client.Chat.Create(ctx, request)

completion.Meta.WaitedFor   // 36s
completion.Meta.Attempts    // 2
```

```rust
let completion = client.chat(&request).await?;

completion.meta.waited_for; // 36s
completion.meta.attempts;   // 2
```

```swift
let completion = try await client.chat(request)

completion.meta.waitedFor   // 36s
completion.meta.attempts    // 2
```

```typescript
const completion = await client.chat.completions.create(request);

completion.meta.waitedMs; // 36000
completion.meta.attempts; // 2
```

Use the second. A log line is invisible unless the application configured a handler for it — the
SDKs install a `NullHandler` and do not touch your logging — and a latency dashboard cannot read
one anyway.

> **Known limit, in four of the five.** A call that waited and then failed *anyway* reports none of
> this in Python, Go, Rust or Swift: the exception carries no response metadata. That is the call
> whose duration most needs explaining, and it is on the roadmap rather than done.
>
> **TypeScript answers it**, because its errors already carry `meta` — so the counters are stamped
> on before the error leaves, and `error.meta.waitedMs` says how long a failed call spent asleep:
>
> ```typescript
> catch (error) {
>   if (error instanceof APIError) {
>     log.warn("gave up", { attempts: error.meta.attempts, waitedMs: error.meta.waitedMs });
>   }
> }
> ```

## Idempotency

An `Idempotency-Key` changes what is safe, and it is the only thing that makes a timed-out request
retryable:

```python
completion = client.chat.completions.create(
    model="qwen3-0.6b",
    messages=[{"role": "user", "content": "..."}],
    idempotency_key="order-4417-summary",
)
```

```go
completion, err := client.Chat.Create(ctx, axonium.ChatRequest{
	Model:          "qwen3-0.6b",
	Messages:       []axonium.Message{axonium.TextMessage("user", "...")},
	IdempotencyKey: "order-4417-summary",
})
```

```rust
let completion = client
    .chat(&ChatRequest {
        model: "qwen3-0.6b".into(),
        messages: vec![Message::text("user", "...")],
        idempotency_key: "order-4417-summary".into(),
        ..Default::default()
    })
    .await?;
```

```swift
let completion = try await client.chat(
    ChatRequest(model: "qwen3-0.6b", messages: [.user("...")]),
    idempotencyKey: "order-4417-summary")
```

```typescript
const completion = await client.chat.completions.create(
  { model: "qwen3-0.6b", messages: [{ role: "user", content: "..." }] },
  { idempotencyKey: "order-4417-summary" },
);
```

With a key, a repeat returns the **stored** result: no model is reached, no usage is recorded,
nothing counts against the spend cap. So the retry costs a round trip instead of a generation, and
the timeout objection disappears. Without a key the old rule stands, because nothing about the
danger has changed.

**And the case that makes a key worth sending on every long call**: a retry while the first request
is *still running* is not a failure and not a second generation. The gateway answers
`409 idempotency-in-progress`, which is the **only `409` in the catalog marked retryable**, so the
SDK waits it out on your behalf and returns the stored result when the original finishes.

| on retry | the gateway answers | what the SDK does |
|---|---|---|
| the first one **finished** | the stored result | returns it — no model reached, no usage recorded, nothing against the spend cap |
| the first one **is still running** | `409 idempotency-in-progress` | retries it, until the original finishes |
| same key, a fingerprint that **does not match** | `409 idempotency-key-reuse` | raises; see below — a different body is the usual cause, not the only one |

So with a key a retry is not a gamble: it either collects the result or waits for it. Two edges worth
knowing:

Keys are capped at 255 characters and the SDK checks that before sending — the gateway reports an
over-length key as a *conflict*, which points the investigation in the wrong direction.

**`409 idempotency-key-reuse` does not always mean you reused the key.** The fingerprint is taken
over the *gateway's* parsed request model **including its defaults**, not over the bytes you sent, so
an additive change to that model invalidates every key stored before it. Veritium measured this on
2026-10-08: `PRM-235` added two optional fields defaulting to null, and from that deploy a client
resending a byte-identical request got this error. The contract's own remedy — *resend the original
request unchanged* — is exactly what fails.

So **do not mint a fresh key reflexively.** Check whether the body genuinely changed:

- **It did** — a fresh key is correct, and the error was doing its job.
- **It did not** — a fresh key buys a **second billable generation** for work the first request may
  already have finished, which is the precise harm a key exists to prevent. Wait out the window.

No SDK retries this error, and none of them recovers from it by minting a new key, deliberately:
in the genuine-misuse case that would double-bill in silence. The platform has been asked to
fingerprint what the client sent (`VRT-PRM-004`).

`409 idempotency-response-not-retained` means the original succeeded but its response exceeded the
**1 MiB** retention cap, so it was never stored. It was generated and billed; there is simply nothing
to replay. Long generations reach this.

### Telling a replay apart from a generation

```python
if completion.meta.idempotent_replay:
    billed = completion.meta.idempotent_replay_of
```

```go
if completion.Meta.IdempotentReplay {
	billed := completion.Meta.IdempotentReplayOf
	_ = billed
}
```

```rust
if completion.meta.idempotent_replay {
    let billed = &completion.meta.idempotent_replay_of;
}
```

```swift
if completion.meta.idempotentReplay {
    let billed = completion.meta.idempotentReplayOf
}
```

```typescript
if (completion.meta.idempotentReplay) {
  const billed = completion.meta.idempotentReplayOf;
}
```

A replay carries its **own** `request_id`, and that id has no usage row — looking it up returns
`404`, correctly, because replaying reached no model and was not billed.
`idempotent_replay_of` is the id of the generation that *was* billed, and the only route from the
response you received to the charge it corresponds to.

If you reconcile usage from response ids, you need this field. Without it an audit starting from a
replay's id finds nothing **and cannot tell why**.

### A key is not known to be released by a failure

Everything above describes what a key can **replay**. It deliberately says nothing about what
happens to the key when the first request *fails*, because we do not know, and the difference
matters to anyone whose key is derived rather than random.

A consumer running deterministic keys — `(run_id, step_id, body-fingerprint)`, so that resuming
reproduces instead of paying twice — reported that a step which failed once kept returning the
stored error for the whole 24-hour window, in milliseconds, so their retries never reached a model
again. We could not reproduce it against our deployment with the failures we can produce
(`400 unknown-instance`): the key was still usable afterwards, and a second call with a different
body succeeded rather than being refused. Their case was a `5xx`, which we cannot force.

So the honest statement is: **whether a failed request holds its key is undefined here**, it is
decided by the gateway and not by this SDK, and it is being asked. Until it is answered, treat a
derived key whose request failed as possibly unusable for the rest of the window, and note that a
stored error arrives without `Idempotent-Replay`, so it is indistinguishable from a fresh one.

## Cooldowns

The gateway runs its own circuit breaker per backend, so the SDKs do not add a second one — it
would open on signals the server already counted, with worse information.

What is left uncovered is what the gateway cannot report: the gateway itself being unreachable. For
that there is a small cooldown registry keyed by `(host, model)`. When a `503` arrives with a
`Retry-After`, further calls to that model fail locally until it expires, rather than spending a
request to be told the same thing. The error says so explicitly, so a fast local failure is not
mistaken for a real gateway answer.

The cooldown is scoped to the model, not the host: one unavailable backend does not stop the rest.

## Catching things

```python
from axonium import RateLimitError, SpendCapExceededError, APIError

try:
    completion = client.chat.completions.create(model="qwen3-0.6b", messages=[...])
except SpendCapExceededError:
    ...                      # not retryable, ever; a human decision
except RateLimitError as error:
    schedule_in(error.retry_after)
except APIError as error:
    log.warning("gateway said no", extra={"request_id": error.request_id})
    raise
```

```go
var apiErr *axonium.APIError
if errors.As(err, &apiErr) && apiErr.Retryable() {
	// ...
}
if errors.Is(err, axonium.ErrRateLimit) {
	// ...
}
```

```rust
match client.chat(&request).await {
    Err(Error::Api(api)) if api.kind == ErrorKind::SpendCapExceeded => { /* ... */ }
    Err(Error::Api(api)) if api.retryable() => { /* ... */ }
    other => other?,
}
```

```swift
do {
    let completion = try await client.chat(request)
} catch let AxoniumError.api(error) where error.kind == .spendCapExceeded {
    // not retryable, ever; a human decision
} catch let AxoniumError.api(error) where error.isRetryable {
    // ...
}
```

```typescript
import { RateLimitError, SpendCapExceededError, APIError } from "axonium";

try {
  const completion = await client.chat.completions.create({ model: "qwen3-0.6b", messages });
} catch (error) {
  if (error instanceof SpendCapExceededError) throw error; // not retryable, ever
  if (error instanceof RateLimitError) scheduleIn(error.retryAfter);
  else if (error instanceof APIError) log.warn("gateway said no", { requestId: error.requestId });
  else throw error;
}
```

Every error carries `request_id`, and `trace_id` too on a current deployment. The rate-limit
envelope used to omit `trace_id`; since guide `2026-09-19b` that envelope is a strict superset of
the standard one, so both ids are there. The SDKs still model `trace_id` as optional, because a
deployment predating that fix omits it. Those two ids are what
a platform team needs; an error report without them is a description of a feeling.

Next: [Configuration and transport](04-configuration.md).
