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

## Development

```bash
cd python
uv sync
uv run pytest
uv run ruff check
uv run mypy src/
```
