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

## Updating the vendored guide

`prometheus-gateway.md` is a copy, not the original. When the platform team revises the guide,
replace the file wholesale, then update `errors.json` and the affected cases in the same commit so
the fixtures never describe a contract that no longer exists.
