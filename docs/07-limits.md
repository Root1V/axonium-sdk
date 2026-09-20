# What it does not do

> **Where does this stop?**

The page that is easiest to skip writing and the one that saves the most time. If a limit here is
wrong for you, that is useful to find out now rather than three days in.

## It does not normalise responses

Inference responses are passed through close to verbatim. The SDKs model the fields the contract
guarantees, keep the rest reachable as raw, and do **not** reshape backend-specific output into a
vocabulary of their own.

This is deliberate. Backends are heterogeneous, and a normalising layer inside three separate
language SDKs is three implementations of the same opinion, drifting. Normalisation belongs above
the SDK, in whatever framework consumes the models, where there is one of it.

## It does not retry streams

Ever, and there is no flag. A retry after partial output has been delivered is a fresh billable
generation, not a resumption. The gateway does not retry them either.

If a stream fails, you get `StreamInterruptedError` carrying what arrived. Deciding whether to start
a new one is yours, because only you know whether the partial output was used.

## It does not run a circuit breaker

The gateway runs one per backend and reports `503 backend-unavailable` with a `Retry-After`
computed from real recovery time. A second breaker on the client would open on signals the server
already counted, using worse information.

What is here is smaller: a cooldown registry that respects `Retry-After` and fails fast locally
until it expires. If you want full `CLOSED`/`OPEN`/`HALF_OPEN`, put it above the SDK.

## It does not log prompts or completions

Not at any level, and there is no flag to turn it on. That is where personal data leaks, and a flag
for it is a flag someone sets during an incident and never unsets. The `Authorization` header is
never logged either.

Correlation is by `request_id`, `trace_id` and `instance_id`, which are on every response.

## It does not configure your logging

`NullHandler` in Python, no subscriber installed in Rust, an injected `slog.Logger` in Go. A library
that calls `basicConfig()` overwrites its host's configuration.

The consequence is real and worth stating: **if you have not attached a handler, you will not see
the SDK's logs** — including the line saying it is waiting out a `429`. That is why the wait is also
data on the response. See [Failure](03-failure.md).

## It does not read `.env`

Loading one is the application's decision. See [Configuration](04-configuration.md).

## It does not report metadata on failures

A call that waited 90 seconds across three attempts and then failed reports none of that: the
exception carries no `ResponseMeta`. Successes carry `waited_s` and `attempts`; failures do not.
This is a known gap on the roadmap, not a design decision.

## It does not propagate W3C trace context

No `traceparent` is sent by default. The gateway's documented behaviour is that a client-supplied
`X-Trace-ID` is ignored or honoured conditionally depending on deployment mode, and W3C propagation
is not documented at all. Sending a header that may be silently dropped produces traces with
invisible gaps, which is worse than no propagation.

OpenTelemetry spans are available behind an optional extra, off by default, spans only.

## It does not validate your prompts

No content filtering, no PII detection, no token counting before send. The previous generation of
this SDK did some of that and it belongs elsewhere: the SDK cannot know your policy, and a partial
implementation of one invites the belief that it is complete.

It does validate what the **contract** constrains: parameter ranges, message roles, idempotency key
length, and image URLs with an `http(s)` scheme — the last rejected with an explicit SSRF-policy
message rather than a generic validation error.

## It is not a framework

No agent loop, no workflow abstractions, no chain builders, no LangChain or LangGraph bridges. The
previous generation had them and they were the first thing to rot, because they encoded opinions
about orchestration that changed faster than the transport did.

This is a transport client. Build the loop you need; [Composed operations](05-composed.md) shows
what the pieces look like together.

Next: [Python API reference](08-reference.md).
