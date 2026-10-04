# Axonium for TypeScript

The Axonium SDK for the Prometheus inference platform, for Node, Bun, Deno and edge runtimes.

> **Status: `0.1.0`, ready to publish and not yet published.** Every resource, streaming, vision, tool
> calling and structured output; **49 of 49** shared contract cases; 77 unit tests and 16 integration
> tests against a live gateway; five runnable [examples](examples/) and an
> [API reference](docs/api.md). **9.5 KB** minified and gzipped, against a 50 KB budget.
>
> Not here yet: OpenTelemetry and a logging hook (`0.2.0`), `usage.export` (in no SDK of this family),
> and `X-Prometheus-Ignored-Parameters` on a success (held until all five SDKs agree on the shape). Bun,
> Deno and edge are **untested** — only Node 20, 22 and 24 are in CI, and claiming the rest because this
> only uses `fetch` would be the kind of unmeasured assertion this repository keeps catching.
>
> Requested in
> `apeiron_axonium_prometheus/solicitud-axonium-sdk-typescript.md`; see that channel for scope and
> dates.

```ts
import { Transport, resolveConfig } from "axonium";

const transport = new Transport(resolveConfig()); // reads AXONIUM_GATEWAY_BASE_URL, …

const catalog = await transport.sendJSON("GET", "/v1/models", {});
const answer = await transport.sendJSON("POST", "/v1/chat/completions", {
  model: "qwen3-0.6b",
  body: { model: "qwen3-0.6b", messages: [{ role: "user", content: "hola" }] },
});
```

`Transport` is the layer the resource methods will sit on; it is exported because it is useful on its
own and because nothing about it should be a surprise later.

## What is decided, and why

**Zero runtime dependencies, and a test that says so.** `fetch` is the platform's. A transitive
dependency is how a 50 KB budget becomes 400 KB and how an edge build breaks, and both get discovered
by the consumer rather than here — so `test/package.test.ts` fails if `dependencies` is ever
non-empty, and if anything under `src/` imports a `node:` module.

**`fetch` is injected, not patched.** `options.fetch` exists so the contract corpus can be replayed
against a function. That is why this package has no HTTP-mocking dev dependency, and a test refuses
one: with a mocking library, what the suite exercises stops being the real transport.

**The source is erasable TypeScript.** `erasableSyntaxOnly` is on, so no parameter properties, enums
or decorators — nothing that compiles to code. The reason is concrete rather than stylistic: Node
runs this package's own `.ts` with `--experimental-strip-types`, so the whole suite needs no build
step and no test runner. Writing one parameter property is what revealed this, and a compiler flag is
cheaper than remembering.

**A credential is refused where this cannot establish it is on a server.** Not via
`typeof window !== "undefined"` — jsdom defines `window`, so that fires in anyone's vitest suite, and
a guard with false positives in CI is a guard somebody disables. It looks for positive evidence of a
server runtime instead, so an environment nobody anticipated reads as unknown rather than as safe.

The rule it enforces is about **whose** credential it is, not where the code runs. A `client_id` is
the principal that model grants and every usage row are keyed to, so an integrator's credential must
never reach a machine its users control — while an end client's **own** credential may live on their
own device, which the platform supports explicitly. A bundle cannot tell those apart, so the default
refuses and `allowInsecureCredential` is how a caller states which case theirs is.

**`request` defaults to 600 s, deliberately.** The gateway allows its backends 600 s and some image
backends take 2–8 minutes. A client timeout shorter than the server's, combined with a retry, is a
known failure mode: the backend keeps computing and the retry queues a second expensive generation.
**An edge runtime cannot wait that long** — that is a real conflict rather than a tuning question, and
a deployment that cannot afford it should say so instead of meeting it as a truncated request.

## What the transport does

**Retries only what the contract says reached no model.** This API deduplicates nothing server-side,
so a retried generation is a _new billable one_ rather than a replay — which makes an over-eager
policy expensive rather than merely noisy. Three attempts, 1s base, 60s cap, full jitter. An
`idempotencyKey` is what turns a repeat into a replay, and every call accepts one.

**A client timeout is never retried.** The backend is probably still generating, so a retry queues a
second expensive generation on top of the first rather than resuming it.

**`Retry-After` is honoured verbatim**, because the gateway computes it from the real reset time or
the breaker's expected recovery. It is still capped: a wait longer than `maxBackoff` is handed to the
caller rather than slept through, since blocking for ten minutes is a scheduling decision that belongs
to the application.

**Cooldowns are keyed by model, not by gateway.** `backend-unavailable` means every replica of _that_
model is out while others on the same gateway keep serving.

**A 401 refreshes the token once, then gives up.** Bounded deliberately: the condition compares the
rejected token with the one just used, which is false again after every refresh, so without a counter
a gateway answering 401 to everything loops forever.

**Correlation ids are read from the body and then the headers.** The fallback is load-bearing: a body
that is not an envelope at all still arrives — an HTML page from a proxy that never reached the
gateway — and there the ids exist only in the headers, if anywhere.

## The error taxonomy

One class per catalogued `type` suffix, dispatched on the **last path segment** of the problem-details
`type` URI. An unknown suffix falls back by status rather than throwing: the catalogue grows, and an
error documented on a Tuesday must not become a parse failure in a version already published.

```ts
try {
  /* … */
} catch (err) {
  if (err instanceof ForbiddenError) console.log(err.detail, err.meta.requestId);
}
```

Two deliberate shapes:

- **`OAuthError` does not extend `APIError`.** The token endpoint answers RFC 6749
  `{error, error_description}`, not problem+json, and the remedies differ: an inference failure may be
  worth retrying with the same credential, a credential failure never is. Both extend `AxoniumError`,
  so one `catch` still covers the package.
- **`PredictBackendRejectedError.retryable` is derived from the status**, because the pass-through
  route keeps the _engine's_ status: one suffix covers a `422` that will never succeed and a `429`
  that will.

## Documentation

- **[`docs/api.md`](docs/api.md)** — every export, including all 34 error classes against their `type`
  suffix. A test asserts that nothing is exported without an entry.
- **[`docs/migrating-from-python.md`](docs/migrating-from-python.md)** — call by call, including the three
  things Python has that this does not and why.
- **[`examples/`](examples/)** — chat, streaming from a Next.js Route Handler, vision, tool calling, and
  governed mode. They import `axonium` by name, so they typecheck against the **published** `.d.ts`
  rather than against `src/`.

## A custom certificate authority

There is no `caBundle` option and there cannot be one: `fetch` has no option for a CA, and reaching one
means either an `undici` dependency or a `node:` import — each breaking a requirement this package was
built to. Use the runtime's own mechanism:

```bash
NODE_EXTRA_CA_CERTS=/path/to/ca.pem node app.js   # Node, Bun
deno run --cert /path/to/ca.pem app.ts            # Deno
```

## Development

```bash
npm install
npm test               # node --test, no runner, no build step (needs Node 22+)
npm run build          # ESM + CJS + .d.ts
npm run lint           # prettier --check, plus tsc on the package and on the examples

AXONIUM_INTEGRATION=1 AXONIUM_GATEWAY_BASE_URL=… AXONIUM_CLIENT_ID=… AXONIUM_CLIENT_SECRET=… \
  npm run test:integration
```

The integration suite is **skipped without credentials and never runs in CI** — it spends real
inference, so a contributor who has not opted in must not pay for it. It picks models by **modality**
from the live catalogue rather than hardcoding ids, because a fixed id fails on every deployment that
does not serve it and that failure reads as a broken SDK.

The published package supports Node 20+. Node 20 cannot strip types, so the suite above proves
nothing about it — CI runs the **built** artifact there instead, which is what a consumer on 20
installs.
