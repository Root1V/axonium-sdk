# Concepts

> **What are the pieces, and what is each one for?**

Six things. You create one of them; the rest you read.

## The client

The one object you construct. It holds the HTTP connection pool, the token manager, and the
per-resource namespaces. It is safe to share across threads, goroutines or tasks, and it is meant
to be long-lived — not per request, because a fresh client throws away the cached token and opens
a new pool.

Python and Rust have an async client and a sync one with identical surfaces; Go has one client and
`context.Context`.

**An async client belongs to the event loop it was built in, so "one per process" is only right
for a process with one loop.** This page used to say per process without that qualification, and a
service with an API and a worker pool usually has more than one loop. Measured on Python 3.13,
reusing one `AsyncAxonium` across two `asyncio.run()` calls:

```
loop 1: ok
loop 2: RuntimeError: Event loop is closed
```

The pool keeps the first loop's resources, and the error comes from inside asyncio, so it does not
name the cause. Build the client inside the loop that will use it — a FastAPI `lifespan`, a worker's
startup — keep it for that loop's lifetime, and close it at shutdown (`await client.aclose()`, or
`async with`). The sync client has no such constraint.

**What it does not do:** it does not retry streams, it does not read `.env` files, and it does not
configure your logging.

## Resources

Namespaces on the client, one per gateway endpoint:

| Resource | What it asks for |
|---|---|
| `models` | The catalog, and the subset your token is scoped to |
| `chat` | Completions, streaming or not |
| `embeddings` | Vectors |
| `images` | Generated images |
| `rerank` | A query scored against N documents, in one request |
| `usage` | What a single request of yours cost, and why it stopped |

## The token manager

Invisible in normal use. It exchanges your `client_id` and `client_secret` for an access token,
caches it, and refreshes it **before** it expires — at 80% of the stated lifetime, or with 30
seconds left, whichever comes first.

Three details worth knowing:

- The expiry is computed from a **monotonic clock read taken before the request is sent**, so a
  slow token response makes the SDK refresh early rather than late.
- `expires_in` is always read from the response. The SDK never assumes a lifetime.
- The granted `scope` is read back from the response, never assumed to be what was asked for.

**What it does not do:** it does not refresh a token it never fetched, and it does not hide a
`401`. One reactive retry, then the error is yours.

## Response metadata

Every response carries a `meta` alongside the payload — on successes, not only failures, because
correlating a slow call that worked matters as much as correlating one that did not.

```python
completion = client.chat.completions.create(model="qwen3-0.6b", messages=[...])

completion.meta.request_id     # take this to the platform team
completion.meta.instance_id    # which instance served it
completion.meta.rate_limit     # the budget as of this response
completion.meta.waited_s       # seconds this SDK spent deliberately asleep
completion.meta.attempts       # how many HTTP attempts produced this
```

```go
completion.Meta.RequestID
completion.Meta.InstanceID
completion.Meta.RateLimit
completion.Meta.WaitedFor
completion.Meta.Attempts
```

```rust
completion.meta.request_id;
completion.meta.instance_id;
completion.meta.rate_limit;
completion.meta.waited_for;
completion.meta.attempts;
```

```swift
completion.meta.requestID     // take this to the platform team
completion.meta.instanceID    // which instance served it
completion.meta.rateLimit     // the budget as of this response
completion.meta.waitedFor     // seconds this SDK spent deliberately asleep
completion.meta.attempts      // how many HTTP attempts produced this
```

```typescript
completion.meta.requestId; // take this to the platform team
completion.meta.instanceId; // which instance served it
completion.meta.rateLimit; // the budget as of this response
completion.meta.waitedMs; // milliseconds this SDK spent deliberately asleep
completion.meta.attempts; // how many HTTP attempts produced this
```

`waited_s` exists because a respected `Retry-After` of 0–60 seconds looks from the outside like one
slow call among fast ones. Three separate teams reported exactly that as a hang. The SDK does log
it, but a log line is invisible by default and a latency metric cannot read one — so the number
travels on the answer. It is deliberately **excluded** from any duration the SDK reports: subtract
it from your own wall clock to get what the platform actually spent.

## The rate-limit snapshot

`meta.rate_limit` carries the six `X-RateLimit-*` counters and, importantly, a `scope` naming
**which budget** they describe. The endpoints hold separate budgets, so a `remaining_requests` read
after a chat call says nothing about your embeddings budget.

The set of scope names is **read from the header and not listed here**. This page used to name
three of them; five SDKs each kept their own list in a doc comment, and they had already drifted —
one said `chat` where the header says `chat_completions`, which is a name you would key a map by and
never match.

The window is worth designing against, though, and it is not a sliding one: the budget is a **fixed
60-second bucket aligned to the wall clock**, and the whole allowance returns at second 0 of each
minute. So a burst can straddle a boundary and pass where the same burst seconds earlier is refused.
Pace against the remaining count, never against a rate you assumed.

```python
client.last_rate_limit          # what the most recent call reported
client.rate_limits["embeddings"]  # the most recent reading for that budget
```

```go
client.LastRateLimit()               // what the most recent call reported
client.RateLimits()["embeddings"]    // the most recent reading for that budget
```

```rust
client.last_rate_limit();                  // what the most recent call reported
client.rate_limits().get("embeddings");    // the most recent reading for that budget
```

```swift
// No client-held map yet: read the budget off the response you just got.
completion.meta.rateLimit?.scope              // which budget these numbers describe
completion.meta.rateLimit?.remainingRequests  // what is left of it
```

```typescript
client.lastRateLimit; // what the most recent call reported
completion.meta.rateLimit; // the budget as of this response, and the one to key yourself
```

Read `client.rate_limits`, not `last_rate_limit`, when the question is "how much of budget X is
left". The distinction is not pedantry: before `scope` existed, a suggestion pipeline touching
three endpoints in a row left `last_rate_limit` describing whichever answered last, with nothing in
the numbers saying so.

**The per-scope map exists in Python, Go and Rust only.** TypeScript has `lastRateLimit` and Swift
has neither — in both, `meta.rate_limit` on each response carries the same numbers with the `scope`
attached, so keying them yourself is three lines and is what the map does. Said here rather than
left as a tab that quietly shows something different.

**What it does not do:** the token counters are the gateway's post-hoc accounting, not a
reservation. They are a strong signal, not a guarantee you will not see a `429`.

## Errors

One class per row of the gateway's error catalog, all descending from a common root, split by a
line that matters:

```
AxoniumError
├── ConfigurationError      you gave the SDK something unusable
├── TransportError          the request never produced a response
├── APIError                the gateway answered, and said no
│   ├── RateLimitError, SpendCapExceededError, UnknownModelError, …
└── OAuthError              the token endpoint said no, in RFC 6749 shape
```

`OAuthError` deliberately does **not** inherit from `APIError`. They are different envelopes with
different semantics, and collapsing them is how a 502 HTML proxy page once got typed as "your
credentials are wrong".

An unrecognised error type falls back to a class chosen by HTTP status rather than raising — the
catalog grows, and an SDK that breaks on a new error type is worse than one that types it loosely.

Next: [Making calls](02-calls.md).
