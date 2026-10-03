# Axonium for TypeScript

The Axonium SDK for the Prometheus inference platform, for Node, Bun, Deno and edge runtimes.

> **Status: scaffolding.** Configuration and the error taxonomy are in place and the 34 catalogued
> error types are asserted against [`spec/errors.json`](../spec/errors.json) in both directions. There
> is **no transport yet**, so nothing talks to a gateway. Requested in
> `apeiron_axonium_prometheus/solicitud-axonium-sdk-typescript.md`; see that channel for scope and
> dates.

```ts
import { resolveConfig } from "axonium";

const config = resolveConfig(); // reads AXONIUM_GATEWAY_BASE_URL, AXONIUM_CLIENT_ID, …
```

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
  route keeps the *engine's* status: one suffix covers a `422` that will never succeed and a `429`
  that will.

## Development

```bash
npm install
npm test          # node --test, no runner, no build step (needs Node 22+)
npm run build     # ESM + CJS + .d.ts
npm run lint      # prettier --check and tsc --noEmit
```

The published package supports Node 20+. Node 20 cannot strip types, so the suite above proves
nothing about it — CI runs the **built** artifact there instead, which is what a consumer on 20
installs.
