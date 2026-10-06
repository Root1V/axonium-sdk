# API reference

Everything `axonium` exports. Generated from nothing — written, and a test asserts that every exported
symbol appears here, so a new export with no entry is a red run rather than an undocumented surface.

## Client

### `new Axonium(options?)`

| option                     | default                                                   | meaning                                                                                                                         |
| -------------------------- | --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `gatewayBaseURL`           | `AXONIUM_GATEWAY_BASE_URL`                                | Required. `https`, or `localhost`/`127.0.0.1` for development. There is no default: a wrong gateway is worse than a missing one |
| `clientId`, `clientSecret` | `AXONIUM_CLIENT_ID`, `AXONIUM_CLIENT_SECRET`              | The autonomous mode                                                                                                             |
| `tokenProvider`            | —                                                         | The governed mode. Supplying both modes is **refused**, not resolved by precedence                                              |
| `scope`                    | `AXONIUM_SCOPE`                                           | Omitted when empty — an empty `scope` asks for _no_ scopes, omitting it asks for everything the account holds                   |
| `timeouts`                 | `{connect: 10_000, request: 600_000, stream: 180_000}` ms | See below                                                                                                                       |
| `retry`                    | 3 attempts, 1s base, 60s cap, full jitter                 | Shared with every SDK in this family; the corpus depends on these numbers                                                       |
| `fetch`                    | `globalThis.fetch`                                        | Injected for tests, or for a runtime whose `fetch` you want to wrap                                                             |
| `allowInsecureCredential`  | `false`                                                   | Permits a `clientSecret` where this cannot establish it is on a server                                                          |

**`request` defaults to 600 s deliberately.** It matches what the gateway allows its backends, and some
image backends take 2–8 minutes. A client timeout shorter than the server's, plus a retry, queues a
second expensive generation on top of one still running. **An edge runtime cannot wait that long** — set
`timeouts.request` down and know you are giving up on image generation.

**`stream` is 180 s**, above the gateway's own 120 s read timeout against the backend, so this SDK does
not give up before the gateway would.

| property            |                                                                         |
| ------------------- | ----------------------------------------------------------------------- |
| `api.lastRateLimit` | The budget as of the most recent response, whichever call produced it   |
| `api.tokenClaims`   | Decoded, **not verified**. `subject` is the `client_id`                 |
| `api.grantedScope`  | What the gateway **granted**, which may be narrower than what was asked |

## Resources

### `models`

- **`list(options?)`** → `ModelList`. **Not every deployed model**, since `PRM-167`: only the models this
  token holds a `model:<id>` grant for. An empty list means no grants, _not_ an empty platform.
- **`mine(options?)`** → `ModelList`, cached for the client's lifetime. An alias of `list` since
  `PRM-167`. This is what belongs behind a "test connection" button — it proves the gateway answers, the
  credential works, and there is something you may send.

`ModelList` has `data`, `ids` and `find(id)`. A `Model` carries `id`, `modality`, `contextLength`
(`undefined` for image models, which have no window at all), `servedBy`, `family`, `quantization`,
`ownedBy`, `payloadSchema` and `raw`.

**Dispatch on `payloadSchema`, not on `modality`.** `sst2-clf` and `von-decide` are both classifiers and
want different bodies.

### `chat.completions`

- **`create(request, options?)`** → `ChatCompletion`
- **`stream(request, options?)`** → `ChatStream`

A separate `stream` rather than `create({stream: true})`: it keeps the return type honest, makes the
`inference:stream` scope requirement explicit, and gives one place to say that **a stream is never
retried once it has begun**.

`ChatRequest` takes `model`, `messages`, `max_tokens`, `temperature`, `top_p`, `stop`, `tools`,
`tool_choice`, `response_format`, and anything else — the gateway forwards what it knows and silently
drops the rest, which `requireParameters` turns into an error.

`ChatCompletion` has `content`, `reasoning`, `finishReason`, `toolCalls`, `usage`, `model`, `meta`, `raw`.

**`content` can be empty with nothing wrong.** A reasoning model may spend its whole budget in
`reasoning` and finish with `finishReason: "length"`.

### `ChatStream`

`AsyncIterable<Chunk>`, where `chunk.delta` is the text that arrived and `chunk.raw` the decoded chunk.
After iterating: `content`, `reasoning`, `usage`, `toolCalls`, `finish`, `meta`. `finalMessage()` drains
and returns the first four.

- **It can be consumed once.** Iterating again throws rather than yielding nothing, which would read as a
  model that produced no output.
- **Breaking out cancels the request**, releasing the reader and cancelling the body.
- **`usage.estimated`** is `true` when the counts were derived from the final chunk's `timings` rather
  than reported — llama.cpp-family backends emit no usage chunk when streaming.
- **An in-band failure throws `StreamInterruptedError`**, carrying `partialContent`. Detected by the
  presence of a top-level `error` key, never by matching its text.

### `embeddings.create(request, options?)` → `EmbeddingList`

`data[].embedding`, `usage`. An embedding generates nothing, so `completionTokens` is `undefined` rather
than `0`.

### `images.generate(request, options?)` → `ImageList`

`outputFormat`, `data[].b64JSON`, `data[].bytes()`. **Takes minutes**: see the timeout note.

### `rerank.create(request, options?)` → `RerankList`

`results[].index`, `results[].relevanceScore`, `ranking`, `usage`. The whole document set is **one**
request — against a 60 RPM budget, scoring 50 candidates costs one unit rather than fifty. **`index`
points into the array you sent**, never into `results`, which is what keeps a reordered result
attributable to its input.

### `predict.create(model, body, options?)` → `PredictResult`

The pass-through route for `classification`, `zero_shot` and `typed_decision`. The body goes to the
engine verbatim and `value` comes back **undecoded** — `sst2-clf` answers a _top-level array_, so a type
assuming an object would fail on the first engine the platform shipped here. All three modalities share
**one** rate-limit budget, named `predict`.

### `usage.get(requestId, options?)` → `RequestUsage`

`requestId`, `model`, `requestKind`, `interrupted`, `terminationReason`, `usage`.

A `404` has a third cause beyond "no such id", and it is the common one: **the id belongs to a replay**,
which reached no model and has no row. `meta.idempotentReplayOf` on the original response is the id that
_was_ charged.

## Per-call options

|                     |                                                                                                                                                                                                            |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `idempotencyKey`    | Makes a retry safe: a repeat with the same key and body returns the stored result without reaching a model, recording usage, or counting against the spend cap. Max 255 chars, refused locally beyond that |
| `instance`          | Pins to one replica. Diagnostic                                                                                                                                                                            |
| `timeout`           | Overrides the configured one, in ms                                                                                                                                                                        |
| `signal`            | An `AbortSignal`, **composed** with the timeout rather than replacing it                                                                                                                                   |
| `requireParameters` | Makes the gateway refuse an unsupported field instead of dropping it silently                                                                                                                              |

## Errors

Everything extends `AxoniumError`, so one `catch` covers the package.

| class                    | when                                                                                                                      |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------- |
| `ConfigurationError`     | A setting is missing or contradictory. Before any request                                                                 |
| `InvalidRequestError`    | Refused locally — nothing reached the gateway, nothing was billed                                                         |
| `TransportError`         | No answer: DNS, TLS, a dropped connection, a cancellation                                                                 |
| `TimeoutError`           | A `TransportError` subclass. **Never retried**: the backend is probably still generating                                  |
| `StreamInterruptedError` | A stream that began and failed. Carries `partialContent`                                                                  |
| `APIError`               | A gateway failure. One subclass per catalogued `type` suffix; an unknown suffix falls back by status rather than throwing |
| `OAuthError`             | The token endpoint. **Not an `APIError`**, because retrying a credential failure with the same credential can never work  |

`APIError` carries `status`, `typeSuffix`, `title`, `detail`, `instance`, `retryAfter`, `meta` and `raw`.
`retryable` says whether retrying can help — and on `PredictBackendRejectedError` it is derived from the
status, because that route keeps the _engine's_ status: one suffix covering a `422` that never succeeds
and a `429` that will.

The 34 subclasses are asserted against [`spec/errors.json`](../../spec/errors.json) in both directions, so
a suffix cannot be claimed here and absent there — or invented here, which would leave a caller with a
`catch` that can never run.

### The full taxonomy

All 34 rows of [`spec/errors.json`](../../spec/errors.json), and the class each maps to. The
table below is derived from that file and from `src/errors.ts` together, so it cannot claim a class that
does not exist or a suffix the catalogue does not list — a test asserts both directions.

| status | `type` suffix |
|---|---|---|---|
| `400` | `unknown-model` | `UnknownModelError` | no |
| `400` | `modality-mismatch` | `ModalityMismatchError` | no |
| `400` | `context-exceeded` | `ContextExceededError` | no |
| `401` | `missing-credentials` | `MissingCredentialsError` | no |
| `401` | `invalid-token` | `InvalidTokenError` | no |
| `401` | `token-expired` | `TokenExpiredError` | yes |
| `401` | `token-revoked` | `TokenRevokedError` | no |
| `402` | `spend-cap-exceeded` | `SpendCapExceededError` | no |
| `403` | `forbidden` | `ForbiddenError` | no |
| `429` | `rate-limit-exceeded-requests` | `RateLimitError` | yes |
| `502` | `upstream-error` | `UpstreamError` | yes |
| `503` | `capacity-exhausted` | `CapacityExhaustedError` | yes |
| `503` | `model-not-loaded` | `ModelNotLoadedError` | no |
| `503` | `backend-unavailable` | `BackendUnavailableError` | yes |
| `503` | `rate-limiting-unavailable` | `RateLimitingUnavailableError` | yes |
| `503` | `usage-store-unavailable` | `UsageStoreUnavailableError` | yes |
| `503` | `rerank-dialect-unknown` | `RerankDialectUnknownError` | **no** |
| `422` | `validation-error` | `ValidationError` | no |
| `400` | `unknown-parameter` | `UnknownParameterError` | no |
| `400` | `unknown-instance` | `UnknownInstanceError` | no |
| `400` | `invalid-idempotency-key` | `InvalidIdempotencyKeyError` | no |
| `409` | `idempotency-key-reuse` | `IdempotencyKeyReuseError` | no |
| `409` | `idempotency-in-progress` | `IdempotencyInProgressError` | yes |
| `409` | `idempotency-response-not-retained` | `IdempotencyResponseNotRetainedError` | no |
| `404` | `not-found` | `NotFoundError` | no |
| `404` | `unknown-route` | `UnknownRouteError` | no |
| `405` | `method-not-allowed` | `MethodNotAllowedError` | no |
| `503` | `upstream-unavailable` | `TokenEndpointUnavailableError` | yes |
| `503` | `not-configured` | `TokenEndpointNotConfiguredError` | no |
| `400` | `inconsistent-model-group` | `InconsistentModelGroupError` | no |
| `401` | `unauthorized` | `UnauthorizedRequestError` | no |
| `400` | `invalid-date` | `InvalidDateError` | no |
| `400` | `invalid-range` | `InvalidRangeError` | no |
| `400` | `range-too-large` | `RangeTooLargeError` | no |
| `4xx` | `predict-backend-rejected` | `PredictBackendRejectedError` | **by status** |

An unlisted suffix falls back to `ServerError` for a `5xx` and to `APIError` otherwise, rather than
throwing: the catalogue grows, and an error documented on a Tuesday must not become a parse failure in a
version already published.

`predict-backend-rejected` is the one row with no fixed status. The pass-through route keeps the
**engine's** status, so a `422` stays a `422` and a `429` stays a `429` — and `retryable` is derived from
it rather than from the name.

### The token endpoint

| status | `error` |
|---|---|---|---|
| `400` | `unsupported_grant_type` | `UnsupportedGrantTypeError` | no |
| `400` | `invalid_scope` | `InvalidScopeError` | no |
| `401` | `invalid_client` | `InvalidClientError` | no |
| `401` | `unauthorized_client` | `UnauthorizedClientError` | no |

A `5xx` from the token endpoint is not one of these: it is the gateway failing to reach the
auth-service, arrives as problem+json, and maps into the table above as
`TokenEndpointUnavailableError` or `TokenEndpointNotConfiguredError`.

### Other exports

|                                                                          |                                                                                         |
| ------------------------------------------------------------------------ | --------------------------------------------------------------------------------------- |
| `resolveConfig(options?)`                                                | Applies the precedence and returns the resolved config. What `new Axonium()` does first |
| `errorFromProblem(problem)`, `errorFromOAuth(status, code, description)` | Build a typed error from an envelope. Exported for anyone wrapping this SDK             |
| `usageFrom(value)`                                                       | Reads a usage object, lifting `prompt_tokens_details.cached_tokens` to the flat name    |
| `MAX_IDEMPOTENCY_KEY_LENGTH`                                             | `255`, the gateway's limit                                                              |

## Helpers

|                                                 |                                                                                                                            |
| ----------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `imageFromBytes(bytes, mediaType)`              | A base64 data URI part. **No URL helper exists**: the gateway refuses `http(s)://` as an SSRF mitigation                   |
| `jsonSchema(name, schema, options?)`            | A `json_schema` response format. Accepts anything with `toJSONSchema()`, so Zod works without this package depending on it |
| `decodedArguments(call)`                        | Parses a tool call's arguments. A function, not a method, because the response types are plain data                        |
| `decodeClaims(token)`                           | Reads a JWT's payload. **Not verification** — that is the gateway's job                                                    |
| `VERSION`, `USER_AGENT`                         |                                                                                                                            |
| `DEFAULT_RETRY`, `NO_RETRY`, `DEFAULT_TIMEOUTS` |                                                                                                                            |
| `Transport`, `TokenManager`                     | The layers under the client, exported because they are useful alone                                                        |
| `events(body)`, `dataOf(event)`                 | The SSE primitives                                                                                                         |

## Per-token probabilities

`logprobs: true` returns the chosen token's own probability; `top_logprobs: N` (0–20) adds the N most
likely alternatives at each position. **`top_logprobs` requires `logprobs: true`** — sending it alone
is a `422`, refused here at the call site rather than after a round trip.

```typescript
const completion = await client.chat.completions.create({
  model: "qwen3-0.6b",
  messages: [{ role: "user", content: "yes or no?" }],
  logprobs: true,
  top_logprobs: 3,
});

for (const token of completion.logprobs ?? []) {
  console.log(token.token, token.probability); // 0.9995, not -0.00054
}
```

`logprob` is a **natural logarithm**: `-0.00054` is ~99.95% and `-7.6` is ~0.05%. Read as a
probability it looks like a number near zero meaning "unlikely", and the mistake is silent, so
`probability` is computed for you. It is `undefined` when the backend sent no `logprob` — a token it
said nothing about is a different fact from one it said was impossible.

`completion.logprobs` is `undefined` when the engine does not have the feature, because the response
simply carries no key. Send `requireParameters: true` to be told rather than inferring it.

## `meta` on every response

| field                                    |                                                                                                                                        |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `requestId`, `traceId`                   | What the platform team asks for. Worth logging on success too — correlating a slow-but-successful call matters as much as a failed one |
| `instance`, `instanceId`                 | Which replica answered                                                                                                                 |
| `idempotentReplay`, `idempotentReplayOf` | A replay is neither generated nor billed, and `idempotentReplayOf` is the only id with a usage row — a replay's own id has none        |
| `rateLimit`                              | `undefined` when the gateway reported none, which is not a budget of zero                                                              |
| `ignoredParameters`                      | Fields the gateway accepted, **ignored**, and named back                                                                               |
| `attempts`, `waitedMs`                   | How many HTTP attempts produced this, and how long this SDK spent **deliberately asleep** before answering                             |

**`waitedMs` is the number a latency graph needs.** A respected `Retry-After` of up to 60 seconds
looks, from outside, exactly like one slow call among fast ones — three separate teams reported that
as a hang. Subtract it from your own wall clock to get what the platform actually spent. It is on
errors too, so a call that waited and then failed anyway can still explain its duration; the other
four SDKs cannot answer that one yet.

**`ignoredParameters` is `undefined` when there were none**, because the header is present only when
there is something to report — so an empty array would claim the gateway looked and found nothing, which
is a different statement from the gateway not having said.

This endpoint takes an OpenAI-compatible _subset_: `n`, `presence_penalty`, `logit_bias`, `seed` and the
like neither fail the request nor reach the engine. They were dropped in silence until `PRM-127`, and the
guide's own words on fixing that are why this is surfaced rather than read and discarded — _a setting that
does nothing and says nothing is indistinguishable from one that works_. `requireParameters: true` turns
it into a `400 unknown-parameter` instead.

## Not here, and why

- **`usage.export`** — not an oversight. The contract says it plainly: _"Requires `admin:read`. Not
  something an SDK calls; documented because consumers parse the file."_ An integrator's token does not
  hold `admin:read`, and the response is a CSV whose columns are a documented contract — so the thing to
  read is §3.9, not a method here.
- **OpenTelemetry** and a logging hook — `0.2.0`.
- **A `caBundle` option** — cannot exist. `fetch` has no option for a CA, and reaching one means an
  `undici` dependency or a `node:` import, each breaking a requirement this package was built to. Use
  `NODE_EXTRA_CA_CERTS` on Node and Bun, `--cert` on Deno.
