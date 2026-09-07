# axonium (Python)

Python SDK for the Prometheus Gateway inference API.

> **Status: in development.** The package skeleton is in place; the client is being implemented
> phase by phase. Not yet published to PyPI.

## Installation

```bash
pip install axonium
```

With OpenTelemetry span support:

```bash
pip install "axonium[otel]"
```

Requires Python 3.10+.

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
client = Axonium(otel_enabled=True)   # or AXONIUM_OTEL_ENABLED=true
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
