# Changelog

Each language SDK versions independently. Entries are grouped by language and use tags of the
form `python/vX.Y.Z`, `go/vX.Y.Z`, `rust/vX.Y.Z`.

## TypeScript

### Unreleased

Nothing yet.

### 0.2.0 — 2026-10-05

Re-vendored at `2026-10-05a · PRM-187/188`, and the two things we asked for arrived in `2026-10-04b`.

**`logprobs` and `top_logprobs` on chat completions (`logprobs` / `top_logprobs`), with the answer at `completion.logprobs`.** How
confident the model was, so an agent can escalate to a person instead of acting on a guess — which is
what Apeiron asked the platform for.

**`logprob` is a natural logarithm**, and that is the whole reason `TokenLogprob.probability` exists: `-0.00054` is about
99.95% and `-7.6` is about 0.05%. Read as a probability it looks like a number near zero meaning
*unlikely*, and nothing about the mistake is loud. It is **absent rather than zero** when the backend
sent no `logprob`, because a token it said nothing about is a different fact from one it said was
impossible, and a caller thresholding on confidence has to tell them apart.

**`top_logprobs` without `logprobs` is refused here**, not after a round trip. The rule is the
engine's — llama.cpp answers *"top_logprobs requires logprobs to be set to true"* — and the gateway
enforces it before forwarding so the refusal arrives as problem+json. `logprobs: false` beside it is
refused too: an SDK checking only for *absence* would have sent that one, since the field is present
and wrong.

**`raw_scores` on `/v1/rerank` (`raw_scores`).** The field we declined to implement last time because it was
announced in a message and absent from §3.6 — the platform added it to the contract and said the
procedure was the right call, so it lands now. The logit instead of the probability: a reranker's
probabilities saturate near 1.0, and a saturated probability cannot be calibrated while the logit
behind it can. Safe to send unconditionally, because an engine without it answers normally and names
the field in `X-Prometheus-Ignored-Parameters`.

**And the scope question we raised came back as the platform's own defect.** Their new paragraph
listed four scopes and omitted `embeddings` and `rerank`, which have had their own buckets since
`PRM-129` — the paragraph below it, which they had not touched, was the correct one. They fixed it by
**removing the list** rather than correcting it, which is what this SDK did in the same release. Two
wrong copies of one truth, one in each team, and they caught each other; no test on either side could
have.

No corpus case covers any of this yet — `logprobs` needs a recording and `raw_scores` needs an engine
that has it — so the local tests are what hold it.

**`meta.attempts` and `meta.waitedMs`, which this SDK has been computing since `0.1.0` and throwing
away.** The retry loop tracked both, `sendJSON` dropped them, and the package exported an `Attempts`
type that no public call ever returned — a type a consumer could name and never obtain.

`waitedMs` is the number that matters: a respected `Retry-After` of up to 60 seconds looks from
outside exactly like one slow call among fast ones, and three separate teams reported that as a hang.
Subtract it from your own wall clock to get what the platform actually spent.

**And unlike the other four, it is on errors too.** The site lists "a call that waited and then
failed anyway reports none of this" as a known limit; here the error already carried `meta`, so the
counters are stamped on before it leaves. That call is precisely the one whose duration needs
explaining.

The test that was supposed to cover this **read the counters off `send`'s internal return**, a shape
no caller holds, so it stayed green while every consumer saw a retried call as a slow one. Rewritten
against `sendJSON().meta`, where a caller stands, and mutation-tested: removing the threading turns
it red.


Re-vendored at `2026-10-04 · PRM-182/183/184`, which brings a new engine, a new error, and a trap.

**`503 rerank-dialect-unknown` → `RerankDialectUnknownError`.** A reranker running on an engine whose rerank request
shape the gateway has not recorded. **The one 5xx in the catalogue that is not retryable**, and that
is the whole reason it needed naming rather than falling through: measured here, an unmapped `503`
resolves to the status-keyed fallback, which **is** retryable — so until today this error would have
been retried through the whole attempt budget and reported as a timeout for a condition that was
never going to clear. Mutation-tested two ways in Rust: deleting the mapping fails the catalogue
parity check by name, and marking it retryable fails it with the number.

**`tei.predict.v1`, and the case `payload_schema` was waiting for.** A second engine now serves
`zero_shot`, and the two disagree. `hf-inference.zero-shot-classification.v1` answers scores
normalised across *the caller's* candidate labels; `tei.predict.v1` answers scores across the
**model's own** classes and has no notion of candidate labels at all. Both sum to 1, over different
things. Dispatching on `modality` reads one as the other, which is exactly the failure the decision
to dispatch on `payload_schema` was made to prevent — and it had no case to prove it until now. No
code changed: `payload_schema` is a pass-through string and no SDK enumerates its values.

**The batch trap, documented where somebody will read it.** On that engine a batch is *always* a
list of lists: a flat array of two strings is read as one pair and answers **once, in silence**, and
three or more is a `422`. So the obvious "send my N texts as an array" is the single form that
quietly returns one wrong answer. It is also the argument for leaving the predict result undecoded —
one input returns a flat list and a batch returns a list of lists, from the same model and the same
endpoint.

**Five SDKs kept five different hand-written lists of rate-limit scopes, and they had diverged.**
This one said `chat`, `embeddings`, `rerank`, `predict`, `images`, `default`. One of the five said `chat` where the header says `chat_completions`, which
is a name a caller would key a map by and never match. The guide now contradicts itself about the
set too — the new §6.3 addendum names four scopes as today's complete set while the paragraph below
it, unchanged, says `PRM-129` gave `embeddings` and `rerank` their own. Raised with the platform; in
the meantime all five stop enumerating and say the set is read from the header. The fixed 60-second
wall-clock window is documented in its place, because that one is a fact a caller has to design
against: pace on the remaining count, never on an assumed rate.

**`raw_scores` on `/v1/rerank` is deliberately absent.** It was announced to us as a new optional
field in the gateway's own contract, with measurements — but §3.6 of the guide revision that was
supposed to carry it still documents only `query`, `documents` and `top_n`. Implementing from a
message rather than from the contract is how a field ends up in five SDKs and in no allowlist check.
Raised; it lands when the contract has it.

### 0.1.0 — 2026-10-05

On npm as [`axonium`](https://www.npmjs.com/package/axonium), with provenance. Verified as a consumer
rather than read off the publish log: installed from the registry into an empty project, `npm audit
signatures` reports a verified attestation, **one** package in `node_modules` — the zero-dependency
claim, measured — and both `import` and `require` resolve `VERSION` to `0.1.0` and `ForbiddenError` to
a class.

**The first publish could not use OIDC**, which is a fact about npm rather than a shortcut taken here:
a trusted publisher is configured on the package's own settings page, and there is no page until the
package exists. npm has no pending-publisher flow the way PyPI does. So `0.1.0` authenticated with a
short-lived granular token, deleted immediately after, and every release from here is OIDC — the
workflow carries both paths and switches on the secret's absence, so nothing is edited at the moment of
switching. **Provenance was never at stake**: it needs `id-token: write` and a recognised CI, not OIDC
authentication, so the attestation on `0.1.0` is the same one every later release will carry, and
Apeiron's acceptance criterion 5 is met by this version rather than the next.

npm left a `0.0.0-stage` placeholder in the version list from its own staging step. It is not `latest`
and nothing resolves to it.


Apeiron's `H2` scope, plus the three acceptance criteria that were missing.

#### Two things the first release rehearsals found, both about leftover state

**`npm run lint` needed a build that only existed on my machine.** The examples import `axonium` by name,
which self-resolves to `dist/`, so checking them needs the build to exist — and it did here, left over
from an earlier run, and did not in a clean checkout. CI failed on the exact command that passed locally.
`lint` now builds what it checks rather than assuming somebody built it, verified with `dist/` **and**
`node_modules/` removed.

**The test stub deadlocked the event loop on Node 22.** `AbortSignal.timeout` schedules an *unref'd*
timer there, so with nothing else pending the loop drained before the abort fired and the stub's promise
never settled — reported as *"Promise resolution is still pending but the event loop has already
resolved"*. A real `fetch` cannot hit it, because an open socket holds the loop; a stub has to hold it
deliberately, which it now does with a ref'd backstop.

**The CI matrix is what found it**: Node 24 and 26 pass, Node 22 does not. The suite was green on the
runtime I happened to have and red on the floor the package claims — the same shape as every other
instrument failure in this repository, one layer out. Reproduced locally against Node 22 before fixing,
and both versions are green now.

**`meta.ignoredParameters`** — the request fields the gateway accepted, **ignored**, and named back in
`X-Prometheus-Ignored-Parameters`. `undefined` when there were none, because the header is present only
when there is something to report: an empty array would claim the gateway looked and found nothing, which
is a different statement from the gateway not having said.

The first of the five SDKs to expose it, and the guide's own words on why `PRM-127` added the header are
the argument — *a setting that does nothing and says nothing is indistinguishable from one that works*.
An SDK that read the header and discarded it would restore that silence one layer down. The name is now
the one the other four will copy, which is why it is going to the channel rather than only here.

**`usage.export` is not missing, it is declined**, and the contract says why: *"Requires `admin:read`.
Not something an SDK calls; documented because consumers parse the file."* An integrator's token does not
hold `admin:read`, and the answer is a CSV whose column rules are themselves a contract. The thing to read
is §3.9, not a method here.

**A `tokenProvider` now discards credentials that merely happen to be in the environment**, and only a
pair passed explicitly beside it is a contradiction. The first version read both the same way, so a caller
supplying a provider on a machine with `AXONIUM_CLIENT_SECRET` exported was refused outright — which is
the governed multi-tenant shape on any host where ops set those variables. Rust had already decided this
and said so in a doc comment; this one had not read it.

Found by running `verify.sh` in a shell with a `.env` sourced, which is what a developer does. The
reading also turns the guarantee into a fact rather than a claim: with a provider, this SDK holds no
long-lived secret whatever the environment contains.

**Integration tests, 16 of them, against a live gateway.** Skipped without `AXONIUM_INTEGRATION=1` and
credentials, and never in CI: they spend real inference, so a contributor who has not opted in must not
pay for them, and a suite that failed on a missing credential would train everyone to ignore a red run.

They pick models by **modality from the live catalogue** rather than hardcoding ids. A fixed id fails on
every deployment that does not serve it, and that failure reads as a broken SDK rather than as a
different catalogue. What they cover that the corpus cannot: the corpus replays recorded bytes, so it
proves agreement with four other SDKs about a *past* response. Only a live gateway proves the contract
still holds — which is how a premise that quietly expired gets found, and this repository has found
several.

**Five runnable examples**, the five that were asked for: chat, streaming from a Next.js Route Handler,
vision, tool calling, governed mode. They import `axonium` by name, which self-resolves to `dist/`, so
they typecheck against the **published** `.d.ts` rather than against `src/` — which makes them a check on
the published surface and not only documentation. Four were run against a live gateway.

**An API reference and a migration guide from Python.** The reference lists all 34 error classes against
their `type` suffix, in a table derived from `errors.json` and `src/errors.ts` together. A test asserts
every export has an entry.

#### Three defects the new checks found immediately

**The generator for that error table had a greedy regex** that spanned class boundaries, so it named
`AxoniumError` as the class for `unknown-model` and `ServerError` for `upstream-error`. The
every-export-is-documented test caught it, because the two real classes then appeared nowhere.

**A `.npmignore` was silently overriding the `files` allowlist**, and `docs/` never reached the tarball.
Two statements of the same truth with one of them winning quietly — so the `.npmignore` is gone and
`files` is the only one.

**Every sourcemap pointed at `../../src/*.ts` with `src/` excluded from the package.** A map pointing at a
file that is not there is worse than no map: a debugger reports "file not found" instead of falling back
to the compiled output. `src/` now ships, and the release workflow extracts the tarball and asserts that
every map's sources resolve inside it.

#### Also

`tools`, `tool_choice` and `response_format` are typed rather than `unknown`, since tool calling is in
`H2`. `jsonSchema(name, schema)` accepts anything with a `toJSONSchema()` method — which is how a Zod
schema works without this package depending on Zod, resolving the contradiction in the original request.

**`ts-release.yml`**: npm with `--provenance` via OIDC, no stored token, behind an `npm` environment with
a required reviewer. The build job packs, lists the tarball, installs it into a clean directory, exercises
both ESM and CJS, checks the sourcemaps, and **measures the bundle** — 9.5 KB minified and gzipped
against the 50 KB budget, asserted rather than assumed, because a transitive dependency is how that
budget quietly becomes 400 KB.

#### Deliberately absent

- **A `caBundle` option, which cannot exist.** `fetch` has no option for a CA, and reaching one means an
  `undici` dependency or a `node:` import — each breaking a stated requirement. Documented per runtime
  instead: `NODE_EXTRA_CA_CERTS` on Node and Bun, `--cert` on Deno.
- **`usage.export`** — in no SDK of this family.
- **`X-Prometheus-Ignored-Parameters` on a success** — doing it in one SDK first is the divergence the
  shared corpus exists to prevent.
- **OpenTelemetry and the logging hook** — `H3`.

**Bun, Deno and edge are untested.** CI runs Node 20, 22 and 24. Claiming the other three because this
only uses `fetch` would be exactly the unmeasured assertion this repository keeps catching.

**Resource methods and the contract corpus: 49 of 49.** `models`, `chat` (including `stream`),
`embeddings`, `images`, `rerank`, `predict` and `usage`, plus an SSE parser written over
`ReadableStream` with no dependency. 75 tests. Exercised end to end through the installed package
against a live deployment — catalogue, chat, a 120-chunk stream, vision from bytes, embeddings, rerank,
predict and the usage row.

**The corpus passed on its first complete run**, which is not a boast about the code: the four SDKs
before it had already paid for every case. What it did find is below.

#### Three things mutation found that the corpus could not

The runner reaching 49/49 proves agreement with four other SDKs. It does not prove the cases have
teeth, so five mutations were applied. Two survived, and a third gap turned up while measuring:

| mutation | why the corpus let it pass |
|---|---|
| detect the in-band stream error by matching `"stream interrupted"` | the one fixture with an in-band error carries that one message, so "detect the key" and "match the string" are indistinguishable |
| build `rerank`'s `ranking` from positions rather than indices | **zero** cases assert `ranking` — and `rerank.json`'s indices are `[2,0,1]`, so the data to catch it is right there |
| *(not a mutation)* a stream with no `reasoning` accessor at all | no case asserts streamed reasoning; four SDKs having it was a coincidence |

The first is **fixed in the corpus**, as manifest v27: a second in-band failure whose payload is an
object with a code and a message, so the only reading that satisfies both cases is the one the platform
asked for. Authored rather than recorded, and the case says why — it cannot be recorded until the
gateway emits a second shape, and waiting leaves five SDKs free to hardcode a string meanwhile. It
reuses the `stream_error` kind, so no runner changed and Python, Go and Rust picked it up on the next
run.

The other two are covered locally and recorded as `AXO-129` and `AXO-130`. Both need a new expectation
key, which means a resolver in all five runners rather than a manifest edit.

**The reasoning gap matters more than it sounds.** Measured live on `qwen3-0.6b`: a stream was 120
chunks with `content` empty and every delta carrying `reasoning_content`. A UI showing only `content`
displays nothing, which looks exactly like a broken SDK. `ChatStream.reasoning` now accumulates it and
`finalMessage()` hands it over.

#### Shapes worth naming

**`chat.completions.stream()` is a separate method**, not `create({stream: true})`. It keeps the return
type honest — no union of a completion and an iterable — makes the `inference:stream` scope requirement
explicit, and gives one place to say that a stream is never retried once it has begun.

**`ToolCall` carries the wire nesting**, `function: {name, arguments}`, with flat `name`/`arguments`
shortcuts over it. That is what the other four expose, so one contract case resolves
`tool_calls.0.function.name` across all five. Arguments stay a **string**: a generation stopped by
`max_tokens` leaves them truncated, and parsing eagerly would fail the whole response and lose the
correlation ids with it.

**Tool calls are reassembled by `index`.** Appending to whichever call was last concatenates two
interleaved calls' arguments into one unparseable string — the corpus catches that one.

**There is no helper for a remote image URL**, because the gateway refuses `http(s)://` as an SSRF
mitigation: an API accepting one would accept something that always fails. `imageFromBytes` produces a
data URI, and an `http(s)` part is refused before the round trip.

**`usage.get` documents the third cause of its `404`** and it is the common one: the id belongs to a
**replay**, which reached no model and has no row. `meta.idempotentReplayOf` is the id that was charged.

**The SSE buffer is flushed at end of stream.** A final event with no trailing blank line is otherwise
dropped silently, and a truncated stream is exactly when a caller most needs what did arrive.

**Transport and auth.** The SDK talks to a gateway now: OAuth2 `client_credentials`, the retry loop,
per-model cooldowns, typed errors off the wire, and the correlation metadata on every response.
Verified through the installed package against a live deployment — the catalog, `predict`, chat, a
`400 unknown-model` and a `404 unknown-route`, which is the first time any SDK in this family has
exercised `PRM-174`'s new type against a deployment rather than a fixture.

**The token's lifetime is anchored to the server's clock.** When the response's `Date` header and the
token's own `exp` are both present, their difference is a second reading of the same lifetime and a
skew-free one, because both come from the server. The shorter wins: an early refresh costs one
request, believing a token lives longer than it does costs every request after it expires.

**Concurrent callers share one in-flight token request**, through a kept promise rather than a lock.
Without it a cold client answering ten simultaneous requests sends ten token requests and nine are
charged against the rate-limit budget for nothing.

**The granted scope is read back from the response, never assumed from the request.** Asking for a
subset is honoured, so what came back is what the token can do; a caller trusting their own request
would diagnose a `403` as a platform fault.

**`fetch` is injected, so the whole test harness is a function.** 62 tests, no mocking library, and
nothing to restore afterwards — what they exercise is the real transport rather than a seam around it.

#### One defect found by measuring, which no test would have caught

`decodeClaims` read a `client_id` claim. **No token carries one**: the guide documents `sub` and
`azp`, the other four SDKs read `sub`, and this one invented a name. The accessor returned `undefined`
for every real token, which reads exactly like a gateway that had not sent it. Found by printing the
claims of a live token. It is now `subject` and `authorizedParty`, matching the other four, and a test
pins the measured shape — including that an unmodelled claim stays reachable through `raw`.

#### One real bug, found by a test hanging

The reactive `401` refresh had no bound. Its condition compares the rejected token with the one just
used, which is false again after every refresh, so a gateway answering `401` to every token looped
forever fetching new ones. The test written to assert "raised rather than looped" found it by never
finishing.

#### And four cases of the instrument being wrong rather than the code

Worth writing down together, because the pattern is the expensive part:

| what looked broken | what was actually wrong |
|---|---|
| the decoder mangled a non-ASCII claim | the test's JWT helper encoded latin1; a real issuer encodes UTF-8 |
| a 40s token was not refreshed early | 40s is above the 30s floor, so reuse was correct |
| a refresh did not replace the token | the stub issued one constant string, so the assertion compared a value with itself |
| a 20ms `Retry-After` was not honoured | the test's policy capped backoff at 10ms, so refusing it was correct |

Each would have been "fixed" into a worse SDK. The first is the clearest: matching the decoder to the
helper would have broken every real token with an accent in it.

**Scaffolding, configuration and the error taxonomy.** No transport yet, so nothing talks to a
gateway. Requested by Apeiron on 2026-10-03; the scope and the dates live in that channel.

**In the monorepo, under `typescript/`.** Swift is the one SDK in a repository of its own, and that
was argued badly at the time — the claim that SwiftPM forced it was measured false afterwards. The
price is real and gets paid on every contract change: its corpus is a submodule whose pointer
somebody has to remember to move. npm has no such constraint, so `spec/` is a relative path here and
cannot fall behind.

**All 34 catalogued error types, asserted against `spec/errors.json` in both directions.** The second
direction is the one nothing else would report: a suffix this SDK *invented* leaves a caller with a
`catch` block that can never run, and reading this package alone would never show it.

Two shapes carried over from the other four because they were learned the hard way:

- `OAuthError` does **not** extend `APIError`. The token endpoint answers RFC 6749, not problem+json,
  and a credential failure is never worth retrying with the same credential.
- `PredictBackendRejectedError.retryable` is derived from the status, because the pass-through route
  keeps the **engine's** status — one suffix covering a `422` that will never succeed and a `429`
  that will.

**Zero runtime dependencies, and three tests that enforce it** rather than a line in a README: no
`dependencies`, no `peerDependencies`, and nothing under `src/` importing a `node:` module. The last
one matters because a `node:crypto` import would pass every test and fail only on an edge deployment.

**`fetch` is injected, not patched**, so the contract corpus will replay against a function. A test
refuses an HTTP-mocking dev dependency: with one, what the suite exercises stops being the transport.

**The source is erasable TypeScript** (`erasableSyntaxOnly`), so Node runs it with
`--experimental-strip-types` and the suite needs no build step and no test runner. Discovered by
writing two parameter properties and watching Node refuse the file — a compiler flag is cheaper than
remembering, and this is the whole reason there is no vitest here.

**The browser guard looks for a server, not for a window.** Apeiron's request asked for
`typeof window !== "undefined"`; jsdom defines `window`, so that fires in anyone's vitest suite, and a
guard with false positives in CI is a guard somebody disables. More importantly the platform's rule
changed axis on 2026-10-02: it is about **whose** credential it is, not where the code runs. An
integrator's must never reach a machine its users control; an end client's own may live on their own
device. A bundle cannot tell those apart, so the default refuses and `allowInsecureCredential` is how
a caller states which case theirs is.

**The non-streaming timeout defaults to 600 s and that is not an oversight** — it matches what the
gateway allows its backends, because a client timeout shorter than the server's plus a retry queues a
second expensive generation on top of one still running. An edge runtime cannot wait that long, which
is a real conflict rather than a tuning question and is written down as one.

## Python

### Unreleased

Nothing yet.

### 1.0.0rc7 — 2026-10-05

Re-vendored at `2026-10-05a · PRM-187/188`, and the two things we asked for arrived in `2026-10-04b`.

**`logprobs` and `top_logprobs` on chat completions (``logprobs`` / ``top_logprobs``), with the answer at ``choices[0].logprobs.content``.** How
confident the model was, so an agent can escalate to a person instead of acting on a guess — which is
what Apeiron asked the platform for.

**`logprob` is a natural logarithm**, and that is the whole reason ``TokenLogprob.probability`` exists: `-0.00054` is about
99.95% and `-7.6` is about 0.05%. Read as a probability it looks like a number near zero meaning
*unlikely*, and nothing about the mistake is loud. It is **absent rather than zero** when the backend
sent no `logprob`, because a token it said nothing about is a different fact from one it said was
impossible, and a caller thresholding on confidence has to tell them apart.

**`top_logprobs` without `logprobs` is refused here**, not after a round trip. The rule is the
engine's — llama.cpp answers *"top_logprobs requires logprobs to be set to true"* — and the gateway
enforces it before forwarding so the refusal arrives as problem+json. `logprobs: false` beside it is
refused too: an SDK checking only for *absence* would have sent that one, since the field is present
and wrong.

**`raw_scores` on `/v1/rerank` (``RerankRequest.raw_scores``).** The field we declined to implement last time because it was
announced in a message and absent from §3.6 — the platform added it to the contract and said the
procedure was the right call, so it lands now. The logit instead of the probability: a reranker's
probabilities saturate near 1.0, and a saturated probability cannot be calibrated while the logit
behind it can. Safe to send unconditionally, because an engine without it answers normally and names
the field in `X-Prometheus-Ignored-Parameters`.

**And the scope question we raised came back as the platform's own defect.** Their new paragraph
listed four scopes and omitted `embeddings` and `rerank`, which have had their own buckets since
`PRM-129` — the paragraph below it, which they had not touched, was the correct one. They fixed it by
**removing the list** rather than correcting it, which is what this SDK did in the same release. Two
wrong copies of one truth, one in each team, and they caught each other; no test on either side could
have.

No corpus case covers any of this yet — `logprobs` needs a recording and `raw_scores` needs an engine
that has it — so the local tests are what hold it.

Re-vendored at `2026-10-04 · PRM-182/183/184`, which brings a new engine, a new error, and a trap.

**`503 rerank-dialect-unknown` → ``RerankDialectUnknownError``.** A reranker running on an engine whose rerank request
shape the gateway has not recorded. **The one 5xx in the catalogue that is not retryable**, and that
is the whole reason it needed naming rather than falling through: measured here, an unmapped `503`
resolves to the status-keyed fallback, which **is** retryable — so until today this error would have
been retried through the whole attempt budget and reported as a timeout for a condition that was
never going to clear. Mutation-tested two ways in Rust: deleting the mapping fails the catalogue
parity check by name, and marking it retryable fails it with the number.

**`tei.predict.v1`, and the case `payload_schema` was waiting for.** A second engine now serves
`zero_shot`, and the two disagree. `hf-inference.zero-shot-classification.v1` answers scores
normalised across *the caller's* candidate labels; `tei.predict.v1` answers scores across the
**model's own** classes and has no notion of candidate labels at all. Both sum to 1, over different
things. Dispatching on `modality` reads one as the other, which is exactly the failure the decision
to dispatch on `payload_schema` was made to prevent — and it had no case to prove it until now. No
code changed: `payload_schema` is a pass-through string and no SDK enumerates its values.

**The batch trap, documented where somebody will read it.** On that engine a batch is *always* a
list of lists: a flat array of two strings is read as one pair and answers **once, in silence**, and
three or more is a `422`. So the obvious "send my N texts as an array" is the single form that
quietly returns one wrong answer. It is also the argument for leaving the predict result undecoded —
one input returns a flat list and a batch returns a list of lists, from the same model and the same
endpoint.

**Five SDKs kept five different hand-written lists of rate-limit scopes, and they had diverged.**
This one said `embeddings`, `rerank`, `chat_completions`, `default`. One of the five said `chat` where the header says `chat_completions`, which
is a name a caller would key a map by and never match. The guide now contradicts itself about the
set too — the new §6.3 addendum names four scopes as today's complete set while the paragraph below
it, unchanged, says `PRM-129` gave `embeddings` and `rerank` their own. Raised with the platform; in
the meantime all five stop enumerating and say the set is read from the header. The fixed 60-second
wall-clock window is documented in its place, because that one is a fact a caller has to design
against: pace on the remaining count, never on an assumed rate.

**`raw_scores` on `/v1/rerank` is deliberately absent.** It was announced to us as a new optional
field in the gateway's own contract, with measurements — but §3.6 of the guide revision that was
supposed to carry it still documents only `query`, `documents` and `top_n`. Implementing from a
message rather than from the contract is how a field ends up in five SDKs and in no allowlist check.
Raised; it lands when the contract has it.

Re-vendored at `2026-10-02 · PRM-167/173/174`, which adds **two error types** and takes one away.

- **`404 unknown-route`** → ``UnknownRouteError``
- **`405 method-not-allowed`** → ``MethodNotAllowedError``

`unknown-route` is deliberately **not** `not-found`, and the platform split them for these SDKs'
benefit: `not-found` is a statement about *data* — no usage row with that id belonging to this client
— which a caller may reasonably read as an empty result or retry. A bad URL is neither. All four SDKs
dispatch on the suffix, so one shared type would have made them do the wrong thing with one of the
two. `method-not-allowed` keeps Starlette's `Allow` header through the re-wrapping, so a `405` still
answers "then which verb".

**And it retires a claim this repository made about the gateway.** `spec/errors.json` carried, since
2026-09-27 and correctly then, that a `404` for an unserved route was *not* the problem+json envelope
— a bare `{"detail": "Not Found"}` with no `type` and no correlation ids in the body. `PRM-174`
fixed it; measured 2026-10-03 against the restarted stack. The note is corrected in place rather than
deleted, because **the fallback it forced stays and its reason has changed**: a body with no `type`
still arrives, but from a proxy returning HTML before the request ever reaches the gateway, which is
not the platform's to fix. One sentence of the corpus case that replays it said the same thing and is
corrected the same way.

**The corpus moved to v27, 49 cases**, and this SDK needed no change to pass it. The new case is a
second in-band stream failure whose payload is an **object** rather than the literal string
`stream interrupted` — added because mutating a runner to compare that exact text left all 48 cases
green. One fixture carried an in-band error and it carried the one message, so the corpus could not
tell *detect the key* from *compare the string*, which is precisely the distinction the platform asked
for and the one this SDK has always implemented. It passed on the first run; what changed is that it
is now **pinned rather than lucky**.

### 1.0.0rc6 — 2026-10-02

**`1.0.0rc5` on PyPI cannot list the catalog. This release is that fix, and it is the reason to
upgrade.** `client.models.list()` raised `MissingCredentialsError` from a published version, because
Python passed `authenticate=False` to the one endpoint the vendored guide named as public — and
`PRM-167` closed it. Go, Rust and Swift carried the same false comment and survived by accident:
their code sent the token anyway.

#### Announced, as the surface diff requires: `Modality` widened, and the preflight now refuses more

`Model.modality`'s `Literal` gains `rerank`, `classification`, `zero_shot` and `typed_decision`. The
annotation is `Literal[...] | str`, so nothing a caller passes or reads becomes invalid — **but the
behaviour behind it changed, and that is the part worth knowing**:

With `verify_modality=True`, these pairings used to reach the gateway and come back `400
modality-mismatch`. They are now refused locally as `ModalityMismatchError`, before the request:

    a rerank model on chat            a classification model on chat
    a chat model on /v1/rerank        anything with its own endpoint on /predict

Same failure, different exception, one round trip earlier. A caller catching `APIError` for this and
not `AxoniumError` will stop catching it. The flag is off by default, so a caller who never set it
sees nothing.

The reason it changed is that the check was **not working at all** for `rerank`: the accepted set was
right, the separate hand-kept list of *known* modalities had never heard of it, and the check returns
early on a modality it does not recognise. `qwen3-reranker` on chat was allowed. The known set is now
derived from the per-endpoint sets, so the two cannot drift again.

And the reason the check exists has expired, which is corrected in its prose but changes nothing
about its behaviour: the gateway used to answer chat on an embedding model with `200` and degenerate
billable output. `RM-66` closed it, measured on all six combinations. The check stays — it saves a
request and a rate-limit unit — and no longer claims to save money.

#### New

- **`client.predict.create(model, body)`** — the pass-through route, `POST
  /v1/models/{model}/predict`, for `classification`, `zero_shot` and `typed_decision`.
  `PredictResult.value` is typed `Any` because `sst2-clf` answers a **top-level array**.
- **`PredictResult`**, **`PredictBackendRejectedError`**, **`CapacityExhaustedError`**,
  **`UnknownParameterError`**.
- **`Model.payload_schema`** — the field that identifies which body a model takes. Dispatch on it
  rather than on `modality`: `sst2-clf` and `von-decide` are both classifiers and want different
  bodies.
- **`ChatCompletionRequest.response_format`** — structured output. The allowlist had been warning
  that the gateway did not support it, which was false in both halves and stripped the field.

#### Also

The preflight no longer refuses a model missing from the catalog. Since `PRM-167` the catalog holds
only the models the token has a grant for, so absence has two causes this SDK cannot tell apart — not
registered (`400 unknown-model`) or not granted (`403 forbidden`) — and refusing locally pre-empted
the `403` whose whole job is to name the missing scope.

Streams retry once when rejected *before* the first byte, matching Go and Rust. All three documented
"never" and did 1, 3 and 3.

**The pass-through route: `client.predict.create(model, body)`.**

`POST /v1/models/{model}/predict` serves three modalities the OpenAI surface has no shape for ---
`classification`, `zero_shot` and `typed_decision`. The body goes to the engine verbatim and its
answer comes back verbatim, so `PredictResult.value` is typed `Any`.

**Not defensive typing --- a measured constraint.** The three live engines answer:

    sst2-clf     [{"label":"POSITIVE","score":0.978}]        <- a top-level ARRAY
    von-decide   {"sequence":...,"labels":[...],"scores":[...]}
    laya-decide  {"model":...,"answers":{...},"routing":{...}}

A type that assumed an object would have failed on the first engine the platform shipped here, and
would have reported "this is not JSON" about valid JSON. There is no `classify(text)` either:
`sst2-clf` and `von-decide` are both classifiers and want different bodies, so a typed method would
promise a stability the route does not have.

`payload_schema` is now modelled on ``Model``. The spec names it as the field that identifies
the body shape, it is populated on all ten live models, and without it the route ships with no way
for a caller to know what to send.

Four contract cases, recorded live, replayed by all four SDKs --- including the first recorded
`predict-backend-rejected`, the one catalog row whose status is the engine's rather than the
gateway's, with the engine's own `backend_error` preserved.

**The modality guard refused nothing on `/v1/rerank`, and had not since rerank shipped.**

The endpoint passed its accepted set correctly. The separate, hand-kept list of *known* modalities
had never heard of `rerank`, and the check returns early on a modality it does not recognise --- so
the call could not compare anything. Measured:

    qwen3-reranker  on chat    -> ALLOWED   (the doc comment promised 400)
    qwen3-embedding on rerank  -> ALLOWED   (same)
    qwen3-embedding on chat    -> refused   <- the only pairing that worked

One truth in two places, and only one of them was updated. The known set is now **derived** from the
per-endpoint sets, so there is one. Adding the three predict modalities would have been dead on
arrival for the same reason, which is why this came first.

The test standing guard over exactly this had been given the wrong answer key: it asserted
``set(ENDPOINT_MODALITIES) == {"chat", "embeddings", "images"}``, a literal typed into the test rather than the invariant its name claimed. A literal cannot
notice a new call site, so it passed throughout. It now reads the call sites out of the source, and
fails if an endpoint calls in without a row, or a row exists for no endpoint.

**The reason the guard exists had expired, and nothing could have told us but a measurement.**

Every justification --- module prose, the error message, a test name, a test's `match` --- said the
gateway accepts chat on an embedding model and answers `200` with degenerate billable output. All six
wrong-modality combinations measured against a live deployment answer `400 modality-mismatch`.
`RM-66` closed it, **the guide documenting `RM-66` is vendored in this repo**, and we read it for
error-catalog rows without re-reading the prose against the code.

The guard stays: it saves a request and a rate-limit unit, and the spec says a client-side check of
this kind can stay. It no longer claims to save money. Mutation testing could never have found this
--- mutating the code turns the tests red correctly, because the code was never wrong. Only
comparing a claim against a live deployment finds a premise that died.
Re-vendored at `2026-10-01 · PRM-164/167/173`. §2.7 is rewritten and renamed, from "Client types
--- who may hold a credential" to "Credentials --- whose they are, and who issues them".

**The axis moved.** The old section's rule was *confidential clients only*, with a distributed app
refused because it cannot keep a secret. The new rule is that **a credential identifies whoever pays
for consumption**, and the device stops being the question:

- An *integrator's* credential must never ship inside a distributed application --- a copy on every
  user's device is a copy of the identity that is granted models and billed.
- An *end client's own* credential may live on that client's own devices, phone and laptop alike.
  The principal, the grants and the bill are theirs, so a leak costs them their own account.

An app with a pasted secret is still a public client in RFC 8252's terms; it is accepted here when
the secret and the bill belong to the same person. That is the distinction the old §2.7 did not
separate, and it is the one that answers `A-34`.

Also now stated as a rule rather than an absence: **issuance is always a human administrator**.
There is no registration endpoint and no API an integrator can call to mint credentials for its
users, because issuing one opens a billing account. An application therefore has to treat **"no
credential yet"** as a first-class state rather than an error, and the request goes to the platform
rather than to the integrator. One credential per client, used on as many of that client's own
devices as they have --- per-device credentials are not issued, so an app assuming one install per
credential is wrong for any user with a phone and a laptop.

**Nothing in these three SDKs changes.** They take a token, or a callback that returns one, and
nothing about who obtained it is theirs to know. The section matters for the Swift SDK, whose
documented example reads a secret from the Keychain --- which the new §2.7 permits when that
credential is the end client's own, and still refuses when it is the integrator's.

`PRM-170` is superseded in substance rather than relaxed or tightened.

Re-vendored at `2026-09-29 · PRM-164/167/170`, which adds §2.7, "Client types — who may hold a
credential".

It answers `A-30` and reframes it: the question was never about mTLS. A client certificate shipped
inside an app somebody downloads is a secret shipped inside an app somebody downloads. What is
actually underneath is that **`client_id` is the billing principal** — grants and invoices are keyed
to it — so a `client_secret` on an end user's device is the *integrator's* identity copied onto
every one of their users' machines. Not a shape to harden; a shape not to have.

PKCE is not the alternative either, and not on cost: per-end-user identity has nowhere to live in
the platform's authorization or billing model, and the auth-service has no authorization endpoint
at all.

The answer that unblocks a distributed app is an integrator-controlled backend holding the
credential, with the app authenticating against that. Nothing here changes: these three SDKs run on
servers, which is the supported shape. It matters for the Swift SDK, whose documented example is an
on-device secret out of the Keychain.

**The modality preflight said "not in the catalog" about a catalog it can no longer see all of.**

With `verify_modality` on, a model missing from the catalog was refused locally as
`unknown-model`, on the stated grounds that the gateway reports the same thing and refusing only
saves the round trip. That was true while the catalog was the platform's full public list. Since
`PRM-167` it holds only the models the token has a grant for, which gives absence two causes this
SDK cannot distinguish:

    not registered at all    -> the gateway answers 400 unknown-model
    registered, not granted  -> the gateway answers 403 forbidden

Refusing locally told a caller to check the spelling of a name that was spelled correctly, and
**pre-empted the `403` whose entire job is to name the missing scope** — the error these SDKs work
hardest to make useful. So the request now goes, and the gateway answers a question only it can
answer. A typo costs one round trip; a missing grant gets diagnosed. That is the right way round.

What the preflight still does is the thing it was built for and can still prove: a model the
catalog **does** show carries its modality, so a mismatch is a fact rather than an inference.

*Corrected further down in these same notes: this paragraph claimed the gateway would not catch that
mismatch, which stopped being true at `RM-66` and was measured false on 2026-10-02.*

Three tests pinned the old behaviour and were correct when written. The Go one now asserts the
boundary with a request count rather than a server-side rejection, because only a number can tell
"stopped locally" from "reached the gateway".

**Not measured against a live deployment.** The only gateway available grants every model it has,
so the registered-but-ungranted case could not be produced. The reasoning stands on the guide's own
statement that `unknown-model` is checked before any scope check; it is not a measurement, and is
labelled as such rather than presented as one.

Re-vendored at guide revision `2026-09-29`, and the catalog means something narrower than it did.

`PRM-167` closed `GET /v1/models` to anonymous callers — which AXO-117 already fixed, by measuring
rather than by reading. What the guide adds is the half that was not visible from a `401`: the
endpoint now returns **only the models the token holds `model:<id>` scope for**, which makes
`models.mine()` an alias of `models.list()`. Same requirement, same filtering, same response.

So three claims in these SDKs were wrong in a way no test could catch: "everything the deployment
serves", "the full catalog", "the public catalog is not the answer to what can I call". The answer
is the same from both endpoints now.

And the fact this session got wrong by inference before the guide stated it: **an empty list means
the token holds no grants, not that the platform has no models.** Two different facts that only an
operator can distinguish. Measuring an empty `/v1/models` and concluding the registry was empty is
exactly the mistake, and it was made here today.

The guide also fixed the header exclusion list this SDK reported in `A-29`: `X-Request-ID` and
`X-Trace-ID` are present on endpoints the old list excluded, and only the rate-limit headers are
actually absent.

The catalog call carrying a credential is pinned by the corpus, not just fixed in the code.

`GET /v1/models` stopped being public on 2026-09-29 and the fix landed in four SDKs — and in zero
contract cases. That is the shape this corpus exists to prevent, for the fourth time: a behaviour
corrected in N languages and held in none, so the fifth SDK inherits nothing.

Manifest v25 asserts it on `catalog-list`, through a third form of header assertion:
`request_headers_present`, by name and with no value. The value could not be pinned — an
`Authorization` bearer is each runner's own test token, so asserting it would assert about the
harness rather than about the SDK. It joins `request_headers`, which compares a value, and
`request_headers_absent`, which forbids one.

Mutation-tested in both directions of the released break: restoring `authenticate=False` on the sync
`list()` fails `catalog-list` sync, and on the async one fails async. That flag is exactly what
shipped in `1.0.0rc5`, where `models.list()` returned `MissingCredentialsError` against the closed
endpoint — the corpus now refuses it.

**`GET /v1/models` stopped being public, and this SDK was the one that believed the documentation.**

The platform closed the endpoint without announcing it. Measured against a live deployment:

    GET /v1/models  with no token  ->  401 missing-credentials

Python passed `authenticate=False` there, because the guide said it was the one public endpoint and
this SDK did what the guide said. `client.models.list()` — the simplest call it has — returned
`MissingCredentialsError` from a released version. Go, Rust and Swift survived by accident: their
comments made the same claim while their code sent the token anyway.

Fixed, and the false comments in the other three corrected with it, along with three lines of
`docs/02-calls.md`.

**Three tests were pinning the broken behaviour**, one of them a security test, and all three were
right when they were written:

    test_the_catalog_call_sends_no_credential_at_all
    test_listing_the_public_catalog_sends_no_token [sync] [async]

They asserted that listing the catalog costs no credential, which was the correct property while
the endpoint was public — spending a token where none is wanted is a real thing to guard against.
They went on passing while the call returned 401 in the field. The security test now guards what
never depended on the endpoint being public: the client secret goes to the token request and
nowhere else, and a bearer token on the wire is the design rather than the bug.

The vendored guide still says otherwise at its line 647. That is a question for the platform team,
not something to paper over here.

The gap the corpus declared is closed by a measurement, not by a repair.

`stream-idempotent-replay` is re-recorded from a complete capture: two streamed calls with the same
`Idempotency-Key`, the second one's bytes and **all** of its headers. So
`meta.idempotent_replay_of` and `meta.request_id` are now asserted, because they were finally
measured on a stream rather than assumed from a guide sentence that happened to read true. A replay
carries its own `request_id`, distinct from the billed one, and the billed one is what
`X-Idempotent-Replay-Of` names — confirmed against `/v1/usage` in the same session, where the
original id returns `200` and the replay's own returns `404`. There is no streaming/non-streaming
asymmetry, which was the open question.

Every expectation was derived by running this SDK's own accumulator over the new bytes rather than
carried over from the old case, and the two bodies were compared before recording.

**The complete headers say two things a partial capture hid.** A replay names **no instance** —
neither `X-Prometheus-Instance` nor `X-Prometheus-Instance-Id`, where the original carried both,
which is correct because no replica served it. And, undocumented anywhere in the guide, **a replay
consumes request budget**: `remaining-requests` goes 59 → 58 across the two calls. Not generated, not
billed, and still counted against the RPM window. The case asserts `58`, so the number itself is the
evidence, and the question is with the platform team.

The tripwire that stood guard over the gap is deleted, which is what it was written for. The general
check it backstopped stays: no `meta.*` assertion may outrun the headers its case recorded.

A declared gap in the corpus is now an enforced one.

`stream-idempotent-replay` says `X-Idempotent-Replay-Of` and `X-Request-ID` were never captured for
a streamed replay, so asserting `meta.idempotent_replay_of` there would fail against an SDK doing
exactly the right thing. The obvious repair is to add the header to the case — which turns a thin
recording into an invented one. Nothing held the sentence that said not to.

Two checks now do, because one cannot. The first refuses any `meta.*` assertion whose sourcing
header is absent from that case's recorded bytes, so the accidental path fails saying *capture it
before asserting it* instead of looking like an SDK bug. That check cannot catch the deliberate path:
from inside a repository a recorded header and a typed one are the same bytes in the same file. So
the second is a tripwire on this one declared gap, written to be **removed** rather than satisfied —
whoever measures those headers for real deletes it in the same commit, which is a deliberate act with
a diff that says so.

**The question itself stays open, and it is the platform's.** The guide documents both headers on a
replay and says streamed replays work, so the expectation *reads* true; what is missing is anyone
having measured it on a stream. Attempting the capture locally established only that it cannot be
done here: the deployment answers `/health` with `{"status":"ok"}` while `GET /v1/models` returns an
**empty catalog**, so there is no model to generate against — which is incidentally a live instance of
the `/health` concern already open with the platform team.

A stream case can assert a field.

Nine of them could not. The streaming branch of all three runners read the stream-shaped keys --
`content`, `chunks`, `usage`, `tool_calls` -- and ignored `fields` in silence, so nothing about a
stream's `meta` was expressible: not the correlation ids, not the rate-limit budget, and not the two
idempotent-replay flags that `chat-idempotent-replay` has pinned since the day it was recorded. A
streamed replay that lost its entire `meta` passed all 44 cases.

Manifest v23 adds `meta.idempotent_replay` to `stream-idempotent-replay`, and the three runners
resolve `fields` on a streamed case against `meta` and nothing else -- `content`, `chunks`, `usage`
and `tool_calls` each already have a key of their own, and a second way to say the same thing is how
two ways eventually disagree. A test holds that restriction rather than a comment.

**What is still missing there is a recording, not an assertion.** This case captured only
`Idempotent-Replay`, where its non-streaming twin captured `X-Request-ID` and
`X-Idempotent-Replay-Of` too, so `meta.idempotent_replay_of` and `meta.request_id` are deliberately
*not* asserted: the headers are absent from these bytes, an SDK reporting them empty is correct, and
an expectation for them would be invented. The gap is declared in the case rather than left to be
discovered, because `idempotent_replay_of` is the only id that carries a usage row -- the replay's
own id does not.

Mutation-tested in all three: a stream that loses its `meta` now fails, where it used to pass.

The corpus now checks what the SDK **sent**, not only what it received.
Six cases supplied an `Idempotency-Key` and not one asked whether it was sent. The only assertions
about what went **out** were on the token endpoint, so an SDK that accepted a key and dropped it
passed all six: the four error cases replay a recorded envelope the mock serves regardless, and the
two replay cases assert the bytes that come **back**. A key that never leaves the process turns the
retry it exists to protect into a second billable generation — the premise the whole streamed-retry
decision rests on.

Manifest v22 adds `expect.request_headers` and `expect.request_headers_absent`, and the three
runners honour them. `chat-idempotent-replay` and `stream-idempotent-replay` now require the key on
the wire; `chat-completion-basic` requires that neither an idempotency key nor an instance pin is
**invented**, because a key the caller never asked for makes a retry silently replay a stale result,
and an invented pin opts them out of load balancing and failover without saying so.

Mutation-tested in all three, both directions: dropping the key on a stream fails the replay case,
and inventing one fails the basic case.

**And it found a comment that was false in two of the three.** Python and Go both stated that the
gateway ignores an idempotency key on a streamed request and that the SDK therefore *refuses to send
one* — while the code beside them sent it, and while a contract case recorded from a live deployment
showed the platform replaying a stream 6 times out of 6. Nothing asked what went out, so the false
comment and the true code sat in one file for as long as nobody read both. The boundary that is real
is narrower and was always documented correctly elsewhere: a key replays a stream the gateway
**finished** and whose delivery dropped, never one the model itself broke.

A streamed request rejected **before the stream begins** is now pinned as *recognised* -- the
precondition the retry above assumes, and the one thing nothing asserted.

No behaviour changed here: the status check that precedes SSE parsing has always been in place. What
changed is that removing it now fails. Measured by the Swift SDK team: reading the body as a stream
without first looking at whether the status was `4xx`/`5xx` still passed **all 40 cases of manifest
v19**. An SDK without that check turns every rejection into a silently empty response -- no error,
no content, and nothing for a caller to correlate -- and the corpus said that was fine. v20 pinned
what a recognised rejection leads to and never that it is recognised, which is the shape of AXO-110
in a different place, and of AXO-108 before it: fixed in three languages, pinned in none.

`expect.kind` for a stream that fails before it begins is `error`, **not** `stream_error`, and the
two are different contracts. `stream_error` is the in-band failure of a stream that has already
begun, after the `200`/`text/event-stream` headers are committed, and it is never retried. This one
arrives *instead of* a stream, as an ordinary status, and is -- which is what the guide (3.3) means
by a client that sets `stream: true` not getting a different error contract for doing so.

The runner could not have expressed this before. Error cases are selected by `kind == "error"` and
dispatched by operation through `call_sync`/`call_async`, neither of which had a branch for
`chat.completions.stream` -- a streaming error case would have died on "unhandled operation". Both
now open the stream *and* iterate it, because an SDK that hands back a stream where a status belongs
must not pass by reading the refusal as an empty body.

Manifest v21 adds two cases, both on `chat.completions.stream`.
`stream-rejected-before-it-begins-is-an-error` replays the `400` that `PRM-143` recorded live on
2026-09-27: the engine's own status and OpenAI-shaped body, passed through verbatim rather than
wrapped in a problem+json envelope, so `error_type_suffix` is null and the correlation ids exist
only in the headers -- which keeps AXO-108's header fallback pinned on the streaming path too.
`stream-rejected-before-it-begins-is-retryable-when-the-backend-is-unavailable` is the
`503 backend-unavailable` that justifies the retry v20 added. It carries no `Retry-After` in header
or body, unlike every other retryable error in the corpus, because the guide (5.2) confirms this
variant supplies no backoff signal at all; a shortened window would have been cheaper to test and
would have pinned a value the gateway never sends.

Mutation-tested in all three languages: removing the status check fails both new cases, and takes
`stream-retried-when-rejected-before-it-begins` with them.

The Swift SDK covers this by hand today, in `StreamRejectionTests.swift`. With the corpus holding
it, those tests are redundant and that team removes them.

A streamed request rejected **before the stream begins** is now retried, as every other request
already was.

`ChatCompletionStream` and `AsyncChatCompletionStream` take a way to *open* a stream rather than one
already-built connection, so a refused open can be reopened. The retry decision itself is not
reimplemented there: they call the same policy the non-streaming loop uses, so `Retry-After`, the
attempt budget, the cooldown registry and the log line are shared rather than similar.

Two things follow that were previously unavailable on a stream. `meta.attempts` and `meta.waited_s`
now report what a reopen cost instead of always claiming one attempt; and a failed open is diagnosed
the way a non-streaming failure is, which matters most here, since a denied `inference:stream` scope
is where callers trip.

The three had never agreed, and none of the three disagreements was a decision. Measured on
2026-09-27, counting requests that reached the server for a `429` on a streamed
`POST /v1/chat/completions`: **Python 1, Go 3, Rust 3** -- and all three documented never retrying a
stream at all. Python's stream opened its connection by another route and missed the shared retry
loop; Go and Rust ran it because nobody had excluded streaming from it.

The two that contradicted their own documentation were right. A stream can only fail this way
*before* any body byte exists -- the gateway reads the engine's status before the
`200`/`text/event-stream` headers are sent -- so nothing was generated and nothing was billed, and
reopening is a first generation rather than a second. It is also the only retry available: the
gateway performs **no** internal retries on a streamed request, so a `503 backend-unavailable`
arrives there after one attempt rather than three.

It could not be said a week earlier. Until `PRM-143`, landed 2026-09-27, a stream rejected before it
began arrived as a `200` whose body was nothing but `data: [DONE]` -- indistinguishable from a
legitimately empty answer, so "retry the rejections that precede the 200" named nothing. The gateway
now returns the engine's real status, and a connection that never opened returns
`503 backend-unavailable` in the problem+json envelope.

What has no exception, in all four SDKs: **a stream that has already begun is never retried.** There
the failure arrives in band, part of the answer was delivered, and part was billed.

Manifest v20 pins both halves, because three hand-written suites had pinned neither. The corpus
gained the shape needed to express it: a case can now serve an ordered *sequence* of responses, and
assert how many requests reached the server. `stream-retried-when-rejected-before-it-begins` serves a
`429` then the stream and requires two; `stream-not-retried-once-it-has-begun` queues a healthy
stream behind an interrupted one and requires that it is never reached -- an SDK that retried there
would pass every assertion of `stream-interrupted` while billing twice and returning the wrong
answer. Both were mutation-tested in all three languages: breaking the retry fails the first,
and the count is what catches it.

Agreed with the Mundus team so the Swift SDK is born with the behaviour rather than inheriting
whichever of the three it happened to read.

A contract case now pins reading the correlation ids out of the headers.

The behaviour shipped in all three SDKs on 2026-09-27 with one hand-written test per language and
nothing added to the shared corpus, so it was fixed three times and pinned zero. Measured by
building a fourth SDK against the corpus alone: removing its header fallback and replaying all
fourteen error cases passed every one of them. Manifest v19 adds
`error-correlation-ids-only-in-the-headers` -- a real 422 body with neither id, both ids in the
headers -- and removing the fallback from each of the three now fails it.

It is also the first case expecting **no** `type` at all, which two of the three runners could
not express: Rust's panicked on the null, and needed a name for the kind an absent type produces.

Two error types the platform added on 2026-09-27 are mapped: `503 capacity-exhausted` and
`predict-backend-rejected`.

`capacity-exhausted` means every replica of a model is busy rather than broken, which is the one
`503` where waiting is the whole remedy. It ships disabled on the platform, so nobody has met one
yet; it is mapped before anyone does.

`predict-backend-rejected` is the first catalogued error with no fixed status. The `predict` route
passes the body to the engine, so a refusal keeps the engine's status and its body, and the name
claims no cause. Retryability is therefore read from the status rather than from the name, and the
engine's own body is reachable through `backend_error`.

**The check that holds the catalog to the guide could not see the second one.** It required a
three-digit status and the guide's row says `4xx`, so the row never parsed and the set comparison
found nothing missing — a guard built to catch a row that disappears, blind to a row that never
arrived. It now validates the status cell instead of selecting on it, and an unreadable one is a
failure rather than a skip.

An error whose body is not a complete problem+json now still carries its correlation ids.

They were read from the body only. A validation failure forwarded verbatim from a backend has
neither id in its body and both in its headers, so the caller was handed an error with nothing to
take to the platform team. The body still wins where it has them, so nothing changes on an envelope
that honours the contract.

`UnknownParameterError` maps `400 unknown-parameter`.

Raised only when a request carries `require_parameters: true`, which asks the gateway to refuse an
unaccepted field instead of dropping it. It was documented in the platform guide we had already
vendored, and nothing here noticed: the parity guard holds the catalog to the SDKs, and nothing
held the guide to the catalog. That direction is now checked too.

**Structured output works.** `response_format` is now a declared field, forwarded verbatim.

The platform started honouring it on 2026-09-18 and told us. This SDK went on warning that it was
unsupported, and shipped a release five days later still saying so. In Python the field was also
*stripped*, so structured output was not reachable at all.

The answer comes back as a JSON **string** in the message content — parse it yourself
(`json.loads(completion.content)`). It is not parsed here for the same reason tool-call `arguments` is not: a generation
stopped by `max_tokens` leaves it truncated, and a response object that raises from the inside is
worse than one that hands you what arrived.

Documentation only, and it matters because it had become wrong.

The rate-limit envelope no longer omits `trace_id`, and `X-RateLimit-Scope` now reaches the `429`
as well (platform guide `2026-09-19b`). Four statements in this SDK still described the old
behaviour. They now describe the current one, and say which deployments still behave the old way.

The body fallback for `scope` stays. It is no longer needed against a current deployment and is
kept for one predating the fix — which the platform team explicitly recommended.

Also documented: what happens to an `Idempotency-Key` whose request *failed* is undefined, is the
gateway's decision rather than this SDK's, and is being asked.

### 1.0.0rc5 — 2026-09-20

No change to the public surface beyond the additions above. Verified rather than asserted: the
release rehearsal now compares this tree against the wheel PyPI actually served, and reports every
symbol and field that was added, removed, or changed type.

`1.0.0rc5` is additive — three new fields, nothing removed, **nothing re-typed**. That last part is
the one that matters to a consumer reading through a tolerant accessor, since a type change there
neither fails nor warns.

`RateLimitSnapshot.scope`, and `client.rate_limits` keyed by it.

The platform gave `/v1/embeddings`, `/v1/rerank` and `/v1/chat/completions` separate rate-limit
budgets. That made `client.last_rate_limit` a number from whichever endpoint answered last, with
nothing in the numbers saying so — a dashboard drawing "requests remaining" kept drawing a
plausible figure from another bucket. Ask about a particular budget by scope instead.

Measured against a deployment rather than taken from the announcement: `X-RateLimit-Scope` is on
successful responses and **absent on the 429**, where the body carries `"scope"` instead. So the
one response whose budget most needs attributing — the one telling you a bucket is exhausted — is
read from the body when the header is missing. The header wins when both are present.

A retried call can now explain its own duration without anyone reading a log.

A `Retry-After` of 0–60s, respected as it should be, looks from outside like one slow call among
fast ones. Three separate teams have reported that as a hang. `1.0.0rc4` began logging the wait at
INFO, which was necessary and not sufficient: this SDK does not configure the host application's
logging, so the line is invisible until somebody opts in — at every entry point, and again at the
next one added. A latency metric cannot read a log line at all, and a latency metric is where this
keeps being seen.

`meta.waited_s` and `meta.attempts` on every response now carry it as data.

Subtract it from a wall-clock reading to get what the platform actually spent: the wait is
deliberately excluded from every duration this SDK reports, because sleeping is not service time.

Not covered: a call that waited and then failed anyway, which is the one whose duration most needs
explaining. Errors carry no response metadata today.

### 1.0.0rc4 — 2026-09-16

Five error types the platform ships and nobody had mapped: `inconsistent-model-group`,
`unauthorized`, `invalid-date`, `invalid-range` and `range-too-large`.

They surfaced from the other side of a gap we reported. We told the platform that five suffixes
they send appear nowhere in their guide; they wrote a test comparing every `type` the gateway
raises against that table, and it found ten. Five were ours, and these five had been there longer
and nobody had noticed.

Three of them belong to the admin-scoped usage export, which this SDK does not call. They are
mapped anyway so the taxonomy is complete and an unknown suffix means what it says.

`POST /v1/rerank`, for models with `rerank` modality.

A reranker is a cross-encoder: it scores a query against each document and returns them ordered. It
generates nothing, so there are no completion tokens and billing is prompt-only.

Two things matter if you were doing this through the chat endpoint. The **whole document set is one
request**, not one per document — against a 60 RPM budget, scoring 50 candidates costs 1 unit rather
than 50. And each result's `index` points into the `documents` you sent, never into the results, so
a reordered result stays attributable to its input.

- `client.rerank.create(...)` (and the async mirror), returning `RerankResponse`, whose `.ranking` gives the input indices best-first. An empty `documents` list is refused before the wire, which the gateway would answer
  `400 validation-error`.

The SDK now says when it is waiting, and for how long.

The platform's `Retry-After` on a `429` is seconds until the window resets, so it runs 0–60. An SDK
that respects it — as it should — looks from outside like one slow call among fast ones, and that
arrives as a latency bug report. A wait long enough for a person to notice is now reported at INFO
with `delay_s`; sub-second backoff stays at DEBUG, because the noise worry is frequent small retries
rather than the rare long one.

The wait is reported as a wait, never folded into `duration_ms` — that is measured per attempt and
deliberately excludes time spent sleeping. Time waiting is not time the gateway took.

A token response in neither documented envelope is now a transport failure rather than an OAuth2
one.

A `4xx` carrying `error` is an OAuth2 outcome; a `5xx` carrying `type` is the gateway's own
problem+json. A body with neither — an HTML error page from a proxy or load balancer that answered
instead of the gateway — used to be reported as an OAuth2 failure, which tells a caller their
credentials are the problem. That is both wrong and the most expensive wrong answer available here:
the obvious next step is rotating a perfectly good secret.

`spec/errors.json` and this SDK's error mapping are now held together by a test (`tests/test_catalog_parity.py`): every
catalogued error must map to a class, and its retryability must match the catalog.

Go has had this from the start and it earned its keep the day the platform added two token errors —
it refused the change until both had a mapping, then refused again until their retryability matched.
This SDK had no equivalent, and shipped one of them with the wrong retryability until a hand-written
test caught it.

A failed token request is now typed by its envelope rather than read as OAuth2 unconditionally.

A `4xx` is an OAuth2 outcome in the RFC 6749 shape — wrong credentials, a scope the client does not
hold — and is never worth retrying. A `5xx` is the gateway failing to reach the auth-service,
arrives as problem+json, and `upstream-unavailable` **is** worth retrying. Reading both as OAuth2
left the 5xx with no type and no retryability, so a momentary blip looked exactly like bad
credentials and the request was abandoned rather than retried.

`not-configured` shares that status and is deliberately *not* retryable, which is why the suffix
drives the decision rather than the status.

**One address, not two.** The gateway serves both the inference API and `/oauth2/token`, so the
auth-service address is **gone from this SDK** rather than defaulted.

Keeping it as a field that defaults to the gateway would still have taught every consumer that a
second address exists. It does not, for them: the platform's auth-service is now reachable only
from inside the deployment, which is what it was always for. Removing the field removes the failure
it enabled — pointing the SDK at a self-hosted deployment used to mean changing two addresses, and
forgetting the second left the client asking the **official platform** for a token to use somewhere
else, with nothing erroring.

**Breaking**, and deliberately so while the surface is pre-1.0: `auth_base_url` and `DEFAULT_AUTH_BASE_URL` are **removed**, along with `AXONIUM_AUTH_BASE_URL`. A deployment whose gateway
has no token endpoint wired up answers `not-configured`, which says exactly that rather than
failing obscurely.

Per-request usage lookup: what one of your own requests was charged, and why it stopped.

Both aggregate usage endpoints require `admin:read`, which a normal client neither has nor should
have — so `termination_reason` existed for callers who could not read it. The platform shipped this
after we made that case; this is the client half.

A replay has its own request id and no row of its own, so looking that id up is a `not-found` —
correctly, since a replay is not billed. `meta.idempotent_replay_of` names the generation that was
charged; look *that* up. The round trip is verified live in all three languages.

`termination_reason` is a plain string, not an enum. The platform proposed a fourth value this week
and withdrew it; the next one may not be withdrawn, and a closed set would turn a new value into a
parse failure for a caller who only wanted the token counts.

- `client.usage.retrieve(request_id)` (and the async mirror), returning `RequestUsage`. `NotFoundError` is new.

`meta.idempotent_replay_of` carries, on a replay, the request id of the generation that was
actually billed.

A replay has its own request id and no usage row of its own, so looking that id up returns `404` —
correctly, since replaying reaches no model and is not billed. This header names the id that does
resolve, which makes it the only path from the response a caller received to the charge it
corresponds to. `None`/empty on anything that is not a replay.

The quickstart in the README used a model name that is not registered, so copying it produced
`400 unknown-model` rather than a completion. Examples and doc comments now use a real slug, and
each README says what a slug is: it never changes and is never reused, so pinning one is safe, but
which ones exist depends on the deployment and on what the token is granted — the catalog endpoint
is the source of truth, not the README.

Tool calls are now typed like everything around them.

They used to arrive as raw dicts while the message carrying them was a model. The asymmetry cost a
consumer real work twice over: reaching in by hand to read a name, and converting back to dicts to
feed a call into the next request. Both directions are now the same type.

`arguments` deliberately stays the model's own JSON **string** rather than a decoded object.
Decoding it at parse time would raise from inside a response model, for a caller who only wanted to
see what the model had managed to say — a generation stopped by `max_tokens` leaves a string that
was never going to parse. Decoding is a separate, explicit call that fails loudly, and the raw
string stays reachable either way.

**Breaking**, and deliberately so while the surface is still pre-1.0: anything indexing a tool call
as a dict/map/`Value` needs the field instead.

- `ToolCall` and `FunctionCall`, returned by `completion.tool_calls` and `stream.tool_calls`, and
  accepted by `Message.tool_calls` on the request side. Plain dicts are still validated into the
  model there, so request-building code written before this keeps working.
- `call.parse_arguments()` decodes the arguments, raising `ToolCallArgumentsError` — which carries
  the offending call — rather than letting `json`'s own `ValueError` escape. `call.name` reads the
  function name without reaching through `call.function`.
- Streamed *fragments* stay raw dicts on `chunk.tool_call_fragments`. A fragment is not a call: it
  carries `index`, which the complete shape has no field for, and only a slice of the arguments.

Streamed tool calls are now reassembled for you.

A tool call arrives split across as many deltas as it takes — `{`, `"`, `city` — and the fragments
are individually invalid JSON. Only the first carries the identity, and **`index` is the
correlation key**, because `id` never repeats. Every consumer was writing that join by hand.

The assembled call is **byte-for-byte the shape a non-streaming completion returns**, `arguments`
included: still a JSON *string*, not a decoded object. That is deliberate — the same caller code
handles both, and a stream cut short by `max_tokens` hands back the fragment that did arrive
instead of raising or dropping the call. Check `finish_reason` before decoding.

A second contract case was recorded live for this: a single-call recording cannot tell `index`
correlation apart from any other strategy, so a stream with two concurrent calls was recorded to
give the case teeth. The manifest is now 25 cases, and all three SDKs replay both.

- `stream.tool_calls` on `ChatCompletionStream` and `AsyncChatCompletionStream`, populated as the
  stream runs and complete once it ends.
- `chunk.tool_call_fragments` exposes the raw fragments for a caller who wants to watch them
  arrive. They remain unusable on their own; this is not the accessor to reach for.

### 1.0.0rc3

`rc2` reached TestPyPI missing six error exports and was replaced rather than patched, since a
version is never reusable. Everything learned from running `rc1` against a live deployment and from three rounds of
coordination with the platform team. No breaking change to code written against `rc1`; one
behaviour change worth reading.

**Credentials are now the only required setting**

- `auth_base_url` and `gateway_base_url` default to the official Prometheus platform. Precedence is
  unchanged — explicit argument, then environment, then the default — and overriding one does not
  force restating the other.
- **The default addresses are provisional.** The platform has not moved to its cloud host yet, so
  they currently point at a local deployment. Upgrading will pick up the new address automatically;
  **a pinned version will not**, and the release that changes them will say so prominently.

**Idempotency**

- `idempotency_key` on `chat.completions.create()`, `.stream()`, `embeddings.create()` and
  `images.generate()`. A repeat with the same key and body returns the stored result without
  reaching a model, recording usage, or counting against the spend cap.
- **A client-side timeout is now retried — but only under a key.** Without one the old rule stands:
  the backend is probably still generating, so a retry would be a second billable generation.
- Four typed refusals, only one of them retryable: `InvalidIdempotencyKeyError`,
  `IdempotencyKeyReuseError`, `IdempotencyInProgressError` (retryable, carries `retry_after`) and
  `IdempotencyResponseNotRetainedError` — which is proof the original succeeded.
- Key length is checked before the wire, so an over-long key names its own problem.

**Multi-instance deployments**

- `instance` pins a call to one replica, by label (`"#2"`) or full id, and is kept across retries.
  It opts out of load balancing *and* failover, so it is for reproducing a problem rather than for
  normal traffic.
- `meta.instance` and `meta.instance_id` on every response — the values to quote when reporting a
  slow or odd one.
- `UnknownInstanceError` for a pin that names something not serving the model.

**Usage and correlation**

- `usage.cache_read_tokens`: how much of the input came from cache, read from the reported
  `prompt_tokens_details` where the gateway supplies it and derived from `timings` otherwise.
  `input` **includes** the cached prefix, which is the convention the three fronts settled on.
- `meta.idempotent_replay` says whether a response was replayed rather than generated — so a
  `usage` on a replay is not added to a running total by mistake.
- `ValidationError` for `422`, which now arrives in the same problem-details envelope as every
  other error.

**Fixed**

- Six error classes added after `rc1` were exported from `axonium.errors` but not from the package
  itself, so `from axonium import IdempotencyInProgressError` failed. All of them are importable
  from the package now, and a test pins the invariant -- nothing failed in CI before, because every
  test imported from the submodule.
- `cache_n` present with a null value was reported as a measured zero rather than as unmeasured,
  making an unknown cache indistinguishable from a cold one.

### 1.0.0rc1

Ground-up rewrite targeting the Prometheus Gateway API. Not backward compatible with `v0.6.0`,
which spoke to a platform generation that no longer exists.

**Client**

- `Axonium` and `AsyncAxonium`, mirroring each other exactly. Every behavior is tested against
  both, so async paths cannot quietly diverge.
- Resources: `models.list()` / `models.mine()`, `chat.completions.create()` /
  `chat.completions.stream()`, `embeddings.create()`, `images.generate()`.
- Python 3.10+ (down from 3.13, which excluded most enterprise environments).

**Authentication**

- OAuth2 `client_credentials`, form-encoded, with refresh-ahead caching so a request never fails
  merely to discover its token expired. The reactive `401` path remains as a fallback.
- Token lifetime is anchored to the server's clock via the response `Date` header and the token's
  `exp` claim, which removes client/server clock skew from the calculation.
- Concurrent callers share a single refresh instead of each triggering one.

**Errors**

- A typed exception per entry in the gateway's error catalog, selected by the `type` suffix.
  Unrecognized suffixes fall back by status rather than raising, since the catalog will grow.
- The token endpoint's RFC 6749 errors are a separate branch of the hierarchy: `except APIError`
  cannot accidentally swallow an authentication failure.
- A `403` is diagnosed against the scopes the token actually holds, naming the missing scope.

**Resilience**

- Retries only where the platform reports that no generation happened. `502 upstream-error` is
  opt-in, since the request may have reached a model and this API has no idempotency mechanism.
- Client-side timeouts are never retried.
- A server-supplied `Retry-After` is honored but capped by `max_backoff`; a longer wait is
  surfaced to the caller rather than slept through inside one call.
- Cooldowns are recorded only from waits the platform supplied, never inferred locally.

**Streaming**

- Mid-stream failures are detected in-band, since the `200` and headers are already committed by
  the time a backend fails. Partial output is preserved on the raised error.
- Token counts are reconstructed from the final chunk's `timings` when a backend sends no `usage`
  chunk, and flagged `estimated` so a derived figure is never mistaken for a reported one.

**Observability**

- Correlation IDs and rate-limit budget on every response, successes included.
- Structured logging under the `axonium` logger with a `NullHandler`. Prompts, completions and
  credentials are never logged and there is no flag to enable it.
- OpenTelemetry spans behind the `axonium[otel]` extra, off by default. Trace context is not
  propagated outbound, because the gateway does not read it.

**Removed from the legacy SDK**

- Langfuse coupling and the `llm-guard` PII masking layer. Vendor observability belongs to the
  platform; the masking layer was also heavyweight, English-only, and provably broken.
- LangChain and LangGraph bridges, and the `MiniAgent` / `LLMRunnable` workflow abstractions —
  out of scope for an API client.
- Spanish-heuristic response normalizers, which were model-output-shape hacks.

## Go

### Unreleased

Nothing yet.

### 0.6.0 — 2026-10-05

Re-vendored at `2026-10-05a · PRM-187/188`, and the two things we asked for arrived in `2026-10-04b`.

**`logprobs` and `top_logprobs` on chat completions (`Logprobs` / `TopLogprobs`), with the answer at `Choice.Logprobs`.** How
confident the model was, so an agent can escalate to a person instead of acting on a guess — which is
what Apeiron asked the platform for.

**`logprob` is a natural logarithm**, and that is the whole reason `TokenLogprob.Probability()` exists: `-0.00054` is about
99.95% and `-7.6` is about 0.05%. Read as a probability it looks like a number near zero meaning
*unlikely*, and nothing about the mistake is loud. It is **absent rather than zero** when the backend
sent no `logprob`, because a token it said nothing about is a different fact from one it said was
impossible, and a caller thresholding on confidence has to tell them apart.

**`top_logprobs` without `logprobs` is refused here**, not after a round trip. The rule is the
engine's — llama.cpp answers *"top_logprobs requires logprobs to be set to true"* — and the gateway
enforces it before forwarding so the refusal arrives as problem+json. `logprobs: false` beside it is
refused too: an SDK checking only for *absence* would have sent that one, since the field is present
and wrong.

**`raw_scores` on `/v1/rerank` (`RerankRequest.RawScores`).** The field we declined to implement last time because it was
announced in a message and absent from §3.6 — the platform added it to the contract and said the
procedure was the right call, so it lands now. The logit instead of the probability: a reranker's
probabilities saturate near 1.0, and a saturated probability cannot be calibrated while the logit
behind it can. Safe to send unconditionally, because an engine without it answers normally and names
the field in `X-Prometheus-Ignored-Parameters`.

**And the scope question we raised came back as the platform's own defect.** Their new paragraph
listed four scopes and omitted `embeddings` and `rerank`, which have had their own buckets since
`PRM-129` — the paragraph below it, which they had not touched, was the correct one. They fixed it by
**removing the list** rather than correcting it, which is what this SDK did in the same release. Two
wrong copies of one truth, one in each team, and they caught each other; no test on either side could
have.

No corpus case covers any of this yet — `logprobs` needs a recording and `raw_scores` needs an engine
that has it — so the local tests are what hold it.

Re-vendored at `2026-10-04 · PRM-182/183/184`, which brings a new engine, a new error, and a trap.

**`503 rerank-dialect-unknown` → `ErrRerankDialectUnknown`.** A reranker running on an engine whose rerank request
shape the gateway has not recorded. **The one 5xx in the catalogue that is not retryable**, and that
is the whole reason it needed naming rather than falling through: measured here, an unmapped `503`
resolves to the status-keyed fallback, which **is** retryable — so until today this error would have
been retried through the whole attempt budget and reported as a timeout for a condition that was
never going to clear. Mutation-tested two ways in Rust: deleting the mapping fails the catalogue
parity check by name, and marking it retryable fails it with the number.

**`tei.predict.v1`, and the case `payload_schema` was waiting for.** A second engine now serves
`zero_shot`, and the two disagree. `hf-inference.zero-shot-classification.v1` answers scores
normalised across *the caller's* candidate labels; `tei.predict.v1` answers scores across the
**model's own** classes and has no notion of candidate labels at all. Both sum to 1, over different
things. Dispatching on `modality` reads one as the other, which is exactly the failure the decision
to dispatch on `payload_schema` was made to prevent — and it had no case to prove it until now. No
code changed: `payload_schema` is a pass-through string and no SDK enumerates its values.

**The batch trap, documented where somebody will read it.** On that engine a batch is *always* a
list of lists: a flat array of two strings is read as one pair and answers **once, in silence**, and
three or more is a `422`. So the obvious "send my N texts as an array" is the single form that
quietly returns one wrong answer. It is also the argument for leaving the predict result undecoded —
one input returns a flat list and a batch returns a list of lists, from the same model and the same
endpoint.

**Five SDKs kept five different hand-written lists of rate-limit scopes, and they had diverged.**
This one said `embeddings`, `rerank`, `chat_completions`, `default`. One of the five said `chat` where the header says `chat_completions`, which
is a name a caller would key a map by and never match. The guide now contradicts itself about the
set too — the new §6.3 addendum names four scopes as today's complete set while the paragraph below
it, unchanged, says `PRM-129` gave `embeddings` and `rerank` their own. Raised with the platform; in
the meantime all five stop enumerating and say the set is read from the header. The fixed 60-second
wall-clock window is documented in its place, because that one is a fact a caller has to design
against: pace on the remaining count, never on an assumed rate.

**`raw_scores` on `/v1/rerank` is deliberately absent.** It was announced to us as a new optional
field in the gateway's own contract, with measurements — but §3.6 of the guide revision that was
supposed to carry it still documents only `query`, `documents` and `top_n`. Implementing from a
message rather than from the contract is how a field ends up in five SDKs and in no allowlist check.
Raised; it lands when the contract has it.

Re-vendored at `2026-10-02 · PRM-167/173/174`, which adds **two error types** and takes one away.

- **`404 unknown-route`** → ``ErrUnknownRoute``
- **`405 method-not-allowed`** → ``ErrMethodNotAllowed``

`unknown-route` is deliberately **not** `not-found`, and the platform split them for these SDKs'
benefit: `not-found` is a statement about *data* — no usage row with that id belonging to this client
— which a caller may reasonably read as an empty result or retry. A bad URL is neither. All four SDKs
dispatch on the suffix, so one shared type would have made them do the wrong thing with one of the
two. `method-not-allowed` keeps Starlette's `Allow` header through the re-wrapping, so a `405` still
answers "then which verb".

**And it retires a claim this repository made about the gateway.** `spec/errors.json` carried, since
2026-09-27 and correctly then, that a `404` for an unserved route was *not* the problem+json envelope
— a bare `{"detail": "Not Found"}` with no `type` and no correlation ids in the body. `PRM-174`
fixed it; measured 2026-10-03 against the restarted stack. The note is corrected in place rather than
deleted, because **the fallback it forced stays and its reason has changed**: a body with no `type`
still arrives, but from a proxy returning HTML before the request ever reaches the gateway, which is
not the platform's to fix. One sentence of the corpus case that replays it said the same thing and is
corrected the same way.

**The corpus moved to v27, 49 cases**, and this SDK needed no change to pass it. The new case is a
second in-band stream failure whose payload is an **object** rather than the literal string
`stream interrupted` — added because mutating a runner to compare that exact text left all 48 cases
green. One fixture carried an in-band error and it carried the one message, so the corpus could not
tell *detect the key* from *compare the string*, which is precisely the distinction the platform asked
for and the one this SDK has always implemented. It passed on the first run; what changed is that it
is now **pinned rather than lucky**.

### 0.5.0 — 2026-10-02

**The modality guard was refusing nothing on `/v1/rerank`, and had not since rerank shipped.** The
accepted set at the call site was right; the separate hand-kept list of *known* modalities had never
heard of `rerank`, and the check returns early on a modality it does not recognise. So it could not
compare anything. Measured against a live deployment:

    qwen3-reranker  on chat    -> ALLOWED   (the doc comment promised 400)
    qwen3-embedding on rerank  -> ALLOWED   (same)
    qwen3-embedding on chat    -> refused   <- the only pairing that worked

The known set is now **derived** from the per-endpoint sets, so the two cannot drift again, and a
test refuses a literal at the call site — which is how a modality gets accepted by an endpoint
without ever becoming known.

#### Announced: the guard now refuses more

With the modality check enabled, these pairings used to reach the gateway and come back `400
modality-mismatch`. They are refused locally now, before the request:

    a rerank model on chat            a classification model on chat
    a chat model on /v1/rerank        anything with its own endpoint on /predict

Same failure, one round trip earlier, and as `ErrInvalidRequest` rather than an `*APIError`. Off by default, so a caller who never enabled it sees
no change.

#### And the reason the guard exists had expired

Every justification for it — the doc comment and the error message — said the gateway accepts chat on
an embedding model and answers `200` with degenerate billable output. All six wrong-modality
combinations measured live answer `400 modality-mismatch`: `RM-66` closed it, and **the guide
documenting `RM-66` was vendored in this repository the whole time**, read for its error-catalog rows
without the prose being re-read against the code.

The guard stays — it saves a request and a rate-limit unit, and the guide says a check of this kind
can stay — and no longer claims to save money. Nothing about its behaviour changed here, only what
it tells a caller.

#### New

- **`client.Predict.Create(ctx, model, body, PredictOptions{})`** — the pass-through route, `POST /v1/models/{model}/predict`, for `classification`,
  `zero_shot` and `typed_decision`. `PredictResult.Value` is a `json.RawMessage`, read through `Into(&dest)`, because `sst2-clf` answers a **top-level array** and a
  map would have failed on the first engine the platform put on this route.
- **`payload_schema` on the catalog entry** — the field that identifies which body a model takes.
  Dispatch on it rather than on the modality: `sst2-clf` and `von-decide` are both classifiers and
  want different bodies.

Four contract cases cover the route, recorded live, including the first recorded
`predict-backend-rejected` — the one catalog row whose status is the engine's rather than the
gateway's, with the engine's own error preserved under `backend_error`.

**The pass-through route: `client.Predict.Create(ctx, model, body, PredictOptions{})`.**

`POST /v1/models/{model}/predict` serves three modalities the OpenAI surface has no shape for ---
`classification`, `zero_shot` and `typed_decision`. The body goes to the engine verbatim and its
answer comes back verbatim, so `PredictResult.Value` is a `json.RawMessage`, read through `Into(&dest)`.

**Not defensive typing --- a measured constraint.** The three live engines answer:

    sst2-clf     [{"label":"POSITIVE","score":0.978}]        <- a top-level ARRAY
    von-decide   {"sequence":...,"labels":[...],"scores":[...]}
    laya-decide  {"model":...,"answers":{...},"routing":{...}}

A type that assumed an object would have failed on the first engine the platform shipped here, and
would have reported "this is not JSON" about valid JSON. There is no `classify(text)` either:
`sst2-clf` and `von-decide` are both classifiers and want different bodies, so a typed method would
promise a stability the route does not have.

`payload_schema` is now modelled on ``Model``. The spec names it as the field that identifies
the body shape, it is populated on all ten live models, and without it the route ships with no way
for a caller to know what to send.

Four contract cases, recorded live, replayed by all four SDKs --- including the first recorded
`predict-backend-rejected`, the one catalog row whose status is the engine's rather than the
gateway's, with the engine's own `backend_error` preserved.

**The modality guard refused nothing on `/v1/rerank`, and had not since rerank shipped.**

The endpoint passed its accepted set correctly. The separate, hand-kept list of *known* modalities
had never heard of `rerank`, and the check returns early on a modality it does not recognise --- so
the call could not compare anything. Measured:

    qwen3-reranker  on chat    -> ALLOWED   (the doc comment promised 400)
    qwen3-embedding on rerank  -> ALLOWED   (same)
    qwen3-embedding on chat    -> refused   <- the only pairing that worked

One truth in two places, and only one of them was updated. The known set is now **derived** from the
per-endpoint sets, so there is one. Adding the three predict modalities would have been dead on
arrival for the same reason, which is why this came first.

The test standing guard over exactly this had been given the wrong answer key: it asserted
`an equality against a hand-written map`, a literal typed into the test rather than the invariant its name claimed. A literal cannot
notice a new call site, so it passed throughout. It now reads the call sites out of the source, and
fails if an endpoint calls in without a row, or a row exists for no endpoint.

**The reason the guard exists had expired, and nothing could have told us but a measurement.**

Every justification --- module prose, the error message, a test name, a test's `match` --- said the
gateway accepts chat on an embedding model and answers `200` with degenerate billable output. All six
wrong-modality combinations measured against a live deployment answer `400 modality-mismatch`.
`RM-66` closed it, **the guide documenting `RM-66` is vendored in this repo**, and we read it for
error-catalog rows without re-reading the prose against the code.

The guard stays: it saves a request and a rate-limit unit, and the spec says a client-side check of
this kind can stay. It no longer claims to save money. Mutation testing could never have found this
--- mutating the code turns the tests red correctly, because the code was never wrong. Only
comparing a claim against a live deployment finds a premise that died.
Re-vendored at `2026-10-01 · PRM-164/167/173`. §2.7 is rewritten and renamed, from "Client types
--- who may hold a credential" to "Credentials --- whose they are, and who issues them".

**The axis moved.** The old section's rule was *confidential clients only*, with a distributed app
refused because it cannot keep a secret. The new rule is that **a credential identifies whoever pays
for consumption**, and the device stops being the question:

- An *integrator's* credential must never ship inside a distributed application --- a copy on every
  user's device is a copy of the identity that is granted models and billed.
- An *end client's own* credential may live on that client's own devices, phone and laptop alike.
  The principal, the grants and the bill are theirs, so a leak costs them their own account.

An app with a pasted secret is still a public client in RFC 8252's terms; it is accepted here when
the secret and the bill belong to the same person. That is the distinction the old §2.7 did not
separate, and it is the one that answers `A-34`.

Also now stated as a rule rather than an absence: **issuance is always a human administrator**.
There is no registration endpoint and no API an integrator can call to mint credentials for its
users, because issuing one opens a billing account. An application therefore has to treat **"no
credential yet"** as a first-class state rather than an error, and the request goes to the platform
rather than to the integrator. One credential per client, used on as many of that client's own
devices as they have --- per-device credentials are not issued, so an app assuming one install per
credential is wrong for any user with a phone and a laptop.

**Nothing in these three SDKs changes.** They take a token, or a callback that returns one, and
nothing about who obtained it is theirs to know. The section matters for the Swift SDK, whose
documented example reads a secret from the Keychain --- which the new §2.7 permits when that
credential is the end client's own, and still refuses when it is the integrator's.

`PRM-170` is superseded in substance rather than relaxed or tightened.

Re-vendored at `2026-09-29 · PRM-164/167/170`, which adds §2.7, "Client types — who may hold a
credential".

It answers `A-30` and reframes it: the question was never about mTLS. A client certificate shipped
inside an app somebody downloads is a secret shipped inside an app somebody downloads. What is
actually underneath is that **`client_id` is the billing principal** — grants and invoices are keyed
to it — so a `client_secret` on an end user's device is the *integrator's* identity copied onto
every one of their users' machines. Not a shape to harden; a shape not to have.

PKCE is not the alternative either, and not on cost: per-end-user identity has nowhere to live in
the platform's authorization or billing model, and the auth-service has no authorization endpoint
at all.

The answer that unblocks a distributed app is an integrator-controlled backend holding the
credential, with the app authenticating against that. Nothing here changes: these three SDKs run on
servers, which is the supported shape. It matters for the Swift SDK, whose documented example is an
on-device secret out of the Keychain.

**The modality preflight said "not in the catalog" about a catalog it can no longer see all of.**

With `verify_modality` on, a model missing from the catalog was refused locally as
`unknown-model`, on the stated grounds that the gateway reports the same thing and refusing only
saves the round trip. That was true while the catalog was the platform's full public list. Since
`PRM-167` it holds only the models the token has a grant for, which gives absence two causes this
SDK cannot distinguish:

    not registered at all    -> the gateway answers 400 unknown-model
    registered, not granted  -> the gateway answers 403 forbidden

Refusing locally told a caller to check the spelling of a name that was spelled correctly, and
**pre-empted the `403` whose entire job is to name the missing scope** — the error these SDKs work
hardest to make useful. So the request now goes, and the gateway answers a question only it can
answer. A typo costs one round trip; a missing grant gets diagnosed. That is the right way round.

What the preflight still does is the thing it was built for and can still prove: a model the
catalog **does** show carries its modality, so a mismatch is a fact rather than an inference.

*Corrected further down in these same notes: this paragraph claimed the gateway would not catch that
mismatch, which stopped being true at `RM-66` and was measured false on 2026-10-02.*

Three tests pinned the old behaviour and were correct when written. The Go one now asserts the
boundary with a request count rather than a server-side rejection, because only a number can tell
"stopped locally" from "reached the gateway".

**Not measured against a live deployment.** The only gateway available grants every model it has,
so the registered-but-ungranted case could not be produced. The reasoning stands on the guide's own
statement that `unknown-model` is checked before any scope check; it is not a measurement, and is
labelled as such rather than presented as one.

Re-vendored at guide revision `2026-09-29`, and the catalog means something narrower than it did.

`PRM-167` closed `GET /v1/models` to anonymous callers — which AXO-117 already fixed, by measuring
rather than by reading. What the guide adds is the half that was not visible from a `401`: the
endpoint now returns **only the models the token holds `model:<id>` scope for**, which makes
`models.mine()` an alias of `models.list()`. Same requirement, same filtering, same response.

So three claims in these SDKs were wrong in a way no test could catch: "everything the deployment
serves", "the full catalog", "the public catalog is not the answer to what can I call". The answer
is the same from both endpoints now.

And the fact this session got wrong by inference before the guide stated it: **an empty list means
the token holds no grants, not that the platform has no models.** Two different facts that only an
operator can distinguish. Measuring an empty `/v1/models` and concluding the registry was empty is
exactly the mistake, and it was made here today.

The guide also fixed the header exclusion list this SDK reported in `A-29`: `X-Request-ID` and
`X-Trace-ID` are present on endpoints the old list excluded, and only the rate-limit headers are
actually absent.

The catalog call carrying a credential is pinned by the corpus, not just fixed in the code.

`GET /v1/models` stopped being public on 2026-09-29 and the fix landed in four SDKs — and in zero
contract cases. That is the shape this corpus exists to prevent, for the fourth time: a behaviour
corrected in N languages and held in none, so the fifth SDK inherits nothing.

Manifest v25 asserts it on `catalog-list`, through a third form of header assertion:
`request_headers_present`, by name and with no value. The value could not be pinned — an
`Authorization` bearer is each runner's own test token, so asserting it would assert about the
harness rather than about the SDK. It joins `request_headers`, which compares a value, and
`request_headers_absent`, which forbids one.

Mutation-tested in both directions of the released break: restoring `authenticate=False` on the sync
`list()` fails `catalog-list` sync, and on the async one fails async. That flag is exactly what
shipped in `1.0.0rc5`, where `models.list()` returned `MissingCredentialsError` against the closed
endpoint — the corpus now refuses it.

**`GET /v1/models` stopped being public, and this SDK was the one that believed the documentation.**

The platform closed the endpoint without announcing it. Measured against a live deployment:

    GET /v1/models  with no token  ->  401 missing-credentials

Python passed `authenticate=False` there, because the guide said it was the one public endpoint and
this SDK did what the guide said. `client.models.list()` — the simplest call it has — returned
`MissingCredentialsError` from a released version. Go, Rust and Swift survived by accident: their
comments made the same claim while their code sent the token anyway.

Fixed, and the false comments in the other three corrected with it, along with three lines of
`docs/02-calls.md`.

**Three tests were pinning the broken behaviour**, one of them a security test, and all three were
right when they were written:

    test_the_catalog_call_sends_no_credential_at_all
    test_listing_the_public_catalog_sends_no_token [sync] [async]

They asserted that listing the catalog costs no credential, which was the correct property while
the endpoint was public — spending a token where none is wanted is a real thing to guard against.
They went on passing while the call returned 401 in the field. The security test now guards what
never depended on the endpoint being public: the client secret goes to the token request and
nowhere else, and a bearer token on the wire is the design rather than the bug.

The vendored guide still says otherwise at its line 647. That is a question for the platform team,
not something to paper over here.

The gap the corpus declared is closed by a measurement, not by a repair.

`stream-idempotent-replay` is re-recorded from a complete capture: two streamed calls with the same
`Idempotency-Key`, the second one's bytes and **all** of its headers. So
`meta.idempotent_replay_of` and `meta.request_id` are now asserted, because they were finally
measured on a stream rather than assumed from a guide sentence that happened to read true. A replay
carries its own `request_id`, distinct from the billed one, and the billed one is what
`X-Idempotent-Replay-Of` names — confirmed against `/v1/usage` in the same session, where the
original id returns `200` and the replay's own returns `404`. There is no streaming/non-streaming
asymmetry, which was the open question.

Every expectation was derived by running this SDK's own accumulator over the new bytes rather than
carried over from the old case, and the two bodies were compared before recording.

**The complete headers say two things a partial capture hid.** A replay names **no instance** —
neither `X-Prometheus-Instance` nor `X-Prometheus-Instance-Id`, where the original carried both,
which is correct because no replica served it. And, undocumented anywhere in the guide, **a replay
consumes request budget**: `remaining-requests` goes 59 → 58 across the two calls. Not generated, not
billed, and still counted against the RPM window. The case asserts `58`, so the number itself is the
evidence, and the question is with the platform team.

The tripwire that stood guard over the gap is deleted, which is what it was written for. The general
check it backstopped stays: no `meta.*` assertion may outrun the headers its case recorded.

A declared gap in the corpus is now an enforced one.

`stream-idempotent-replay` says `X-Idempotent-Replay-Of` and `X-Request-ID` were never captured for
a streamed replay, so asserting `meta.idempotent_replay_of` there would fail against an SDK doing
exactly the right thing. The obvious repair is to add the header to the case — which turns a thin
recording into an invented one. Nothing held the sentence that said not to.

Two checks now do, because one cannot. The first refuses any `meta.*` assertion whose sourcing
header is absent from that case's recorded bytes, so the accidental path fails saying *capture it
before asserting it* instead of looking like an SDK bug. That check cannot catch the deliberate path:
from inside a repository a recorded header and a typed one are the same bytes in the same file. So
the second is a tripwire on this one declared gap, written to be **removed** rather than satisfied —
whoever measures those headers for real deletes it in the same commit, which is a deliberate act with
a diff that says so.

**The question itself stays open, and it is the platform's.** The guide documents both headers on a
replay and says streamed replays work, so the expectation *reads* true; what is missing is anyone
having measured it on a stream. Attempting the capture locally established only that it cannot be
done here: the deployment answers `/health` with `{"status":"ok"}` while `GET /v1/models` returns an
**empty catalog**, so there is no model to generate against — which is incidentally a live instance of
the `/health` concern already open with the platform team.

A stream case can assert a field.

Nine of them could not. The streaming branch of all three runners read the stream-shaped keys --
`content`, `chunks`, `usage`, `tool_calls` -- and ignored `fields` in silence, so nothing about a
stream's `meta` was expressible: not the correlation ids, not the rate-limit budget, and not the two
idempotent-replay flags that `chat-idempotent-replay` has pinned since the day it was recorded. A
streamed replay that lost its entire `meta` passed all 44 cases.

Manifest v23 adds `meta.idempotent_replay` to `stream-idempotent-replay`, and the three runners
resolve `fields` on a streamed case against `meta` and nothing else -- `content`, `chunks`, `usage`
and `tool_calls` each already have a key of their own, and a second way to say the same thing is how
two ways eventually disagree. A test holds that restriction rather than a comment.

**What is still missing there is a recording, not an assertion.** This case captured only
`Idempotent-Replay`, where its non-streaming twin captured `X-Request-ID` and
`X-Idempotent-Replay-Of` too, so `meta.idempotent_replay_of` and `meta.request_id` are deliberately
*not* asserted: the headers are absent from these bytes, an SDK reporting them empty is correct, and
an expectation for them would be invented. The gap is declared in the case rather than left to be
discovered, because `idempotent_replay_of` is the only id that carries a usage row -- the replay's
own id does not.

Mutation-tested in all three: a stream that loses its `meta` now fails, where it used to pass.

The corpus now checks what the SDK **sent**, not only what it received.
Six cases supplied an `Idempotency-Key` and not one asked whether it was sent. The only assertions
about what went **out** were on the token endpoint, so an SDK that accepted a key and dropped it
passed all six: the four error cases replay a recorded envelope the mock serves regardless, and the
two replay cases assert the bytes that come **back**. A key that never leaves the process turns the
retry it exists to protect into a second billable generation — the premise the whole streamed-retry
decision rests on.

Manifest v22 adds `expect.request_headers` and `expect.request_headers_absent`, and the three
runners honour them. `chat-idempotent-replay` and `stream-idempotent-replay` now require the key on
the wire; `chat-completion-basic` requires that neither an idempotency key nor an instance pin is
**invented**, because a key the caller never asked for makes a retry silently replay a stale result,
and an invented pin opts them out of load balancing and failover without saying so.

Mutation-tested in all three, both directions: dropping the key on a stream fails the replay case,
and inventing one fails the basic case.

**And it found a comment that was false in two of the three.** Python and Go both stated that the
gateway ignores an idempotency key on a streamed request and that the SDK therefore *refuses to send
one* — while the code beside them sent it, and while a contract case recorded from a live deployment
showed the platform replaying a stream 6 times out of 6. Nothing asked what went out, so the false
comment and the true code sat in one file for as long as nobody read both. The boundary that is real
is narrower and was always documented correctly elsewhere: a key replays a stream the gateway
**finished** and whose delivery dropped, never one the model itself broke.

A streamed request rejected **before the stream begins** is now pinned as *recognised* -- the
precondition the retry above assumes, and the one thing nothing asserted.

No behaviour changed here: the status check that precedes SSE parsing has always been in place. What
changed is that removing it now fails. Measured by the Swift SDK team: reading the body as a stream
without first looking at whether the status was `4xx`/`5xx` still passed **all 40 cases of manifest
v19**. An SDK without that check turns every rejection into a silently empty response -- no error,
no content, and nothing for a caller to correlate -- and the corpus said that was fine. v20 pinned
what a recognised rejection leads to and never that it is recognised, which is the shape of AXO-110
in a different place, and of AXO-108 before it: fixed in three languages, pinned in none.

`expect.kind` for a stream that fails before it begins is `error`, **not** `stream_error`, and the
two are different contracts. `stream_error` is the in-band failure of a stream that has already
begun, after the `200`/`text/event-stream` headers are committed, and it is never retried. This one
arrives *instead of* a stream, as an ordinary status, and is -- which is what the guide (3.3) means
by a client that sets `stream: true` not getting a different error contract for doing so.

The runner could not have expressed this before. `invokeExpectingError` dispatches by operation and
had no branch for `chat.completions.stream`, so a streaming error case would have died on
"unsupported operation". It now opens the stream, iterates it and returns `Err()`, because an SDK that
hands back a stream where a status belongs must not pass by reading the refusal as an empty body.

Manifest v21 adds two cases, both on `chat.completions.stream`.
`stream-rejected-before-it-begins-is-an-error` replays the `400` that `PRM-143` recorded live on
2026-09-27: the engine's own status and OpenAI-shaped body, passed through verbatim rather than
wrapped in a problem+json envelope, so `error_type_suffix` is null and the correlation ids exist
only in the headers -- which keeps AXO-108's header fallback pinned on the streaming path too.
`stream-rejected-before-it-begins-is-retryable-when-the-backend-is-unavailable` is the
`503 backend-unavailable` that justifies the retry v20 added. It carries no `Retry-After` in header
or body, unlike every other retryable error in the corpus, because the guide (5.2) confirms this
variant supplies no backoff signal at all; a shortened window would have been cheaper to test and
would have pinned a value the gateway never sends.

Mutation-tested in all three languages: removing the status check fails both new cases, and takes
`stream-retried-when-rejected-before-it-begins` with them.

The Swift SDK covers this by hand today, in `StreamRejectionTests.swift`. With the corpus holding
it, those tests are redundant and that team removes them.

A streamed request rejected **before the stream begins** is retried, and that is now deliberate
rather than incidental.

No behaviour changed here: `send` already ran the retry loop for a streamed request, because nobody
had excluded it. What changed is that `streaming` not excluding a request from that loop is now
stated where a future reader will look, since the opposite is the reading that looks safer. `Stream`
and `ChatCompletionStream` document the two failures separately instead of claiming a stream is never
retried.

The three had never agreed, and none of the three disagreements was a decision. Measured on
2026-09-27, counting requests that reached the server for a `429` on a streamed
`POST /v1/chat/completions`: **Python 1, Go 3, Rust 3** -- and all three documented never retrying a
stream at all. Python's stream opened its connection by another route and missed the shared retry
loop; Go and Rust ran it because nobody had excluded streaming from it.

The two that contradicted their own documentation were right. A stream can only fail this way
*before* any body byte exists -- the gateway reads the engine's status before the
`200`/`text/event-stream` headers are sent -- so nothing was generated and nothing was billed, and
reopening is a first generation rather than a second. It is also the only retry available: the
gateway performs **no** internal retries on a streamed request, so a `503 backend-unavailable`
arrives there after one attempt rather than three.

It could not be said a week earlier. Until `PRM-143`, landed 2026-09-27, a stream rejected before it
began arrived as a `200` whose body was nothing but `data: [DONE]` -- indistinguishable from a
legitimately empty answer, so "retry the rejections that precede the 200" named nothing. The gateway
now returns the engine's real status, and a connection that never opened returns
`503 backend-unavailable` in the problem+json envelope.

What has no exception, in all four SDKs: **a stream that has already begun is never retried.** There
the failure arrives in band, part of the answer was delivered, and part was billed.

Manifest v20 pins both halves, because three hand-written suites had pinned neither. The corpus
gained the shape needed to express it: a case can now serve an ordered *sequence* of responses, and
assert how many requests reached the server. `stream-retried-when-rejected-before-it-begins` serves a
`429` then the stream and requires two; `stream-not-retried-once-it-has-begun` queues a healthy
stream behind an interrupted one and requires that it is never reached -- an SDK that retried there
would pass every assertion of `stream-interrupted` while billing twice and returning the wrong
answer. Both were mutation-tested in all three languages: breaking the retry fails the first,
and the count is what catches it.

Agreed with the Mundus team so the Swift SDK is born with the behaviour rather than inheriting
whichever of the three it happened to read.

A contract case now pins reading the correlation ids out of the headers.

The behaviour shipped in all three SDKs on 2026-09-27 with one hand-written test per language and
nothing added to the shared corpus, so it was fixed three times and pinned zero. Measured by
building a fourth SDK against the corpus alone: removing its header fallback and replaying all
fourteen error cases passed every one of them. Manifest v19 adds
`error-correlation-ids-only-in-the-headers` -- a real 422 body with neither id, both ids in the
headers -- and removing the fallback from each of the three now fails it.

It is also the first case expecting **no** `type` at all, which two of the three runners could
not express: Rust's panicked on the null, and needed a name for the kind an absent type produces.

Two error types the platform added on 2026-09-27 are mapped: `503 capacity-exhausted` and
`predict-backend-rejected`.

`capacity-exhausted` means every replica of a model is busy rather than broken, which is the one
`503` where waiting is the whole remedy. It ships disabled on the platform, so nobody has met one
yet; it is mapped before anyone does.

`predict-backend-rejected` is the first catalogued error with no fixed status. The `predict` route
passes the body to the engine, so a refusal keeps the engine's status and its body, and the name
claims no cause. Retryability is therefore read from the status rather than from the name, and the
engine's own body is reachable through `backend_error`.

**The check that holds the catalog to the guide could not see the second one.** It required a
three-digit status and the guide's row says `4xx`, so the row never parsed and the set comparison
found nothing missing — a guard built to catch a row that disappears, blind to a row that never
arrived. It now validates the status cell instead of selecting on it, and an unreadable one is a
failure rather than a skip.

An error whose body is not a complete problem+json now still carries its correlation ids.

They were read from the body only. A validation failure forwarded verbatim from a backend has
neither id in its body and both in its headers, so the caller was handed an error with nothing to
take to the platform team. The body still wins where it has them, so nothing changes on an envelope
that honours the contract.

`ErrUnknownParameter` maps `400 unknown-parameter`.

Raised only when a request carries `require_parameters: true`, which asks the gateway to refuse an
unaccepted field instead of dropping it. It was documented in the platform guide we had already
vendored, and nothing here noticed: the parity guard holds the catalog to the SDKs, and nothing
held the guide to the catalog. That direction is now checked too.

**Structured output works.** `ResponseFormat` is now a declared field, forwarded verbatim.

The platform started honouring it on 2026-09-18 and told us. This SDK went on warning that it was
unsupported, and shipped a release five days later still saying so. In Python the field was also
*stripped*, so structured output was not reachable at all.

The answer comes back as a JSON **string** in the message content — parse it yourself
(`json.Unmarshal([]byte(completion.Content()), &v)`). It is not parsed here for the same reason tool-call `arguments` is not: a generation
stopped by `max_tokens` leaves it truncated, and a response object that raises from the inside is
worse than one that hands you what arrived.

Documentation only, and it matters because it had become wrong.

The rate-limit envelope no longer omits `trace_id`, and `X-RateLimit-Scope` now reaches the `429`
as well (platform guide `2026-09-19b`). Four statements in this SDK still described the old
behaviour. They now describe the current one, and say which deployments still behave the old way.

The body fallback for `scope` stays. It is no longer needed against a current deployment and is
kept for one predating the fix — which the platform team explicitly recommended.

Also documented: what happens to an `Idempotency-Key` whose request *failed* is undefined, is the
gateway's decision rather than this SDK's, and is being asked.

### 0.4.0 — 2026-09-20

`Version` reported `0.2.0` from the module published as `v0.3.0`.

It feeds the `User-Agent`, so every request this SDK made identified itself as `axonium-go/0.2.0`
to the platform — a field used to correlate client versions during an incident. Nothing failed; the
answer was simply wrong.

Python and Rust check the tag against the packaged version inside the job that publishes, and
refuse to publish on a mismatch. Go has no such job: pushing the tag *is* the release. So the check
now runs on every push instead, and refuses a constant that has fallen behind the newest `go/v*`
tag — which is the state that produced this.

`RateLimitSnapshot.Scope`, and `Client.RateLimits()` keyed by it.

The platform gave `/v1/embeddings`, `/v1/rerank` and `/v1/chat/completions` separate rate-limit
budgets. That made `Client.LastRateLimit()` a number from whichever endpoint answered last, with
nothing in the numbers saying so — a dashboard drawing "requests remaining" kept drawing a
plausible figure from another bucket. Ask about a particular budget by scope instead.

Measured against a deployment rather than taken from the announcement: `X-RateLimit-Scope` is on
successful responses and **absent on the 429**, where the body carries `"scope"` instead. So the
one response whose budget most needs attributing — the one telling you a bucket is exhausted — is
read from the body when the header is missing. The header wins when both are present.

A retried call can now explain its own duration without anyone reading a log.

A `Retry-After` of 0–60s, respected as it should be, looks from outside like one slow call among
fast ones. Three separate teams have reported that as a hang. `v0.3.0` began logging the wait at
INFO, which was necessary and not sufficient: this SDK does not configure the host application's
logging, so the line is invisible until somebody opts in — at every entry point, and again at the
next one added. A latency metric cannot read a log line at all, and a latency metric is where this
keeps being seen.

`Meta.WaitedFor` and `Meta.Attempts` on every response now carry it as data.

Subtract it from a wall-clock reading to get what the platform actually spent: the wait is
deliberately excluded from every duration this SDK reports, because sleeping is not service time.

Not covered: a call that waited and then failed anyway, which is the one whose duration most needs
explaining. Errors carry no response metadata today.

### 0.3.0 — 2026-09-16

Five error types the platform ships and nobody had mapped: `inconsistent-model-group`,
`unauthorized`, `invalid-date`, `invalid-range` and `range-too-large`.

They surfaced from the other side of a gap we reported. We told the platform that five suffixes
they send appear nowhere in their guide; they wrote a test comparing every `type` the gateway
raises against that table, and it found ten. Five were ours, and these five had been there longer
and nobody had noticed.

Three of them belong to the admin-scoped usage export, which this SDK does not call. They are
mapped anyway so the taxonomy is complete and an unknown suffix means what it says.

`POST /v1/rerank`, for models with `rerank` modality.

A reranker is a cross-encoder: it scores a query against each document and returns them ordered. It
generates nothing, so there are no completion tokens and billing is prompt-only.

Two things matter if you were doing this through the chat endpoint. The **whole document set is one
request**, not one per document — against a 60 RPM budget, scoring 50 candidates costs 1 unit rather
than 50. And each result's `index` points into the `documents` you sent, never into the results, so
a reordered result stays attributable to its input.

- `client.Rerank.Create(ctx, RerankRequest)`, returning `*RerankResponse`, whose `Ranking()` gives the input indices best-first. An empty `documents` list is refused before the wire, which the gateway would answer
  `400 validation-error`.

The SDK now says when it is waiting, and for how long.

The platform's `Retry-After` on a `429` is seconds until the window resets, so it runs 0–60. An SDK
that respects it — as it should — looks from outside like one slow call among fast ones, and that
arrives as a latency bug report. A wait long enough for a person to notice is now reported at INFO
with `delay_s`; sub-second backoff stays at DEBUG, because the noise worry is frequent small retries
rather than the rare long one.

The wait is reported as a wait, never folded into `duration_ms` — that is measured per attempt and
deliberately excludes time spent sleeping. Time waiting is not time the gateway took.

A token response in neither documented envelope is now a transport failure rather than an OAuth2
one.

A `4xx` carrying `error` is an OAuth2 outcome; a `5xx` carrying `type` is the gateway's own
problem+json. A body with neither — an HTML error page from a proxy or load balancer that answered
instead of the gateway — used to be reported as an OAuth2 failure, which tells a caller their
credentials are the problem. That is both wrong and the most expensive wrong answer available here:
the obvious next step is rotating a perfectly good secret.

A failed token request is now typed by its envelope rather than read as OAuth2 unconditionally.

A `4xx` is an OAuth2 outcome in the RFC 6749 shape — wrong credentials, a scope the client does not
hold — and is never worth retrying. A `5xx` is the gateway failing to reach the auth-service,
arrives as problem+json, and `upstream-unavailable` **is** worth retrying. Reading both as OAuth2
left the 5xx with no type and no retryability, so a momentary blip looked exactly like bad
credentials and the request was abandoned rather than retried.

`not-configured` shares that status and is deliberately *not* retryable, which is why the suffix
drives the decision rather than the status.

**One address, not two.** The gateway serves both the inference API and `/oauth2/token`, so the
auth-service address is **gone from this SDK** rather than defaulted.

Keeping it as a field that defaults to the gateway would still have taught every consumer that a
second address exists. It does not, for them: the platform's auth-service is now reachable only
from inside the deployment, which is what it was always for. Removing the field removes the failure
it enabled — pointing the SDK at a self-hosted deployment used to mean changing two addresses, and
forgetting the second left the client asking the **official platform** for a token to use somewhere
else, with nothing erroring.

**Breaking**, and deliberately so while the surface is pre-1.0: `Config.AuthBaseURL` and `DefaultAuthBaseURL` are **removed**, along with `AXONIUM_AUTH_BASE_URL`. A deployment whose gateway
has no token endpoint wired up answers `not-configured`, which says exactly that rather than
failing obscurely.

Per-request usage lookup: what one of your own requests was charged, and why it stopped.

Both aggregate usage endpoints require `admin:read`, which a normal client neither has nor should
have — so `termination_reason` existed for callers who could not read it. The platform shipped this
after we made that case; this is the client half.

A replay has its own request id and no row of its own, so looking that id up is a `not-found` —
correctly, since a replay is not billed. `meta.idempotent_replay_of` names the generation that was
charged; look *that* up. The round trip is verified live in all three languages.

`termination_reason` is a plain string, not an enum. The platform proposed a fourth value this week
and withdrew it; the next one may not be withdrawn, and a closed set would turn a new value into a
parse failure for a caller who only wanted the token counts.

- `client.Usage.Retrieve(ctx, requestID)`, returning `*RequestUsage`. `ErrNotFound` is new.

`ResponseMeta.IdempotentReplayOf` carries, on a replay, the request id of the generation that was
actually billed.

A replay has its own request id and no usage row of its own, so looking that id up returns `404` —
correctly, since replaying reaches no model and is not billed. This header names the id that does
resolve, which makes it the only path from the response a caller received to the charge it
corresponds to. `None`/empty on anything that is not a replay.

The quickstart in the README used a model name that is not registered, so copying it produced
`400 unknown-model` rather than a completion. Examples and doc comments now use a real slug, and
each README says what a slug is: it never changes and is never reused, so pinning one is safe, but
which ones exist depends on the deployment and on what the token is granted — the catalog endpoint
is the source of truth, not the README.

Tool calls are now typed like everything around them.

They used to arrive as raw dicts while the message carrying them was a model. The asymmetry cost a
consumer real work twice over: reaching in by hand to read a name, and converting back to dicts to
feed a call into the next request. Both directions are now the same type.

`arguments` deliberately stays the model's own JSON **string** rather than a decoded object.
Decoding it at parse time would raise from inside a response model, for a caller who only wanted to
see what the model had managed to say — a generation stopped by `max_tokens` leaves a string that
was never going to parse. Decoding is a separate, explicit call that fails loudly, and the raw
string stays reachable either way.

**Breaking**, and deliberately so while the surface is still pre-1.0: anything indexing a tool call
as a dict/map/`Value` needs the field instead.

- `ToolCall` and `FunctionCall`, returned by `(*ChatCompletion).ToolCalls()` and
  `(*ChatCompletionStream).ToolCalls()`, and accepted by `Message.ToolCalls`.
- `call.ParseArguments()` decodes the arguments, returning an error wrapping the new
  `ErrToolCallArguments` sentinel.
- `(*ChatCompletionChunk).ToolCallFragments()` now reads from the chunk's `Raw` rather than the
  decoded delta. `Message` is the same struct for a message and a delta, so decoding a fragment
  into the typed shape would have silently dropped `index` — the one field the reassembler
  correlates on.

Streamed tool calls are now reassembled for you.

A tool call arrives split across as many deltas as it takes — `{`, `"`, `city` — and the fragments
are individually invalid JSON. Only the first carries the identity, and **`index` is the
correlation key**, because `id` never repeats. Every consumer was writing that join by hand.

The assembled call is **byte-for-byte the shape a non-streaming completion returns**, `arguments`
included: still a JSON *string*, not a decoded object. That is deliberate — the same caller code
handles both, and a stream cut short by `max_tokens` hands back the fragment that did arrive
instead of raising or dropping the call. Check `finish_reason` before decoding.

A second contract case was recorded live for this: a single-call recording cannot tell `index`
correlation apart from any other strategy, so a stream with two concurrent calls was recorded to
give the case teeth. The manifest is now 25 cases, and all three SDKs replay both.

- `(*ChatCompletionStream).ToolCalls()`, alongside `Content()` and `Usage()`.
- `(*ChatCompletionChunk).ToolCallFragments()` exposes the raw fragments for a caller who wants to
  watch them arrive. They remain unusable on their own.

### 0.2.0

- **Credentials are the only required setting.** `AuthBaseURL` and `GatewayBaseURL` default to the
  official Prometheus platform, with the same precedence as everywhere else. **The defaults are
  provisional** until the platform moves to its cloud host; a pinned version will keep the old
  address after it moves.
- Contract corpus re-recorded after the platform fixed a defect that left streaming generations
  unbilled. Streams now carry one terminal frame rather than two.

### 0.1.0

First release. Streaming with cancellation that reaches Prometheus — measured by counting the
chunks the upstream produced after the client went away, not asserted. Both credential modes,
idempotency keys, instance pinning, the full error taxonomy from `spec/errors.json`, structured
logging through `log/slog`, and a tracing hook that is an interface rather than an OpenTelemetry
dependency. All 24 shared contract cases replay the same recorded wire bytes as the Python SDK.

No third-party dependencies: standard library only.

## Rust

### Unreleased

Nothing yet.

### 0.6.0 — 2026-10-05

Re-vendored at `2026-10-05a · PRM-187/188`, and the two things we asked for arrived in `2026-10-04b`.

**`logprobs` and `top_logprobs` on chat completions (`logprobs` / `top_logprobs`), with the answer at `Choice::logprobs`.** How
confident the model was, so an agent can escalate to a person instead of acting on a guess — which is
what Apeiron asked the platform for.

**`logprob` is a natural logarithm**, and that is the whole reason `TokenLogprob::probability` exists: `-0.00054` is about
99.95% and `-7.6` is about 0.05%. Read as a probability it looks like a number near zero meaning
*unlikely*, and nothing about the mistake is loud. It is **absent rather than zero** when the backend
sent no `logprob`, because a token it said nothing about is a different fact from one it said was
impossible, and a caller thresholding on confidence has to tell them apart.

**`top_logprobs` without `logprobs` is refused here**, not after a round trip. The rule is the
engine's — llama.cpp answers *"top_logprobs requires logprobs to be set to true"* — and the gateway
enforces it before forwarding so the refusal arrives as problem+json. `logprobs: false` beside it is
refused too: an SDK checking only for *absence* would have sent that one, since the field is present
and wrong.

**`raw_scores` on `/v1/rerank` (`RerankRequest::raw_scores`).** The field we declined to implement last time because it was
announced in a message and absent from §3.6 — the platform added it to the contract and said the
procedure was the right call, so it lands now. The logit instead of the probability: a reranker's
probabilities saturate near 1.0, and a saturated probability cannot be calibrated while the logit
behind it can. Safe to send unconditionally, because an engine without it answers normally and names
the field in `X-Prometheus-Ignored-Parameters`.

**And the scope question we raised came back as the platform's own defect.** Their new paragraph
listed four scopes and omitted `embeddings` and `rerank`, which have had their own buckets since
`PRM-129` — the paragraph below it, which they had not touched, was the correct one. They fixed it by
**removing the list** rather than correcting it, which is what this SDK did in the same release. Two
wrong copies of one truth, one in each team, and they caught each other; no test on either side could
have.

No corpus case covers any of this yet — `logprobs` needs a recording and `raw_scores` needs an engine
that has it — so the local tests are what hold it.

Re-vendored at `2026-10-04 · PRM-182/183/184`, which brings a new engine, a new error, and a trap.

**`503 rerank-dialect-unknown` → `ErrorKind::RerankDialectUnknown`.** A reranker running on an engine whose rerank request
shape the gateway has not recorded. **The one 5xx in the catalogue that is not retryable**, and that
is the whole reason it needed naming rather than falling through: measured here, an unmapped `503`
resolves to the status-keyed fallback, which **is** retryable — so until today this error would have
been retried through the whole attempt budget and reported as a timeout for a condition that was
never going to clear. Mutation-tested two ways in Rust: deleting the mapping fails the catalogue
parity check by name, and marking it retryable fails it with the number.

**`tei.predict.v1`, and the case `payload_schema` was waiting for.** A second engine now serves
`zero_shot`, and the two disagree. `hf-inference.zero-shot-classification.v1` answers scores
normalised across *the caller's* candidate labels; `tei.predict.v1` answers scores across the
**model's own** classes and has no notion of candidate labels at all. Both sum to 1, over different
things. Dispatching on `modality` reads one as the other, which is exactly the failure the decision
to dispatch on `payload_schema` was made to prevent — and it had no case to prove it until now. No
code changed: `payload_schema` is a pass-through string and no SDK enumerates its values.

**The batch trap, documented where somebody will read it.** On that engine a batch is *always* a
list of lists: a flat array of two strings is read as one pair and answers **once, in silence**, and
three or more is a `422`. So the obvious "send my N texts as an array" is the single form that
quietly returns one wrong answer. It is also the argument for leaving the predict result undecoded —
one input returns a flat list and a batch returns a list of lists, from the same model and the same
endpoint.

**Five SDKs kept five different hand-written lists of rate-limit scopes, and they had diverged.**
This one said `embeddings`, `rerank`, `chat_completions`, `default`. One of the five said `chat` where the header says `chat_completions`, which
is a name a caller would key a map by and never match. The guide now contradicts itself about the
set too — the new §6.3 addendum names four scopes as today's complete set while the paragraph below
it, unchanged, says `PRM-129` gave `embeddings` and `rerank` their own. Raised with the platform; in
the meantime all five stop enumerating and say the set is read from the header. The fixed 60-second
wall-clock window is documented in its place, because that one is a fact a caller has to design
against: pace on the remaining count, never on an assumed rate.

**`raw_scores` on `/v1/rerank` is deliberately absent.** It was announced to us as a new optional
field in the gateway's own contract, with measurements — but §3.6 of the guide revision that was
supposed to carry it still documents only `query`, `documents` and `top_n`. Implementing from a
message rather than from the contract is how a field ends up in five SDKs and in no allowlist check.
Raised; it lands when the contract has it.

**`ErrorKind` is now `#[non_exhaustive]`**, which is the breaking half of this release and the reason
it is `0.6.0` rather than `0.5.1`.

Two variants were added, and a bare enum makes that a compile error for every caller who matched
exhaustively — so this release breaks them whatever it does. The catalogue has gone **15 rows, then
32, then 34**, each growth spurt the same breakage, and the doc comment on the enum had been promising
since `0.1.0` that *the catalogue grows* while the type guaranteed it would hurt. One truth stated in
two places with only one of them kept.

Marking it now, inside the break that was already happening, makes this the last time catalogue growth
costs a caller anything: match the variants you handle and leave a `_` arm for the rows that do not
exist yet. **`Error` stays exhaustive** on purpose — it enumerates the ways *this crate* can fail,
which is ours to decide and changes deliberately, not a mirror of a list the platform grows without
asking us.

Re-vendored at `2026-10-02 · PRM-167/173/174`, which adds **two error types** and takes one away.

- **`404 unknown-route`** → ``ErrorKind::UnknownRoute``
- **`405 method-not-allowed`** → ``ErrorKind::MethodNotAllowed``

`unknown-route` is deliberately **not** `not-found`, and the platform split them for these SDKs'
benefit: `not-found` is a statement about *data* — no usage row with that id belonging to this client
— which a caller may reasonably read as an empty result or retry. A bad URL is neither. All four SDKs
dispatch on the suffix, so one shared type would have made them do the wrong thing with one of the
two. `method-not-allowed` keeps Starlette's `Allow` header through the re-wrapping, so a `405` still
answers "then which verb".

**And it retires a claim this repository made about the gateway.** `spec/errors.json` carried, since
2026-09-27 and correctly then, that a `404` for an unserved route was *not* the problem+json envelope
— a bare `{"detail": "Not Found"}` with no `type` and no correlation ids in the body. `PRM-174`
fixed it; measured 2026-10-03 against the restarted stack. The note is corrected in place rather than
deleted, because **the fallback it forced stays and its reason has changed**: a body with no `type`
still arrives, but from a proxy returning HTML before the request ever reaches the gateway, which is
not the platform's to fix. One sentence of the corpus case that replays it said the same thing and is
corrected the same way.

**The corpus moved to v27, 49 cases**, and this SDK needed no change to pass it. The new case is a
second in-band stream failure whose payload is an **object** rather than the literal string
`stream interrupted` — added because mutating a runner to compare that exact text left all 48 cases
green. One fixture carried an in-band error and it carried the one message, so the corpus could not
tell *detect the key* from *compare the string*, which is precisely the distinction the platform asked
for and the one this SDK has always implemented. It passed on the first run; what changed is that it
is now **pinned rather than lucky**.

### 0.5.0 — 2026-10-02

**The modality guard was refusing nothing on `/v1/rerank`, and had not since rerank shipped.** The
accepted set at the call site was right; the separate hand-kept list of *known* modalities had never
heard of `rerank`, and the check returns early on a modality it does not recognise. So it could not
compare anything. Measured against a live deployment:

    qwen3-reranker  on chat    -> ALLOWED   (the doc comment promised 400)
    qwen3-embedding on rerank  -> ALLOWED   (same)
    qwen3-embedding on chat    -> refused   <- the only pairing that worked

The known set is now **derived** from the per-endpoint sets, so the two cannot drift again, and a
test refuses a literal at the call site — which is how a modality gets accepted by an endpoint
without ever becoming known.

#### Announced: the guard now refuses more

With the modality check enabled, these pairings used to reach the gateway and come back `400
modality-mismatch`. They are refused locally now, before the request:

    a rerank model on chat            a classification model on chat
    a chat model on /v1/rerank        anything with its own endpoint on /predict

Same failure, one round trip earlier, and as `Error::InvalidRequest` rather than `Error::Api`. Off by default, so a caller who never enabled it sees
no change.

#### And the reason the guard exists had expired

Every justification for it — the doc comment and the error message — said the gateway accepts chat on
an embedding model and answers `200` with degenerate billable output. All six wrong-modality
combinations measured live answer `400 modality-mismatch`: `RM-66` closed it, and **the guide
documenting `RM-66` was vendored in this repository the whole time**, read for its error-catalog rows
without the prose being re-read against the code.

The guard stays — it saves a request and a rate-limit unit, and the guide says a check of this kind
can stay — and no longer claims to save money. Nothing about its behaviour changed here, only what
it tells a caller.

#### New

- **`client.predict(model, &body, &PredictOptions::default())`** — the pass-through route, `POST /v1/models/{model}/predict`, for `classification`,
  `zero_shot` and `typed_decision`. `PredictResult::value` is a `serde_json::Value`, read through `decode()`, because `sst2-clf` answers a **top-level array** and a
  map would have failed on the first engine the platform put on this route.
- **`payload_schema` on the catalog entry** — the field that identifies which body a model takes.
  Dispatch on it rather than on the modality: `sst2-clf` and `von-decide` are both classifiers and
  want different bodies.

Four contract cases cover the route, recorded live, including the first recorded
`predict-backend-rejected` — the one catalog row whose status is the engine's rather than the
gateway's, with the engine's own error preserved under `backend_error`.

**The pass-through route: `client.predict(model, &body, &PredictOptions::default())`.**

`POST /v1/models/{model}/predict` serves three modalities the OpenAI surface has no shape for ---
`classification`, `zero_shot` and `typed_decision`. The body goes to the engine verbatim and its
answer comes back verbatim, so `PredictResult::value` is a `serde_json::Value`, read through `decode()`.

**Not defensive typing --- a measured constraint.** The three live engines answer:

    sst2-clf     [{"label":"POSITIVE","score":0.978}]        <- a top-level ARRAY
    von-decide   {"sequence":...,"labels":[...],"scores":[...]}
    laya-decide  {"model":...,"answers":{...},"routing":{...}}

A type that assumed an object would have failed on the first engine the platform shipped here, and
would have reported "this is not JSON" about valid JSON. There is no `classify(text)` either:
`sst2-clf` and `von-decide` are both classifiers and want different bodies, so a typed method would
promise a stability the route does not have.

`payload_schema` is now modelled on ``Model``. The spec names it as the field that identifies
the body shape, it is populated on all ten live models, and without it the route ships with no way
for a caller to know what to send.

Four contract cases, recorded live, replayed by all four SDKs --- including the first recorded
`predict-backend-rejected`, the one catalog row whose status is the engine's rather than the
gateway's, with the engine's own `backend_error` preserved.

**The modality guard refused nothing on `/v1/rerank`, and had not since rerank shipped.**

The endpoint passed its accepted set correctly. The separate, hand-kept list of *known* modalities
had never heard of `rerank`, and the check returns early on a modality it does not recognise --- so
the call could not compare anything. Measured:

    qwen3-reranker  on chat    -> ALLOWED   (the doc comment promised 400)
    qwen3-embedding on rerank  -> ALLOWED   (same)
    qwen3-embedding on chat    -> refused   <- the only pairing that worked

One truth in two places, and only one of them was updated. The known set is now **derived** from the
per-endpoint sets, so there is one. Adding the three predict modalities would have been dead on
arrival for the same reason, which is why this came first.

The test standing guard over exactly this had been given the wrong answer key: it asserted
`an equality against a hand-written list`, a literal typed into the test rather than the invariant its name claimed. A literal cannot
notice a new call site, so it passed throughout. It now reads the call sites out of the source, and
fails if an endpoint calls in without a row, or a row exists for no endpoint.

**The reason the guard exists had expired, and nothing could have told us but a measurement.**

Every justification --- module prose, the error message, a test name, a test's `match` --- said the
gateway accepts chat on an embedding model and answers `200` with degenerate billable output. All six
wrong-modality combinations measured against a live deployment answer `400 modality-mismatch`.
`RM-66` closed it, **the guide documenting `RM-66` is vendored in this repo**, and we read it for
error-catalog rows without re-reading the prose against the code.

The guard stays: it saves a request and a rate-limit unit, and the spec says a client-side check of
this kind can stay. It no longer claims to save money. Mutation testing could never have found this
--- mutating the code turns the tests red correctly, because the code was never wrong. Only
comparing a claim against a live deployment finds a premise that died.
Re-vendored at `2026-10-01 · PRM-164/167/173`. §2.7 is rewritten and renamed, from "Client types
--- who may hold a credential" to "Credentials --- whose they are, and who issues them".

**The axis moved.** The old section's rule was *confidential clients only*, with a distributed app
refused because it cannot keep a secret. The new rule is that **a credential identifies whoever pays
for consumption**, and the device stops being the question:

- An *integrator's* credential must never ship inside a distributed application --- a copy on every
  user's device is a copy of the identity that is granted models and billed.
- An *end client's own* credential may live on that client's own devices, phone and laptop alike.
  The principal, the grants and the bill are theirs, so a leak costs them their own account.

An app with a pasted secret is still a public client in RFC 8252's terms; it is accepted here when
the secret and the bill belong to the same person. That is the distinction the old §2.7 did not
separate, and it is the one that answers `A-34`.

Also now stated as a rule rather than an absence: **issuance is always a human administrator**.
There is no registration endpoint and no API an integrator can call to mint credentials for its
users, because issuing one opens a billing account. An application therefore has to treat **"no
credential yet"** as a first-class state rather than an error, and the request goes to the platform
rather than to the integrator. One credential per client, used on as many of that client's own
devices as they have --- per-device credentials are not issued, so an app assuming one install per
credential is wrong for any user with a phone and a laptop.

**Nothing in these three SDKs changes.** They take a token, or a callback that returns one, and
nothing about who obtained it is theirs to know. The section matters for the Swift SDK, whose
documented example reads a secret from the Keychain --- which the new §2.7 permits when that
credential is the end client's own, and still refuses when it is the integrator's.

`PRM-170` is superseded in substance rather than relaxed or tightened.

Re-vendored at `2026-09-29 · PRM-164/167/170`, which adds §2.7, "Client types — who may hold a
credential".

It answers `A-30` and reframes it: the question was never about mTLS. A client certificate shipped
inside an app somebody downloads is a secret shipped inside an app somebody downloads. What is
actually underneath is that **`client_id` is the billing principal** — grants and invoices are keyed
to it — so a `client_secret` on an end user's device is the *integrator's* identity copied onto
every one of their users' machines. Not a shape to harden; a shape not to have.

PKCE is not the alternative either, and not on cost: per-end-user identity has nowhere to live in
the platform's authorization or billing model, and the auth-service has no authorization endpoint
at all.

The answer that unblocks a distributed app is an integrator-controlled backend holding the
credential, with the app authenticating against that. Nothing here changes: these three SDKs run on
servers, which is the supported shape. It matters for the Swift SDK, whose documented example is an
on-device secret out of the Keychain.

**The modality preflight said "not in the catalog" about a catalog it can no longer see all of.**

With `verify_modality` on, a model missing from the catalog was refused locally as
`unknown-model`, on the stated grounds that the gateway reports the same thing and refusing only
saves the round trip. That was true while the catalog was the platform's full public list. Since
`PRM-167` it holds only the models the token has a grant for, which gives absence two causes this
SDK cannot distinguish:

    not registered at all    -> the gateway answers 400 unknown-model
    registered, not granted  -> the gateway answers 403 forbidden

Refusing locally told a caller to check the spelling of a name that was spelled correctly, and
**pre-empted the `403` whose entire job is to name the missing scope** — the error these SDKs work
hardest to make useful. So the request now goes, and the gateway answers a question only it can
answer. A typo costs one round trip; a missing grant gets diagnosed. That is the right way round.

What the preflight still does is the thing it was built for and can still prove: a model the
catalog **does** show carries its modality, so a mismatch is a fact rather than an inference.

*Corrected further down in these same notes: this paragraph claimed the gateway would not catch that
mismatch, which stopped being true at `RM-66` and was measured false on 2026-10-02.*

Three tests pinned the old behaviour and were correct when written. The Go one now asserts the
boundary with a request count rather than a server-side rejection, because only a number can tell
"stopped locally" from "reached the gateway".

**Not measured against a live deployment.** The only gateway available grants every model it has,
so the registered-but-ungranted case could not be produced. The reasoning stands on the guide's own
statement that `unknown-model` is checked before any scope check; it is not a measurement, and is
labelled as such rather than presented as one.

Re-vendored at guide revision `2026-09-29`, and the catalog means something narrower than it did.

`PRM-167` closed `GET /v1/models` to anonymous callers — which AXO-117 already fixed, by measuring
rather than by reading. What the guide adds is the half that was not visible from a `401`: the
endpoint now returns **only the models the token holds `model:<id>` scope for**, which makes
`models.mine()` an alias of `models.list()`. Same requirement, same filtering, same response.

So three claims in these SDKs were wrong in a way no test could catch: "everything the deployment
serves", "the full catalog", "the public catalog is not the answer to what can I call". The answer
is the same from both endpoints now.

And the fact this session got wrong by inference before the guide stated it: **an empty list means
the token holds no grants, not that the platform has no models.** Two different facts that only an
operator can distinguish. Measuring an empty `/v1/models` and concluding the registry was empty is
exactly the mistake, and it was made here today.

The guide also fixed the header exclusion list this SDK reported in `A-29`: `X-Request-ID` and
`X-Trace-ID` are present on endpoints the old list excluded, and only the rate-limit headers are
actually absent.

The catalog call carrying a credential is pinned by the corpus, not just fixed in the code.

`GET /v1/models` stopped being public on 2026-09-29 and the fix landed in four SDKs — and in zero
contract cases. That is the shape this corpus exists to prevent, for the fourth time: a behaviour
corrected in N languages and held in none, so the fifth SDK inherits nothing.

Manifest v25 asserts it on `catalog-list`, through a third form of header assertion:
`request_headers_present`, by name and with no value. The value could not be pinned — an
`Authorization` bearer is each runner's own test token, so asserting it would assert about the
harness rather than about the SDK. It joins `request_headers`, which compares a value, and
`request_headers_absent`, which forbids one.

Mutation-tested in both directions of the released break: restoring `authenticate=False` on the sync
`list()` fails `catalog-list` sync, and on the async one fails async. That flag is exactly what
shipped in `1.0.0rc5`, where `models.list()` returned `MissingCredentialsError` against the closed
endpoint — the corpus now refuses it.

**`GET /v1/models` stopped being public, and this SDK was the one that believed the documentation.**

The platform closed the endpoint without announcing it. Measured against a live deployment:

    GET /v1/models  with no token  ->  401 missing-credentials

Python passed `authenticate=False` there, because the guide said it was the one public endpoint and
this SDK did what the guide said. `client.models.list()` — the simplest call it has — returned
`MissingCredentialsError` from a released version. Go, Rust and Swift survived by accident: their
comments made the same claim while their code sent the token anyway.

Fixed, and the false comments in the other three corrected with it, along with three lines of
`docs/02-calls.md`.

**Three tests were pinning the broken behaviour**, one of them a security test, and all three were
right when they were written:

    test_the_catalog_call_sends_no_credential_at_all
    test_listing_the_public_catalog_sends_no_token [sync] [async]

They asserted that listing the catalog costs no credential, which was the correct property while
the endpoint was public — spending a token where none is wanted is a real thing to guard against.
They went on passing while the call returned 401 in the field. The security test now guards what
never depended on the endpoint being public: the client secret goes to the token request and
nowhere else, and a bearer token on the wire is the design rather than the bug.

The vendored guide still says otherwise at its line 647. That is a question for the platform team,
not something to paper over here.

The gap the corpus declared is closed by a measurement, not by a repair.

`stream-idempotent-replay` is re-recorded from a complete capture: two streamed calls with the same
`Idempotency-Key`, the second one's bytes and **all** of its headers. So
`meta.idempotent_replay_of` and `meta.request_id` are now asserted, because they were finally
measured on a stream rather than assumed from a guide sentence that happened to read true. A replay
carries its own `request_id`, distinct from the billed one, and the billed one is what
`X-Idempotent-Replay-Of` names — confirmed against `/v1/usage` in the same session, where the
original id returns `200` and the replay's own returns `404`. There is no streaming/non-streaming
asymmetry, which was the open question.

Every expectation was derived by running this SDK's own accumulator over the new bytes rather than
carried over from the old case, and the two bodies were compared before recording.

**The complete headers say two things a partial capture hid.** A replay names **no instance** —
neither `X-Prometheus-Instance` nor `X-Prometheus-Instance-Id`, where the original carried both,
which is correct because no replica served it. And, undocumented anywhere in the guide, **a replay
consumes request budget**: `remaining-requests` goes 59 → 58 across the two calls. Not generated, not
billed, and still counted against the RPM window. The case asserts `58`, so the number itself is the
evidence, and the question is with the platform team.

The tripwire that stood guard over the gap is deleted, which is what it was written for. The general
check it backstopped stays: no `meta.*` assertion may outrun the headers its case recorded.

A declared gap in the corpus is now an enforced one.

`stream-idempotent-replay` says `X-Idempotent-Replay-Of` and `X-Request-ID` were never captured for
a streamed replay, so asserting `meta.idempotent_replay_of` there would fail against an SDK doing
exactly the right thing. The obvious repair is to add the header to the case — which turns a thin
recording into an invented one. Nothing held the sentence that said not to.

Two checks now do, because one cannot. The first refuses any `meta.*` assertion whose sourcing
header is absent from that case's recorded bytes, so the accidental path fails saying *capture it
before asserting it* instead of looking like an SDK bug. That check cannot catch the deliberate path:
from inside a repository a recorded header and a typed one are the same bytes in the same file. So
the second is a tripwire on this one declared gap, written to be **removed** rather than satisfied —
whoever measures those headers for real deletes it in the same commit, which is a deliberate act with
a diff that says so.

**The question itself stays open, and it is the platform's.** The guide documents both headers on a
replay and says streamed replays work, so the expectation *reads* true; what is missing is anyone
having measured it on a stream. Attempting the capture locally established only that it cannot be
done here: the deployment answers `/health` with `{"status":"ok"}` while `GET /v1/models` returns an
**empty catalog**, so there is no model to generate against — which is incidentally a live instance of
the `/health` concern already open with the platform team.

A stream case can assert a field.

Nine of them could not. The streaming branch of all three runners read the stream-shaped keys --
`content`, `chunks`, `usage`, `tool_calls` -- and ignored `fields` in silence, so nothing about a
stream's `meta` was expressible: not the correlation ids, not the rate-limit budget, and not the two
idempotent-replay flags that `chat-idempotent-replay` has pinned since the day it was recorded. A
streamed replay that lost its entire `meta` passed all 44 cases.

Manifest v23 adds `meta.idempotent_replay` to `stream-idempotent-replay`, and the three runners
resolve `fields` on a streamed case against `meta` and nothing else -- `content`, `chunks`, `usage`
and `tool_calls` each already have a key of their own, and a second way to say the same thing is how
two ways eventually disagree. A test holds that restriction rather than a comment.

**What is still missing there is a recording, not an assertion.** This case captured only
`Idempotent-Replay`, where its non-streaming twin captured `X-Request-ID` and
`X-Idempotent-Replay-Of` too, so `meta.idempotent_replay_of` and `meta.request_id` are deliberately
*not* asserted: the headers are absent from these bytes, an SDK reporting them empty is correct, and
an expectation for them would be invented. The gap is declared in the case rather than left to be
discovered, because `idempotent_replay_of` is the only id that carries a usage row -- the replay's
own id does not.

Mutation-tested in all three: a stream that loses its `meta` now fails, where it used to pass.

The corpus now checks what the SDK **sent**, not only what it received.
Six cases supplied an `Idempotency-Key` and not one asked whether it was sent. The only assertions
about what went **out** were on the token endpoint, so an SDK that accepted a key and dropped it
passed all six: the four error cases replay a recorded envelope the mock serves regardless, and the
two replay cases assert the bytes that come **back**. A key that never leaves the process turns the
retry it exists to protect into a second billable generation — the premise the whole streamed-retry
decision rests on.

Manifest v22 adds `expect.request_headers` and `expect.request_headers_absent`, and the three
runners honour them. `chat-idempotent-replay` and `stream-idempotent-replay` now require the key on
the wire; `chat-completion-basic` requires that neither an idempotency key nor an instance pin is
**invented**, because a key the caller never asked for makes a retry silently replay a stale result,
and an invented pin opts them out of load balancing and failover without saying so.

Mutation-tested in all three, both directions: dropping the key on a stream fails the replay case,
and inventing one fails the basic case.

Rust carried no such claim on its own constant, so nothing here needed correcting — Python's and
Go's did, and both are fixed.

A streamed request rejected **before the stream begins** is now pinned as *recognised* -- the
precondition the retry above assumes, and the one thing nothing asserted.

No behaviour changed here: the status check that precedes SSE parsing has always been in place. What
changed is that removing it now fails. Measured by the Swift SDK team: reading the body as a stream
without first looking at whether the status was `4xx`/`5xx` still passed **all 40 cases of manifest
v19**. An SDK without that check turns every rejection into a silently empty response -- no error,
no content, and nothing for a caller to correlate -- and the corpus said that was fine. v20 pinned
what a recognised rejection leads to and never that it is recognised, which is the shape of AXO-110
in a different place, and of AXO-108 before it: fixed in three languages, pinned in none.

`expect.kind` for a stream that fails before it begins is `error`, **not** `stream_error`, and the
two are different contracts. `stream_error` is the in-band failure of a stream that has already
begun, after the `200`/`text/event-stream` headers are committed, and it is never retried. This one
arrives *instead of* a stream, as an ordinary status, and is -- which is what the guide (3.3) means
by a client that sets `stream: true` not getting a different error contract for doing so.

The runner could not have expressed this before, and would have failed quietly rather than loudly:
the `("error", _)` arm matched any operation and fell through to `client.chat(...)`, the
**non-streaming** call. A streaming error case would have passed while exercising `chat()` rather
than the `chat_stream()` it describes. It now opens the stream, iterates it and reports whichever
error came out, because an SDK that hands back a stream where a status belongs must not pass by
reading the refusal as an empty body.

Adding the case also found that the runner's own suffix-to-`ErrorKind` table had no
`backend-unavailable` entry -- a table parallel to the SDK's, which has always mapped it, covering
only what the corpus happened to use until now.

Manifest v21 adds two cases, both on `chat.completions.stream`.
`stream-rejected-before-it-begins-is-an-error` replays the `400` that `PRM-143` recorded live on
2026-09-27: the engine's own status and OpenAI-shaped body, passed through verbatim rather than
wrapped in a problem+json envelope, so `error_type_suffix` is null and the correlation ids exist
only in the headers -- which keeps AXO-108's header fallback pinned on the streaming path too.
`stream-rejected-before-it-begins-is-retryable-when-the-backend-is-unavailable` is the
`503 backend-unavailable` that justifies the retry v20 added. It carries no `Retry-After` in header
or body, unlike every other retryable error in the corpus, because the guide (5.2) confirms this
variant supplies no backoff signal at all; a shortened window would have been cheaper to test and
would have pinned a value the gateway never sends.

Mutation-tested in all three languages: removing the status check fails both new cases, and takes
`stream-retried-when-rejected-before-it-begins` with them.

The Swift SDK covers this by hand today, in `StreamRejectionTests.swift`. With the corpus holding
it, those tests are redundant and that team removes them.

A streamed request rejected **before the stream begins** is retried, and that is now deliberate
rather than incidental.

No behaviour changed here: `send` already ran the retry loop for a streamed request, because nobody
had excluded it. What changed is that `opts.streaming` not excluding a request from that loop is now
stated where a future reader will look, since the opposite is the reading that looks safer.
`chat_stream` and the `stream` module document the two failures separately instead of claiming a
stream is never retried -- the module's "failures arrive in band" was true only of the half that has
already begun.

The three had never agreed, and none of the three disagreements was a decision. Measured on
2026-09-27, counting requests that reached the server for a `429` on a streamed
`POST /v1/chat/completions`: **Python 1, Go 3, Rust 3** -- and all three documented never retrying a
stream at all. Python's stream opened its connection by another route and missed the shared retry
loop; Go and Rust ran it because nobody had excluded streaming from it.

The two that contradicted their own documentation were right. A stream can only fail this way
*before* any body byte exists -- the gateway reads the engine's status before the
`200`/`text/event-stream` headers are sent -- so nothing was generated and nothing was billed, and
reopening is a first generation rather than a second. It is also the only retry available: the
gateway performs **no** internal retries on a streamed request, so a `503 backend-unavailable`
arrives there after one attempt rather than three.

It could not be said a week earlier. Until `PRM-143`, landed 2026-09-27, a stream rejected before it
began arrived as a `200` whose body was nothing but `data: [DONE]` -- indistinguishable from a
legitimately empty answer, so "retry the rejections that precede the 200" named nothing. The gateway
now returns the engine's real status, and a connection that never opened returns
`503 backend-unavailable` in the problem+json envelope.

What has no exception, in all four SDKs: **a stream that has already begun is never retried.** There
the failure arrives in band, part of the answer was delivered, and part was billed.

Manifest v20 pins both halves, because three hand-written suites had pinned neither. The corpus
gained the shape needed to express it: a case can now serve an ordered *sequence* of responses, and
assert how many requests reached the server. `stream-retried-when-rejected-before-it-begins` serves a
`429` then the stream and requires two; `stream-not-retried-once-it-has-begun` queues a healthy
stream behind an interrupted one and requires that it is never reached -- an SDK that retried there
would pass every assertion of `stream-interrupted` while billing twice and returning the wrong
answer. Both were mutation-tested in all three languages: breaking the retry fails the first,
and the count is what catches it.

Agreed with the Mundus team so the Swift SDK is born with the behaviour rather than inheriting
whichever of the three it happened to read.

A contract case now pins reading the correlation ids out of the headers.

The behaviour shipped in all three SDKs on 2026-09-27 with one hand-written test per language and
nothing added to the shared corpus, so it was fixed three times and pinned zero. Measured by
building a fourth SDK against the corpus alone: removing its header fallback and replaying all
fourteen error cases passed every one of them. Manifest v19 adds
`error-correlation-ids-only-in-the-headers` -- a real 422 body with neither id, both ids in the
headers -- and removing the fallback from each of the three now fails it.

It is also the first case expecting **no** `type` at all, which two of the three runners could
not express: Rust's panicked on the null, and needed a name for the kind an absent type produces.

Two error types the platform added on 2026-09-27 are mapped: `503 capacity-exhausted` and
`predict-backend-rejected`.

`capacity-exhausted` means every replica of a model is busy rather than broken, which is the one
`503` where waiting is the whole remedy. It ships disabled on the platform, so nobody has met one
yet; it is mapped before anyone does.

`predict-backend-rejected` is the first catalogued error with no fixed status. The `predict` route
passes the body to the engine, so a refusal keeps the engine's status and its body, and the name
claims no cause. Retryability is therefore read from the status rather than from the name, and the
engine's own body is reachable through `backend_error`.

**The check that holds the catalog to the guide could not see the second one.** It required a
three-digit status and the guide's row says `4xx`, so the row never parsed and the set comparison
found nothing missing — a guard built to catch a row that disappears, blind to a row that never
arrived. It now validates the status cell instead of selecting on it, and an unreadable one is a
failure rather than a skip.

An error whose body is not a complete problem+json now still carries its correlation ids.

They were read from the body only. A validation failure forwarded verbatim from a backend has
neither id in its body and both in its headers, so the caller was handed an error with nothing to
take to the platform team. The body still wins where it has them, so nothing changes on an envelope
that honours the contract.

`ErrorKind::UnknownParameter` maps `400 unknown-parameter`.

Raised only when a request carries `require_parameters: true`, which asks the gateway to refuse an
unaccepted field instead of dropping it. It was documented in the platform guide we had already
vendored, and nothing here noticed: the parity guard holds the catalog to the SDKs, and nothing
held the guide to the catalog. That direction is now checked too.

**Structured output works.** `response_format` is now a declared field, forwarded verbatim.

The platform started honouring it on 2026-09-18 and told us. This SDK went on warning that it was
unsupported, and shipped a release five days later still saying so. In Python the field was also
*stripped*, so structured output was not reachable at all.

The answer comes back as a JSON **string** in the message content — parse it yourself
(`serde_json::from_str(&completion.content())`). It is not parsed here for the same reason tool-call `arguments` is not: a generation
stopped by `max_tokens` leaves it truncated, and a response object that raises from the inside is
worse than one that hands you what arrived.

Documentation only, and it matters because it had become wrong.

The rate-limit envelope no longer omits `trace_id`, and `X-RateLimit-Scope` now reaches the `429`
as well (platform guide `2026-09-19b`). Four statements in this SDK still described the old
behaviour. They now describe the current one, and say which deployments still behave the old way.

The body fallback for `scope` stays. It is no longer needed against a current deployment and is
kept for one predating the fix — which the platform team explicitly recommended.

Also documented: what happens to an `Idempotency-Key` whose request *failed* is undefined, is the
gateway's decision rather than this SDK's, and is being asked.

### 0.4.0 — 2026-09-20

`Message` gains `tool_call_id` and `name`, and derives `Default`.

Without `tool_call_id` a tool result cannot be matched to the call that asked for it, which means a
tool-use loop could not be closed in this SDK at all: it could read a tool call and had no way to
send the answer back. Python and Go have carried the field since the beginning.

Both fields are omitted from the wire when empty, so an ordinary turn is unchanged.

**Source-breaking, not behaviour-breaking**: a `Message { .. }` literal that names every field now
misses two. Add `..Default::default()`.

`RateLimit::scope`, and `Client::rate_limits()` keyed by it. `ApiError` also gains `rate_limit`, which Python and Go have always carried and this SDK did not.

The platform gave `/v1/embeddings`, `/v1/rerank` and `/v1/chat/completions` separate rate-limit
budgets. That made `Client::last_rate_limit()` a number from whichever endpoint answered last, with
nothing in the numbers saying so — a dashboard drawing "requests remaining" kept drawing a
plausible figure from another bucket. Ask about a particular budget by scope instead.

Measured against a deployment rather than taken from the announcement: `X-RateLimit-Scope` is on
successful responses and **absent on the 429**, where the body carries `"scope"` instead. So the
one response whose budget most needs attributing — the one telling you a bucket is exhausted — is
read from the body when the header is missing. The header wins when both are present.

A retried call can now explain its own duration without anyone reading a log.

A `Retry-After` of 0–60s, respected as it should be, looks from outside like one slow call among
fast ones. Three separate teams have reported that as a hang. `0.3.0` began logging the wait at
INFO, which was necessary and not sufficient: this SDK does not configure the host application's
logging, so the line is invisible until somebody opts in — at every entry point, and again at the
next one added. A latency metric cannot read a log line at all, and a latency metric is where this
keeps being seen.

`meta.waited_for` and `meta.attempts` on every response now carry it as data.

Subtract it from a wall-clock reading to get what the platform actually spent: the wait is
deliberately excluded from every duration this SDK reports, because sleeping is not service time.

Not covered: a call that waited and then failed anyway, which is the one whose duration most needs
explaining. Errors carry no response metadata today.

### 0.3.0 — 2026-09-16

Five error types the platform ships and nobody had mapped: `inconsistent-model-group`,
`unauthorized`, `invalid-date`, `invalid-range` and `range-too-large`.

They surfaced from the other side of a gap we reported. We told the platform that five suffixes
they send appear nowhere in their guide; they wrote a test comparing every `type` the gateway
raises against that table, and it found ten. Five were ours, and these five had been there longer
and nobody had noticed.

Three of them belong to the admin-scoped usage export, which this SDK does not call. They are
mapped anyway so the taxonomy is complete and an unknown suffix means what it says.

`POST /v1/rerank`, for models with `rerank` modality.

A reranker is a cross-encoder: it scores a query against each document and returns them ordered. It
generates nothing, so there are no completion tokens and billing is prompt-only.

Two things matter if you were doing this through the chat endpoint. The **whole document set is one
request**, not one per document — against a 60 RPM budget, scoring 50 candidates costs 1 unit rather
than 50. And each result's `index` points into the `documents` you sent, never into the results, so
a reordered result stays attributable to its input.

- `client.rerank(&RerankRequest).await`, returning `RerankResponse`, whose `ranking()` gives the input indices best-first. An empty `documents` list is refused before the wire, which the gateway would answer
  `400 validation-error`.

The SDK now says when it is waiting, and for how long.

The platform's `Retry-After` on a `429` is seconds until the window resets, so it runs 0–60. An SDK
that respects it — as it should — looks from outside like one slow call among fast ones, and that
arrives as a latency bug report. A wait long enough for a person to notice is now reported at INFO
with `delay_s`; sub-second backoff stays at DEBUG, because the noise worry is frequent small retries
rather than the rare long one.

The wait is reported as a wait, never folded into `duration_ms` — that is measured per attempt and
deliberately excludes time spent sleeping. Time waiting is not time the gateway took.

A token response in neither documented envelope is now a transport failure rather than an OAuth2
one.

A `4xx` carrying `error` is an OAuth2 outcome; a `5xx` carrying `type` is the gateway's own
problem+json. A body with neither — an HTML error page from a proxy or load balancer that answered
instead of the gateway — used to be reported as an OAuth2 failure, which tells a caller their
credentials are the problem. That is both wrong and the most expensive wrong answer available here:
the obvious next step is rotating a perfectly good secret.

`spec/errors.json` and this SDK's error mapping are now held together by a test (a unit test in `src/error.rs`): every
catalogued error must map to a class, and its retryability must match the catalog.

Go has had this from the start and it earned its keep the day the platform added two token errors —
it refused the change until both had a mapping, then refused again until their retryability matched.
This SDK had no equivalent, and shipped one of them with the wrong retryability until a hand-written
test caught it.

A failed token request is now typed by its envelope rather than read as OAuth2 unconditionally.

A `4xx` is an OAuth2 outcome in the RFC 6749 shape — wrong credentials, a scope the client does not
hold — and is never worth retrying. A `5xx` is the gateway failing to reach the auth-service,
arrives as problem+json, and `upstream-unavailable` **is** worth retrying. Reading both as OAuth2
left the 5xx with no type and no retryability, so a momentary blip looked exactly like bad
credentials and the request was abandoned rather than retried.

`not-configured` shares that status and is deliberately *not* retryable, which is why the suffix
drives the decision rather than the status.

**One address, not two.** The gateway serves both the inference API and `/oauth2/token`, so the
auth-service address is **gone from this SDK** rather than defaulted.

Keeping it as a field that defaults to the gateway would still have taught every consumer that a
second address exists. It does not, for them: the platform's auth-service is now reachable only
from inside the deployment, which is what it was always for. Removing the field removes the failure
it enabled — pointing the SDK at a self-hosted deployment used to mean changing two addresses, and
forgetting the second left the client asking the **official platform** for a token to use somewhere
else, with nothing erroring.

**Breaking**, and deliberately so while the surface is pre-1.0: `Config::auth_base_url` and `DEFAULT_AUTH_BASE_URL` are **removed**, along with `AXONIUM_AUTH_BASE_URL`. A deployment whose gateway
has no token endpoint wired up answers `not-configured`, which says exactly that rather than
failing obscurely.

Per-request usage lookup: what one of your own requests was charged, and why it stopped.

Both aggregate usage endpoints require `admin:read`, which a normal client neither has nor should
have — so `termination_reason` existed for callers who could not read it. The platform shipped this
after we made that case; this is the client half.

A replay has its own request id and no row of its own, so looking that id up is a `not-found` —
correctly, since a replay is not billed. `meta.idempotent_replay_of` names the generation that was
charged; look *that* up. The round trip is verified live in all three languages.

`termination_reason` is a plain string, not an enum. The platform proposed a fourth value this week
and withdrew it; the next one may not be withdrawn, and a closed set would turn a new value into a
parse failure for a caller who only wanted the token counts.

- `client.usage(request_id).await`, returning `RequestUsage`. `ErrorKind::NotFound` is new.

`ResponseMeta::idempotent_replay_of` carries, on a replay, the request id of the generation that was
actually billed.

A replay has its own request id and no usage row of its own, so looking that id up returns `404` —
correctly, since replaying reaches no model and is not billed. This header names the id that does
resolve, which makes it the only path from the response a caller received to the charge it
corresponds to. `None`/empty on anything that is not a replay.

The quickstart in the README used a model name that is not registered, so copying it produced
`400 unknown-model` rather than a completion. Examples and doc comments now use a real slug, and
each README says what a slug is: it never changes and is never reused, so pinning one is safe, but
which ones exist depends on the deployment and on what the token is granted — the catalog endpoint
is the source of truth, not the README.

Tool calls are now typed like everything around them.

They used to arrive as raw dicts while the message carrying them was a model. The asymmetry cost a
consumer real work twice over: reaching in by hand to read a name, and converting back to dicts to
feed a call into the next request. Both directions are now the same type.

`arguments` deliberately stays the model's own JSON **string** rather than a decoded object.
Decoding it at parse time would raise from inside a response model, for a caller who only wanted to
see what the model had managed to say — a generation stopped by `max_tokens` leaves a string that
was never going to parse. Decoding is a separate, explicit call that fails loudly, and the raw
string stays reachable either way.

**Breaking**, and deliberately so while the surface is still pre-1.0: anything indexing a tool call
as a dict/map/`Value` needs the field instead.

- `ToolCall` and `FunctionCall`, returned by `ChatCompletion::tool_calls()` and
  `ChatStream::tool_calls()`, and accepted by `Message::tool_calls`. `type` is spelled `kind` on
  the struct and still serialises as `type`.
- `call.parse_arguments()` decodes the arguments, failing with the new `Error::ToolCallArguments`,
  which carries the call id and the raw string. `call.name()` reads the function name directly.

Streamed tool calls are now reassembled for you.

A tool call arrives split across as many deltas as it takes — `{`, `"`, `city` — and the fragments
are individually invalid JSON. Only the first carries the identity, and **`index` is the
correlation key**, because `id` never repeats. Every consumer was writing that join by hand.

The assembled call is **byte-for-byte the shape a non-streaming completion returns**, `arguments`
included: still a JSON *string*, not a decoded object. That is deliberate — the same caller code
handles both, and a stream cut short by `max_tokens` hands back the fragment that did arrive
instead of raising or dropping the call. Check `finish_reason` before decoding.

A second contract case was recorded live for this: a single-call recording cannot tell `index`
correlation apart from any other strategy, so a stream with two concurrent calls was recorded to
give the case teeth. The manifest is now 25 cases, and all three SDKs replay both.

- `ChatStream::tool_calls()`, alongside `content()` and `usage()`.
- **Breaking:** `Chunk::tool_calls()` is renamed `Chunk::tool_call_fragments()`. It always returned
  fragments rather than calls, and the name said otherwise at exactly the moment a real
  `tool_calls()` appeared one level up. Renamed now, while the crate is `0.x` and a consumer pays
  a compile error rather than a silent wrong result.

### 0.2.0

- **Spans and events behind a `tracing` feature**, off by default so the crate stays free of the
  dependency for anyone tracing with something else. Each attempt carries method, path, model,
  status, attempt, duration and the gateway's correlation IDs. Prompts, completions and credentials
  are never emitted, pinned by a test that fails if the crate is changed to emit any.

### 0.1.0

First release. Core implemented: chat, streaming with cancellation on drop, embeddings, images, both credential
modes, idempotency keys, instance pinning and the full error taxonomy. All 24 shared contract cases
pass. Structured logging, a tracing hook and publication to crates.io remain.

`Config` and `Client` redact the client secret from `Debug`, which a derived implementation printed
in full.

**Not in 0.1.0, and a crates.io version cannot be replaced:** structured logging, a tracing hook,
and reassembly of streamed tool calls. The first two arrived in `0.2.0`.

---

## Legacy (pre-rewrite, single-package Python SDK)

Versions `v0.1.0` through `v0.6.0` targeted the first-generation Prometheus platform and are
documented in the [v0.6.0 release notes](https://github.com/Root1V/axonium-sdk/releases/tag/v0.6.0).
That code is preserved at tag `v0.6.0`.
