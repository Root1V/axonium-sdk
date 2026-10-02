# Changelog

Each language SDK versions independently. Entries are grouped by language and use tags of the
form `python/vX.Y.Z`, `go/vX.Y.Z`, `rust/vX.Y.Z`.

## Python

### Unreleased

Nothing yet.

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
