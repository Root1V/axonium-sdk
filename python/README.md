# axonium (Python)

Python SDK for the Prometheus Gateway inference API.

> **Status: release candidate.** The client is feature-complete against the current gateway
> contract. Not yet published to PyPI.

## Installation

```bash
pip install axonium
```

With OpenTelemetry span support:

```bash
pip install "axonium[otel]"
```

Requires Python 3.10+.

## Quick start

```python
from axonium import Axonium

with Axonium() as client:
    print(client.models.mine().ids)  # what this token can actually call

    completion = client.chat.completions.create(
        model="llama3-8b-q4",
        messages=[{"role": "user", "content": "Hello"}],
    )
    print(completion.content)
```

Streaming is a separate method, because it needs a different scope and is never retried
automatically:

```python
with client.chat.completions.stream(model="llama3-8b-q4", messages=messages) as stream:
    for chunk in stream:
        print(chunk.content or "", end="", flush=True)
    print(stream.usage())
```

`AsyncAxonium` mirrors the whole surface — same names, same behavior, with `await`, `async with`
and `async for`.

### Reasoning models

A reasoning model streams its chain of thought before any answer token, and keeps it in a field
separate from the answer. `content` is therefore empty until it stops thinking — with a small
`max_tokens` it can stay empty, and `finish_reason` will be `"length"`. Both are exposed, and
neither is inferred from the other:

```python
if completion.content:
    print(completion.content)
elif completion.reasoning:
    print(f"still thinking: {completion.reasoning}")  # raise max_tokens

# Streaming: a chunk carries one or the other, so progress can show which phase it is in.
for chunk in stream:
    print(chunk.reasoning or chunk.content or "", end="")
print(stream.reasoning, stream.content)
```

Runnable examples are in [`examples/`](examples/).

## What the client does for you

**Authentication.** Tokens are obtained with OAuth2 `client_credentials` and refreshed *ahead* of
expiry, so a request never fails just to discover its token died. The lifetime comes from the
server — its `Date` header and the token's `exp` claim are both server-side readings, so no clock
skew between your machine and the platform can shorten or extend it. Concurrent callers share one
refresh rather than each triggering their own.

**Typed errors.** Every gateway error maps to its own exception class, so you branch on the type
rather than string-matching a human-readable message. The token endpoint's errors are a separate
branch of the hierarchy, since their shape and meaning differ — `except APIError` will not swallow
an authentication failure.

**Retries that will not double-bill you.** This API has no idempotency mechanism: a retried
generation is a new billable one, not a replay. So retries happen only where the platform tells us
no generation occurred — a rate limit, or a circuit breaker that fast-failed without reaching a
model. A `502` may have reached one, so retrying it is opt-in. Client-side timeouts are never
retried, because the backend is probably still working. A server-supplied `Retry-After` is honored,
but capped: a wait longer than `max_backoff` is handed back to you rather than slept through.

**Request validation before the wire.** The gateway silently drops fields it does not accept, so
the SDK warns by name about every one it removes instead of letting you believe a parameter took
effect. Remote image URLs are rejected client-side, with the SSRF reason spelled out.

**Optional modality preflight.** The gateway's modality check is one-directional: calling
`/v1/embeddings` with a text model is rejected, but calling `/v1/chat/completions` with an
*embedding* model is not — it returns `200` with degenerate output that you pay for. Enable the
check to catch that, and typos, before the request is sent:

```python
client = Axonium(verify_modality=True)  # or AXONIUM_VERIFY_MODALITY=true
```

Off by default because it costs one catalog request per client, and the SDK otherwise makes no
request you did not ask for. If you already call `models.list()`, the result is reused and the
check is free. If the catalog cannot be loaded the check is skipped rather than failing your
request — a guard rail should not become a new way for inference to break.

## Credential modes

Two modes, permanent and mutually exclusive. Which one you use follows from how the SDK is
embedded, not from preference.

**Autonomous** — the SDK mints and refreshes its own tokens. For development, notebooks and tests,
where there is no host to ask.

```python
client = Axonium(client_id=..., client_secret=...)  # or from the environment
```

**Governed** — a host that already owns the credential supplies tokens, and the SDK never holds a
secret. The provider is the sole authority: it owns caching, refresh and rotation, and Axonium does
no refresh-ahead of its own, because two caches for one token is how a client ends up sending a
token its owner already retired.

```python
def tokens(
    rejected: str | None,
) -> str: ...  # `rejected` is the token the gateway just refused, or None


client = Axonium(token_provider=tokens)
```

**The rejected token comes back, not a flag.** With a boolean a provider cannot tell whether two
concurrent refreshes concern the same dead token or different ones, so it must mint twice or guess
with a time window. Given the token, the answer is exact — if what it holds already differs, it
refreshed already.

`AsyncAxonium` requires an async provider: a blocking token fetch would stall the event loop, so
the mismatch is reported rather than tolerated.

Asking for both modes by name is refused. Credentials that merely happen to be in the environment
are not — the explicit provider wins, and the credentials are **discarded** with a warning, so the
secret really does leave the process rather than sitting unused.

## Configuration

The SDK never hardcodes a host, port, or certificate — every deployment supplies its own. Settings
resolve in this order, first match wins:

1. Per-call argument
2. Constructor argument
3. Environment variable
4. `ConfigurationError` naming the missing setting and its environment variable

| Environment variable | Purpose |
|---|---|
| `AXONIUM_AUTH_BASE_URL` | auth-service base URL (OAuth2 token endpoint) |
| `AXONIUM_GATEWAY_BASE_URL` | gateway base URL (`/v1/` inference API) |
| `AXONIUM_CLIENT_ID` | OAuth2 client ID issued by the platform operator |
| `AXONIUM_CLIENT_SECRET` | OAuth2 client secret |
| `AXONIUM_SCOPE` | Optional space-separated scope request |
| `AXONIUM_CA_BUNDLE` | Path to a CA bundle, for deployments using a self-signed certificate |

The SDK does not read `.env` files. Loading them is the application's responsibility (for example
with `python-dotenv`), and because settings resolve when the client is constructed rather than at
import time, load order does not silently change behavior.

## Observability

The platform owns tracing. This SDK provides only the complementary piece: enough for your traces
and logs to line up with the platform's, and nothing that duplicates what it already records.

**Correlation IDs on every response.** `X-Request-ID` and `X-Trace-ID` are parsed onto
`response.meta` for successes as well as failures, and onto every raised error. Correlating a slow
but successful call matters as much as correlating a failed one.

**Rate-limit visibility.** `client.last_rate_limit` and `response.meta.rate_limit` expose the
`X-RateLimit-*` budget from every response that carries one, so you can slow down before a `429`
rather than only reacting to one. The token figures are the gateway's post-hoc accounting — a
strong signal, not a guarantee.

**Structured logging.** The SDK logs under the `axonium` logger with a `NullHandler` attached, so
it stays silent until your application configures logging. Records carry `request_id`, `trace_id`,
`model`, `status`, `duration_ms` and `attempt`.

Prompts, completions and credentials are **never** logged, and there is no option to enable it.
Anything you need to log about the content, you already have at the call site.

**OpenTelemetry spans** are available behind an extra and off by default:

```python
client = Axonium(otel_enabled=True)  # or AXONIUM_OTEL_ENABLED=true
```

Spans only — exporters and providers are your application's to configure. Trace context is not
propagated outbound, because the gateway never reads `traceparent` in any mode; correlation runs
inbound instead, via the IDs above.

**Scope diagnostics.** A `403` is matched against the scopes your token was actually granted, so
the error says what is missing rather than just that access was refused:

```
Forbidden Scope check: the token holds inference:read but not inference:stream;
streaming needs its own scope and holding one does not grant the other. request_id=...
```

## Development

```bash
cd python
uv sync
uv run pytest
uv run ruff check
uv run mypy src/
```
