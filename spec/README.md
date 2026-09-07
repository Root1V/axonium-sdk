# Contract specification

This directory is the shared source of truth for every language SDK in this repository. Behavior
that must be identical across Python, Go and Rust is defined here once, in language-neutral form,
rather than reimplemented three times from prose.

| Path | What it is |
|---|---|
| `prometheus-gateway.md` | Vendored copy of the platform team's integration guide — the API contract |
| `errors.json` | The error catalog: HTTP status × `type` suffix × retryability |
| `cases/manifest.json` | Contract-test case index, replayed by each SDK's test suite |
| `fixtures/` | Golden response bodies (`*.json`) and literal SSE wire captures (`*.sse`) |

## How the contract tests work

Each case in `cases/manifest.json` describes a request, the canned response to serve for it, and
what the SDK is expected to produce — a typed error class with a given retryability, or a success
with specific extracted fields. Each SDK mocks its own HTTP layer, replays every case, and asserts
the expectation.

Because all three SDKs read the same manifest and the same fixture bytes, identical behavior is
enforced by construction rather than by three hand-written suites that drift apart.

SSE fixtures are stored as literal wire bytes, including blank-line record separators, the
terminal `data: [DONE]` sentinel, and the in-band `{"error": ...}` mid-stream failure shape. Do not
reformat or prettify them.

## Platform-team clarifications

Answers to questions raised while implementing the Python SDK, confirmed by the Prometheus team
against the gateway source. Every SDK should follow these.

- **In-band stream errors** — today exactly one site emits one message, `{"error": "stream
  interrupted"}`, but that is implementation detail rather than contract. Detect a failed stream by
  the **presence of a top-level `error` key**, never by matching the string.
- **Token expiry** — anchor a token's lifetime to the server's own clock, not the client's. The
  auth-service sends a standard HTTP `Date` header on every response; `Date` and the token's `exp`
  claim are both server-side readings, so their difference is free of any clock skew between the
  client machine and the platform.
- **429 `Retry-After`** — the response header and the body's `retry_after` field are written from
  the same variable in the same call and cannot disagree. Reading either is correct; no
  precedence rule is needed.
- **503 `backend-unavailable`** — two causes with different handling. Circuit breaker open sets the
  `Retry-After` **header** (never a body field) from the real expected recovery time. A genuine
  connection failure supplies no wait value anywhere, so the backoff there is the SDK's decision:
  use a conservative default (1s, doubling) rather than treating it like the circuit-breaker case.
- **W3C `traceparent`** — never read by the gateway, in any mode. Under OTEL it is deliberately
  ignored and a new span is always started; in legacy mode only `X-Trace-ID` is consulted. SDKs
  must not assume outbound trace propagation works.

## Updating the vendored guide

`prometheus-gateway.md` is a copy, not the original. When the platform team revises the guide,
replace the file wholesale, then update `errors.json` and the affected cases in the same commit so
the fixtures never describe a contract that no longer exists.
