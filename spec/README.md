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

Each case in `cases/manifest.json` names an operation, the canned response to serve for it, and
what the SDK must produce. Each SDK mocks its own HTTP layer, replays every case, and asserts the
expectation. Because all three read the same manifest and the same fixture bytes, identical
behavior is enforced by construction rather than by three hand-written suites that drift apart.

A case's `expect.kind` selects how it is checked:

| `kind` | Meaning |
|---|---|
| `ok` | The response parses and every path in `fields` resolves to the stated value |
| `stream` | The stream assembles to `content` over `chunks` chunks, with the stated `usage` (`null` when none can be determined) |
| `stream_error` | The stream raises the SDK's stream-interrupted error, preserving `partial_content` |

`fields` paths are dotted, with integer segments indexing into lists (`choices.0.finish_reason`),
resolved against the SDK's own accessors where it has them (`content`, `usage.total_tokens`).

**The error taxonomy is not duplicated here.** `errors.json` is the source of truth for it, and
each SDK asserts its own error types against that file directly. The manifest covers what only
concrete wire data can express: response parsing, header parsing, and streaming behavior.

Each SDK should also assert that every case in the manifest is actually executed by one of its
runners. Without that check, a case using a `kind` an SDK does not implement is silently skipped —
looking covered while verifying nothing.

SSE fixtures are stored as literal wire bytes, including blank-line record separators, the
terminal `data: [DONE]` sentinel, and the in-band `{"error": ...}` mid-stream failure shape. They
are captures, not documents: reformatting one silently changes what every SDK is tested against.
The `spec-lint` workflow enforces this.

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
