import { test } from "node:test";
import assert from "node:assert/strict";

import { DEFAULT_GATEWAY_BASE_URL, resolveConfig } from "../src/config.ts";
import {
  Transport,
  MAX_IDEMPOTENCY_KEY_LENGTH,
  rateLimitFrom,
  metaFrom,
} from "../src/transport.ts";
import { DEFAULT_RETRY, NO_RETRY, delayFor, retryAfterMs, CooldownRegistry } from "../src/retry.ts";
import {
  APIError,
  BackendUnavailableError,
  ForbiddenError,
  InvalidRequestError,
  RateLimitError,
  TimeoutError,
  TransportError,
  UnknownModelError,
} from "../src/errors.ts";
import { Axonium, imageFromBytes } from "../src/client.ts";
import { StreamInterruptedError } from "../src/errors.ts";
import { Stub, problemBody } from "./stub.ts";

const CHAT = "/v1/chat/completions";

/** Fast retries: the policy under test is the SDK's, the sleeping is not what is being measured. */
const FAST = { ...DEFAULT_RETRY, initialBackoff: 1, maxBackoff: 10, jitter: false };

function transport(stub: Stub, retry = FAST, extra: Record<string, unknown> = {}): Transport {
  return new Transport(
    resolveConfig({
      gatewayBaseURL: "https://gw.test",
      clientId: "id",
      clientSecret: "secret",
      fetch: stub.fetch,
      ...extra,
    }),
    retry,
  );
}

test("a successful call sends a bearer token and nothing the caller did not ask for", async () => {
  // Asserted ABSENT rather than merely unasserted. An SDK that invents an idempotency key makes a
  // retry silently replay a stale result instead of generating; an invented instance pin opts the
  // caller out of load balancing and failover without saying so. Both are the kind of helpfulness
  // only visible from the request side.
  const stub = new Stub().token().on(CHAT, { body: JSON.stringify({ ok: true }) });
  const result = await transport(stub).sendJSON("POST", CHAT, { body: { model: "m" } });

  assert.deepEqual(result.value, { ok: true });
  const sent = stub.lastFor(CHAT);
  assert.equal(sent?.headers.get("Authorization"), "Bearer test-token-1");
  assert.equal(sent?.headers.get("Content-Type"), "application/json");
  assert.equal(sent?.headers.get("Idempotency-Key"), null);
  assert.equal(sent?.headers.get("X-Prometheus-Instance"), null);
});

test("the credential never reaches the gateway, only the token does", async () => {
  // The secret goes to the token endpoint in a body and nowhere else. A header carrying it would be
  // in every proxy log between here and the gateway.
  const stub = new Stub().token().on(CHAT, { body: "{}" });
  await transport(stub).sendJSON("POST", CHAT, { body: {} });
  const sent = stub.lastFor(CHAT);
  const serialised =
    JSON.stringify(sent ? Object.fromEntries(sent.headers) : {}) + (sent?.body ?? "");
  assert.ok(!serialised.includes("secret"), "the client secret appeared in the gateway request");
});

test("the per-call options reach the wire when asked for", async () => {
  const stub = new Stub().token().on(CHAT, { body: "{}" });
  await transport(stub).sendJSON("POST", CHAT, {
    body: {},
    idempotencyKey: "key-1",
    instance: "replica-7",
    requireParameters: true,
  });
  const sent = stub.lastFor(CHAT);
  assert.equal(sent?.headers.get("Idempotency-Key"), "key-1");
  assert.equal(sent?.headers.get("X-Prometheus-Instance"), "replica-7");
  assert.equal(sent?.headers.get("X-Prometheus-Require-Parameters"), "true");
});

test("an over-long idempotency key is refused before a round trip", async () => {
  // No request at all is the assertion. The gateway reports an over-long key as a *conflict*, which
  // misdirects whoever reads it.
  const stub = new Stub().token().on(CHAT, { body: "{}" });
  await assert.rejects(
    transport(stub).sendJSON("POST", CHAT, {
      body: {},
      idempotencyKey: "k".repeat(MAX_IDEMPOTENCY_KEY_LENGTH + 1),
    }),
    InvalidRequestError,
  );
  assert.equal(stub.countFor(CHAT), 0, "the request was sent anyway");
});

test("a non-retryable error is raised on the first attempt", async () => {
  const stub = new Stub()
    .token()
    .on(CHAT, { status: 400, body: problemBody("unknown-model", { status: 400 }) });

  await assert.rejects(transport(stub).sendJSON("POST", CHAT, { body: {} }), UnknownModelError);
  assert.equal(stub.countFor(CHAT), 1, "a 400 was retried");
});

test("a retryable error is retried up to the attempt budget and then raised", async () => {
  const stub = new Stub()
    .token()
    .on(CHAT, { status: 502, body: problemBody("upstream-error", { status: 502 }) });
  await assert.rejects(transport(stub).sendJSON("POST", CHAT, { body: {} }), /upstream-error/);
  assert.equal(stub.countFor(CHAT), DEFAULT_RETRY.maxAttempts, "attempts did not match the policy");
});

test("a retry that succeeds reports how many attempts it took, on the caller's path", async () => {
  // Asserted through `meta`, which is what a caller holds. The first version of this test read the
  // `attempts` field off `send`'s return -- a shape no public call hands back, so it was green while
  // `sendJSON` dropped both numbers and every caller of this SDK saw a retried call as a slow one.
  // The other four SDKs put them on meta; this one computed them and threw them away.
  const stub = new Stub()
    .token()
    .on(
      CHAT,
      { status: 503, body: problemBody("capacity-exhausted", { status: 503 }) },
      { body: JSON.stringify({ ok: 1 }) },
    );
  const { meta } = await transport(stub).sendJSON("POST", CHAT, { body: {} });
  assert.equal(meta.attempts, 2);
  assert.ok(meta.waitedMs > 0, "a wait happened and was not reported");
});

test("a call that waited and then failed anyway still reports the wait", async () => {
  // The call whose duration most needs explaining. Four of the five SDKs cannot answer it -- the
  // exception carries no response metadata at all -- and the docs list that as a known limit. Here
  // the error already carries `meta`, so the only thing missing was stamping the loop's counters on
  // it before it leaves.
  const stub = new Stub()
    .token()
    .on(CHAT, { status: 503, body: problemBody("capacity-exhausted", { status: 503 }) });
  await assert.rejects(transport(stub).sendJSON("POST", CHAT, { body: {} }), (error: unknown) => {
    assert.ok(error instanceof APIError);
    assert.equal(
      error.meta.attempts,
      3,
      "gave up after the attempt budget and said it was the first",
    );
    assert.ok(error.meta.waitedMs > 0, "slept between attempts and reported no wait");
    return true;
  });
});

test("a first-time success says it waited for nothing, rather than saying nothing", async () => {
  // Zero and absent are different claims. A caller subtracting meta.waitedMs from its own latency
  // must not silently subtract undefined, so these are required fields and not optional ones.
  const stub = new Stub().token().on(CHAT, { body: JSON.stringify({ ok: 1 }) });
  const { meta } = await transport(stub).sendJSON("POST", CHAT, { body: {} });
  assert.equal(meta.attempts, 1);
  assert.equal(meta.waitedMs, 0);
});

test("no retries means one request, and the first failure is what the caller sees", async () => {
  const stub = new Stub()
    .token()
    .on(CHAT, { status: 502, body: problemBody("upstream-error", { status: 502 }) });
  await assert.rejects(
    transport(stub, NO_RETRY).sendJSON("POST", CHAT, { body: {} }),
    /upstream-error/,
  );
  assert.equal(stub.countFor(CHAT), 1);
});

test("a 401 refreshes the token once and retries with the new one", async () => {
  // Separate from the backoff path: nothing needs waiting for, the token was stale rather than the
  // model busy.
  const stub = new Stub()
    .token()
    .on(
      CHAT,
      { status: 401, body: problemBody("token-expired", { status: 401 }) },
      { body: JSON.stringify({ ok: 1 }) },
    );
  const t = transport(stub);
  const result = await t.sendJSON("POST", CHAT, { body: {} });

  assert.deepEqual(result.value, { ok: 1 });
  assert.equal(stub.countFor("/oauth2/token"), 2, "the token was not refreshed");
  assert.equal(stub.lastFor(CHAT)?.headers.get("Authorization"), "Bearer test-token-2");
});

test("a 401 that persists after one refresh is raised rather than looped", async () => {
  // The guard against an infinite refresh loop against a credential that will never work.
  const stub = new Stub()
    .token()
    .on(CHAT, { status: 401, body: problemBody("token-expired", { status: 401 }) });
  await assert.rejects(transport(stub).sendJSON("POST", CHAT, { body: {} }), /token-expired/);
  assert.ok(stub.countFor("/oauth2/token") <= 2, "the token endpoint was hammered");
});

test("Retry-After is honoured verbatim instead of the backoff", async () => {
  // The gateway computes it from the real reset time, which beats any local guess.
  const stub = new Stub().token().on(
    CHAT,
    {
      status: 429,
      headers: { "Retry-After": "0.02" },
      body: problemBody("rate-limit-exceeded-requests", { status: 429 }),
    },
    { body: JSON.stringify({ ok: 1 }) },
  );
  const started = performance.now();
  // maxBackoff has to stay ABOVE the Retry-After or it is refused rather than honoured, which is
  // what the first version of this test got wrong: it inherited FAST's 10ms cap and then asserted
  // that a 20ms Retry-After was obeyed. The contrast under test is 20ms honoured against a 5s
  // backoff, so both numbers have to be inside the cap.
  await transport(stub, { ...DEFAULT_RETRY, initialBackoff: 5_000, jitter: false }).sendJSON(
    "POST",
    CHAT,
    { body: {} },
  );
  const elapsed = performance.now() - started;
  assert.ok(elapsed < 1_000, `waited ${elapsed}ms, so the backoff was used instead of Retry-After`);
});

test("a Retry-After longer than the cap is surfaced rather than slept through", async () => {
  // A library that silently blocks for ten minutes has made a scheduling decision that belongs to
  // the application.
  const stub = new Stub().token().on(CHAT, {
    status: 429,
    headers: { "Retry-After": "600" },
    body: problemBody("rate-limit-exceeded-requests", { status: 429 }),
  });
  await assert.rejects(transport(stub).sendJSON("POST", CHAT, { body: {} }), (err: unknown) => {
    assert.ok(err instanceof RateLimitError);
    assert.equal(err.retryAfter, 600_000, "the caller was not told how long to wait");
    return true;
  });
  assert.equal(stub.countFor(CHAT), 1);
});

test("a client timeout is never retried", async () => {
  // The backend is probably still generating, so a retry queues a second billable generation on top
  // of the first rather than resuming it.
  const stub = new Stub().token().on(CHAT, { hang: true });
  await assert.rejects(
    transport(stub).sendJSON("POST", CHAT, { body: {}, timeout: 20 }),
    (err: unknown) => {
      assert.ok(err instanceof TimeoutError);
      assert.match(err.message, /second billable generation/);
      return true;
    },
  );
  assert.equal(stub.countFor(CHAT), 1, "a timeout was retried");
});

test("a caller's abort is reported as a cancellation, not as a timeout", async () => {
  // Different remedies: a timeout means the generation may still be running and billing; a
  // cancellation is the caller's own decision and needs no warning.
  const stub = new Stub().token().on(CHAT, { hang: true });
  const controller = new AbortController();
  const pending = transport(stub).sendJSON("POST", CHAT, { body: {}, signal: controller.signal });
  controller.abort();
  await assert.rejects(pending, (err: unknown) => {
    assert.ok(err instanceof TransportError);
    assert.ok(!(err instanceof TimeoutError));
    assert.match(err.message, /cancelled by the caller/);
    return true;
  });
});

test("a transport failure is retried, unlike a timeout", async () => {
  // Nothing reached a model, so a retry is a first generation rather than a second.
  const stub = new Stub()
    .token()
    .on(CHAT, { throws: new TypeError("fetch failed") }, { body: JSON.stringify({ ok: 1 }) });
  const result = await transport(stub).sendJSON("POST", CHAT, { body: {} });
  assert.deepEqual(result.value, { ok: 1 });
  assert.equal(stub.countFor(CHAT), 2);
});

test("backend-unavailable records a cooldown, and the next call fails locally", async () => {
  // Keyed by MODEL rather than by gateway: every replica of that model is out while other models on
  // the same gateway keep serving, so cooling the gateway would refuse requests it would answer.
  const stub = new Stub().token().on(CHAT, {
    status: 503,
    headers: { "Retry-After": "30" },
    body: problemBody("backend-unavailable", { status: 503 }),
  });
  const t = transport(stub, NO_RETRY);

  await assert.rejects(
    t.sendJSON("POST", CHAT, { body: {}, model: "busy" }),
    BackendUnavailableError,
  );
  const before = stub.countFor(CHAT);

  await assert.rejects(t.sendJSON("POST", CHAT, { body: {}, model: "busy" }), (err: unknown) => {
    assert.ok(err instanceof BackendUnavailableError);
    assert.match(err.message, /Refused locally, without a request/);
    return true;
  });
  assert.equal(stub.countFor(CHAT), before, "the cooled model reached the gateway anyway");

  // A different model is unaffected, which is the point of keying by model.
  stub.on(CHAT, { body: JSON.stringify({ ok: 1 }) });
  await t.sendJSON("POST", CHAT, { body: {}, model: "fine" });
  assert.equal(stub.countFor(CHAT), before + 1);
});

test("the correlation ids are read from the headers when the body is not an envelope", async () => {
  // Load-bearing: an HTML page from a proxy that never reached the gateway still arrives, and on
  // those the ids exist only in the headers. Reading the body alone left a caller with nothing to
  // report on exactly the failures where they need it.
  const stub = new Stub().token().on(CHAT, {
    status: 422,
    headers: { "X-Request-ID": "req-9", "X-Trace-ID": "trace-9" },
    body: JSON.stringify({ detail: [{ loc: ["body", "model"], msg: "Field required" }] }),
  });
  await assert.rejects(
    transport(stub, NO_RETRY).sendJSON("POST", CHAT, { body: {} }),
    (err: unknown) => {
      assert.ok(err instanceof APIError);
      assert.equal(err.meta.requestId, "req-9");
      assert.equal(err.meta.traceId, "trace-9");
      assert.equal(err.typeSuffix, "", "a type was invented for a body that carried none");
      return true;
    },
  );
});

test("the body's ids win over the headers' when both are present", async () => {
  const stub = new Stub().token().on(CHAT, {
    status: 403,
    headers: { "X-Request-ID": "from-header" },
    body: problemBody("forbidden", { status: 403, request_id: "from-body" }),
  });
  await assert.rejects(
    transport(stub, NO_RETRY).sendJSON("POST", CHAT, { body: {} }),
    (err: unknown) => {
      assert.ok(err instanceof ForbiddenError);
      assert.equal(err.meta.requestId, "from-body");
      return true;
    },
  );
});

test("the rate-limit snapshot is parsed and kept as the last seen", async () => {
  const stub = new Stub().token().on(CHAT, {
    headers: {
      "X-RateLimit-Scope": "predict",
      "X-RateLimit-Limit-Requests": "60",
      "X-RateLimit-Remaining-Requests": "59",
    },
    body: "{}",
  });
  const t = transport(stub);
  const { meta } = await t.sendJSON("POST", CHAT, { body: {} });
  assert.equal(meta.rateLimit?.scope, "predict");
  assert.equal(meta.rateLimit?.limitRequests, 60);
  assert.equal(t.lastRateLimit?.remainingRequests, 59);
});

test("no rate-limit headers means no snapshot, which is not a budget of zero", () => {
  // Reporting zeros would tell a caller they are rate-limited when nobody said so.
  assert.equal(rateLimitFrom(new Headers()), undefined);
  assert.equal(rateLimitFrom(new Headers({ "X-RateLimit-Scope": "chat" }))?.scope, "chat");
});

test("the replay flags are read, including the id that was actually charged", () => {
  // A replay is neither generated nor billed, and `idempotentReplayOf` is the only id with a usage
  // row -- a replay's own id has none, so a caller reconciling cost has nothing else to go on.
  const meta = metaFrom(
    new Headers({
      "Idempotent-Replay": "true",
      "X-Idempotent-Replay-Of": "original-1",
      "X-Request-ID": "replay-1",
    }),
  );
  assert.equal(meta.idempotentReplay, true);
  assert.equal(meta.idempotentReplayOf, "original-1");
  assert.equal(meta.requestId, "replay-1");
});

test("a non-JSON success body is reported as such rather than as an empty result", async () => {
  const stub = new Stub().token().on(CHAT, { body: "<html>hello</html>" });
  await assert.rejects(transport(stub).sendJSON("POST", CHAT, { body: {} }), (err: unknown) => {
    assert.ok(err instanceof TransportError);
    assert.match(err.message, /not JSON/);
    return true;
  });
});

/* --- the policy arithmetic, which the cases above exercise only indirectly ----------------- */

test("delayFor stops at the attempt budget", () => {
  const policy = { maxAttempts: 3, initialBackoff: 100, maxBackoff: 10_000, jitter: false };
  assert.equal(delayFor(policy, 1, undefined), 100);
  assert.equal(delayFor(policy, 2, undefined), 200);
  assert.equal(delayFor(policy, 3, undefined), undefined, "a fourth attempt was offered");
});

test("delayFor caps the exponential and refuses an over-long Retry-After", () => {
  const policy = { maxAttempts: 9, initialBackoff: 1_000, maxBackoff: 5_000, jitter: false };
  assert.equal(delayFor(policy, 5, undefined), 5_000, "the exponential was not capped");
  assert.equal(delayFor(policy, 1, 2_000), 2_000, "Retry-After was not honoured verbatim");
  assert.equal(delayFor(policy, 1, 60_000), undefined, "an over-long wait was slept through");
});

test("jitter spreads the wait across the whole window", () => {
  // Full jitter rather than equal jitter: half a synchronised wait is still the half that matters
  // when a hundred clients come back from one outage.
  const policy = { maxAttempts: 9, initialBackoff: 1_000, maxBackoff: 1_000, jitter: true };
  const samples = Array.from({ length: 200 }, () => delayFor(policy, 1, undefined) as number);
  assert.ok(Math.min(...samples) < 200, "the low end of the window is never used");
  assert.ok(Math.max(...samples) > 800, "the high end of the window is never used");
  assert.ok(samples.every((s) => s >= 0 && s < 1_000));
});

test("Retry-After is read as seconds or as a date, and a past date reads as absent", () => {
  assert.equal(retryAfterMs(new Headers({ "Retry-After": "30" })), 30_000);
  const now = Date.now();
  assert.ok(
    Math.abs(
      (retryAfterMs(new Headers({ "Retry-After": new Date(now + 10_000).toUTCString() }), now) ??
        0) - 10_000,
    ) < 1_500,
  );
  // A date in the past means the clocks disagree. Treating that as "retry immediately" turns a
  // disagreement into a hot loop.
  assert.equal(
    retryAfterMs(new Headers({ "Retry-After": new Date(now - 60_000).toUTCString() }), now),
    undefined,
  );
  assert.equal(retryAfterMs(new Headers({ "Retry-After": "-5" })), undefined);
  assert.equal(retryAfterMs(new Headers()), undefined);
});

test("a cooldown never shortens under a concurrent, smaller wait", () => {
  const registry = new CooldownRegistry();
  registry.record("k", 10_000, 0);
  registry.record("k", 1_000, 0);
  assert.ok((registry.remaining("k", 0) ?? 0) > 5_000, "the larger wait was overwritten");
  assert.equal(registry.remaining("k", 20_000), 0, "an elapsed cooldown was not forgotten");
});

/* --- what the shared corpus cannot assert, and says so ------------------------------------- */

test("rerank's ranking reads the indices, not the positions in results", async () => {
  // NOT COVERED BY THE CORPUS, and it has the data to cover it: rerank.json's indices are [2,0,1], so
  // "position in results" and "index into your documents" genuinely differ there -- and zero cases
  // assert `ranking`. Mutating it to return positions left all 49 green.
  //
  // It is the accessor whose entire purpose is that the index points into the array YOU sent, which is
  // what keeps a reordered result attributable to its input. Every SDK in this family exposes it and
  // none of them pin it. Recorded as AXO-129 rather than fixed in the corpus here, because adding
  // `expect.fields` for it needs an accessor in the Go and Rust runners too.
  const stub = new Stub().token().on("/v1/rerank", {
    body: JSON.stringify({
      model: "qwen3-reranker",
      results: [
        { index: 2, relevance_score: 0.9 },
        { index: 0, relevance_score: 0.5 },
        { index: 1, relevance_score: 0.1 },
      ],
    }),
  });
  const api = new Axonium({
    gatewayBaseURL: "https://gw.test",
    clientId: "id",
    clientSecret: "secret",
    fetch: stub.fetch,
  });
  const ranked = await api.rerank.create({
    model: "qwen3-reranker",
    query: "q",
    documents: ["a", "b", "c"],
  });
  assert.deepEqual(
    [...ranked.ranking],
    [2, 0, 1],
    "ranking returned positions rather than indices",
  );
});

test("an in-band stream error is detected by the key, whatever its payload", async () => {
  // The corpus now carries a second shape for this (v27), added because mutating the detection to
  // match the literal string "stream interrupted" left every case green. Kept here as well because
  // this is the one place the three payload shapes sit side by side.
  for (const payload of ['"stream interrupted"', '{"code":"x","message":"y"}', "null", "123"]) {
    const stub = new Stub().token().on("/v1/chat/completions", {
      headers: { "Content-Type": "text/event-stream" },
      body: `data: {"choices":[{"delta":{"content":"hola"}}]}\n\ndata: {"error":${payload}}\n\n`,
    });
    const api = new Axonium({
      gatewayBaseURL: "https://gw.test",
      clientId: "id",
      clientSecret: "secret",
      fetch: stub.fetch,
    });
    const stream = await api.chat.completions.stream({
      model: "m",
      messages: [{ role: "user", content: "hi" }],
    });
    await assert.rejects(stream.finalMessage(), (err: unknown) => {
      assert.ok(err instanceof StreamInterruptedError, `payload ${payload} was not detected`);
      assert.equal(err.partialContent, "hola", "the partial text was lost");
      return true;
    });
  }
});

test("a stream cannot be consumed twice", async () => {
  // A one-shot sequence over a socket. Iterating again would silently yield nothing, which reads as a
  // model that produced no output.
  const stub = new Stub().token().on("/v1/chat/completions", {
    headers: { "Content-Type": "text/event-stream" },
    body: `data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n`,
  });
  const api = new Axonium({
    gatewayBaseURL: "https://gw.test",
    clientId: "id",
    clientSecret: "secret",
    fetch: stub.fetch,
  });
  const stream = await api.chat.completions.stream({
    model: "m",
    messages: [{ role: "user", content: "hi" }],
  });
  assert.equal((await stream.finalMessage()).content, "ok");
  await assert.rejects(stream.finalMessage(), StreamInterruptedError);
});

test("an http(s) image part is refused before the round trip", async () => {
  // The gateway refuses it as an SSRF mitigation, so an API that accepted one would accept something
  // that always fails.
  const stub = new Stub().token().on("/v1/chat/completions", { body: "{}" });
  const api = new Axonium({
    gatewayBaseURL: "https://gw.test",
    clientId: "id",
    clientSecret: "secret",
    fetch: stub.fetch,
  });
  await assert.rejects(
    api.chat.completions.create({
      model: "qwen3-vl-8b",
      messages: [
        {
          role: "user",
          content: [
            { type: "text", text: "¿qué ves?" },
            { type: "image_url", image_url: { url: "https://example.com/a.png" } },
          ],
        },
      ],
    }),
    InvalidRequestError,
  );
  assert.equal(stub.countFor("/v1/chat/completions"), 0, "the request was sent anyway");
});

test("imageFromBytes produces a data URI the gateway accepts", () => {
  const part = imageFromBytes(new Uint8Array([0x89, 0x50, 0x4e, 0x47]), "image/png");
  assert.equal(part.type, "image_url");
  assert.match(part.image_url.url, /^data:image\/png;base64,/);
  assert.ok(!/https?:/.test(part.image_url.url));
});

test("a stream assembles reasoning separately from content", async () => {
  // NOT COVERED BY THE CORPUS either, and the four other SDKs all have it: the manifest pins a
  // stream's content through `expect.content` and has no key for reasoning, so five SDKs agreeing was
  // a coincidence. Measured live on qwen3-0.6b: 120 chunks, content empty, every delta reasoning --
  // a UI showing only `content` displays nothing, which looks like a broken SDK. AXO-130.
  const stub = new Stub().token().on("/v1/chat/completions", {
    headers: { "Content-Type": "text/event-stream" },
    body:
      `data: {"choices":[{"delta":{"role":"assistant","content":null}}]}\n\n` +
      `data: {"choices":[{"delta":{"reasoning_content":"Pien"}}]}\n\n` +
      `data: {"choices":[{"delta":{"reasoning_content":"so…"}}]}\n\n` +
      `data: {"choices":[{"delta":{"content":"listo"},"finish_reason":"stop"}]}\n\n` +
      `data: [DONE]\n\n`,
  });
  const api = new Axonium({
    gatewayBaseURL: "https://gw.test",
    clientId: "id",
    clientSecret: "secret",
    fetch: stub.fetch,
  });
  const stream = await api.chat.completions.stream({
    model: "m",
    messages: [{ role: "user", content: "hi" }],
  });
  const final = await stream.finalMessage();
  assert.equal(final.content, "listo");
  assert.equal(final.reasoning, "Pienso…", "the reasoning deltas were dropped");
  assert.equal(stream.finish, "stop");
});

test("the gateway's ignored-parameter report is exposed, not swallowed", async () => {
  // The endpoint takes an OpenAI-compatible SUBSET and names back what it dropped. Until PRM-127 these
  // vanished in silence, and the guide's own words on fixing it are why this is exposed: a setting that
  // does nothing and says nothing is indistinguishable from one that works. Reading the header and
  // discarding it would restore that silence inside the SDK.
  const stub = new Stub().token().on("/v1/chat/completions", {
    headers: { "X-Prometheus-Ignored-Parameters": "logit_bias, seed" },
    body: JSON.stringify({ choices: [{ message: { content: "ok" } }] }),
  });
  const api = new Axonium({
    gatewayBaseURL: "https://gw.test",
    clientId: "id",
    clientSecret: "secret",
    fetch: stub.fetch,
  });
  const answer = await api.chat.completions.create({
    model: "m",
    messages: [{ role: "user", content: "hi" }],
    seed: 7,
    logit_bias: { "1": 2 },
  });
  assert.deepEqual(answer.meta.ignoredParameters, ["logit_bias", "seed"]);
});

test("no ignored-parameter header means undefined, which is not an empty list", async () => {
  // The contract says the header is present only when there is something to report, so its presence
  // always means something. An empty array would claim the gateway looked and found nothing, which is a
  // different statement from the gateway not having said.
  const stub = new Stub().token().on("/v1/chat/completions", {
    body: JSON.stringify({ choices: [{ message: { content: "ok" } }] }),
  });
  const api = new Axonium({
    gatewayBaseURL: "https://gw.test",
    clientId: "id",
    clientSecret: "secret",
    fetch: stub.fetch,
  });
  const answer = await api.chat.completions.create({
    model: "m",
    messages: [{ role: "user", content: "hi" }],
  });
  assert.equal(answer.meta.ignoredParameters, undefined);
});

test("requireParameters asks the gateway to refuse instead of dropping", async () => {
  const stub = new Stub().token().on("/v1/chat/completions", {
    status: 400,
    body: problemBody("unknown-parameter", { status: 400 }),
  });
  const api = new Axonium({
    gatewayBaseURL: "https://gw.test",
    clientId: "id",
    clientSecret: "secret",
    fetch: stub.fetch,
    retry: NO_RETRY,
  });
  await assert.rejects(
    api.chat.completions.create(
      { model: "m", messages: [{ role: "user", content: "hi" }], seed: 7 },
      { requireParameters: true },
    ),
    (err: unknown) => {
      assert.ok(err instanceof APIError);
      assert.equal(err.typeSuffix, "unknown-parameter");
      return true;
    },
  );
  assert.equal(
    stub.lastFor("/v1/chat/completions")?.headers.get("X-Prometheus-Require-Parameters"),
    "true",
  );
});

test("a tokenProvider discards environment credentials rather than colliding with them", () => {
  // The governed shape on any machine where ops exported AXONIUM_CLIENT_SECRET. Refusing here would
  // make the multi-tenant mode unusable on exactly the hosts it is for -- and the reading also makes the
  // guarantee a fact rather than a claim: with a provider, this SDK holds no long-lived secret whatever
  // the environment contains. Found by running verify.sh in a shell with a `.env` sourced.
  const restore = { ...process.env };
  try {
    process.env["AXONIUM_CLIENT_ID"] = "from-the-environment";
    process.env["AXONIUM_CLIENT_SECRET"] = "also-from-the-environment";
    const config = resolveConfig({
      gatewayBaseURL: "https://gw.test",
      tokenProvider: { token: async () => "t", refresh: async () => "u" },
    });
    assert.equal(config.clientSecret, "", "an environment secret reached the resolved config");
    assert.ok(config.tokenProvider);
  } finally {
    process.env = restore;
  }
});

test("a tokenProvider passed alongside explicit credentials is still refused", () => {
  // Passing both by name is a belief about which wins, and it would be wrong half the time.
  assert.throws(
    () =>
      resolveConfig({
        gatewayBaseURL: "https://gw.test",
        clientId: "id",
        clientSecret: "secret",
        tokenProvider: { token: async () => "t", refresh: async () => "u" },
      }),
    /different modes/,
  );
});

test("top_logprobs without logprobs is refused at the call site", async () => {
  // The contract case PRM-187 introduced. The rule is the ENGINE's -- llama.cpp answers
  // "top_logprobs requires logprobs to be set to true" -- and the gateway enforces it before
  // forwarding, so the refusal would arrive as problem+json. Refusing here is the difference
  // between learning it at the call site and learning it after a round trip, and no recorded
  // corpus case covers it, so this is the only thing holding the rule in this SDK.
  const stub = new Stub().token().on(CHAT, { body: JSON.stringify({ ok: 1 }) });
  const client = new Axonium({
    gatewayBaseURL: "https://gw.test",
    clientId: "i",
    clientSecret: "s",
    fetch: stub.fetch,
  });
  const messages = [{ role: "user" as const, content: "x" }];

  await assert.rejects(
    client.chat.completions.create({ model: "m", messages, top_logprobs: 3 }),
    /top_logprobs requires logprobs/,
  );
  // `logprobs: false` is present and wrong -- an SDK checking only for ABSENCE would send it.
  await assert.rejects(
    client.chat.completions.create({ model: "m", messages, logprobs: false, top_logprobs: 3 }),
    /top_logprobs requires logprobs/,
  );
  assert.equal(stub.countFor(CHAT), 0, "a refused request still reached the gateway");

  // The asymmetry is the point: logprobs on its own is a complete request.
  await client.chat.completions.create({ model: "m", messages, logprobs: true });
  assert.equal(stub.countFor(CHAT), 1);
});

test("probability is exp of the logprob, and absent rather than zero", async () => {
  // -0.00054 is ~99.95%, not ~0. Read as a probability it looks like a number near zero meaning
  // "unlikely", and nothing about the mistake is loud.
  const body = JSON.stringify({
    choices: [
      {
        message: { role: "assistant", content: "yes" },
        logprobs: {
          content: [
            { token: "yes", logprob: -0.00054, top_logprobs: [{ token: "no", logprob: -7.6 }] },
            { token: "!" },
          ],
        },
      },
    ],
  });
  const stub = new Stub().token().on(CHAT, { body });
  const client = new Axonium({
    gatewayBaseURL: "https://gw.test",
    clientId: "i",
    clientSecret: "s",
    fetch: stub.fetch,
  });

  const completion = await client.chat.completions.create({
    model: "m",
    messages: [{ role: "user", content: "x" }],
    logprobs: true,
  });
  const tokens = completion.logprobs ?? [];
  assert.equal(tokens.length, 2);
  assert.ok(tokens[0]!.probability! > 0.999, "read the natural log as a probability");
  assert.ok(tokens[0]!.topLogprobs[0]!.probability! < 0.001);
  // A token the backend said nothing about is not a token it said was impossible.
  assert.equal(tokens[1]!.probability, undefined);
});

test("a response with no logprobs key says nothing rather than saying empty", async () => {
  // An engine without the feature simply omits it. `undefined` is that absence; `[]` would claim
  // the engine answered with no tokens, which is a different statement.
  const stub = new Stub().token().on(CHAT, { body: JSON.stringify({ choices: [{}] }) });
  const client = new Axonium({
    gatewayBaseURL: "https://gw.test",
    clientId: "i",
    clientSecret: "s",
    fetch: stub.fetch,
  });
  const completion = await client.chat.completions.create({
    model: "m",
    messages: [{ role: "user", content: "x" }],
  });
  assert.equal(completion.logprobs, undefined);
});

test("credentials are the only setting a caller must supply", () => {
  // The shape Python, Go and Rust have had since AXO-83 collapsed auth onto the gateway, and that
  // this SDK did not: 0.1.0 and 0.2.0 both threw `Missing gatewayBaseURL`, on the argument that a
  // wrong gateway is worse than a missing one -- an argument the other three had already answered
  // and this one had not read. The published documentation's own "smallest thing that works"
  // example threw against the published package.
  const config = resolveConfig({ clientId: "i", clientSecret: "s" });
  assert.equal(config.gatewayBaseURL, DEFAULT_GATEWAY_BASE_URL);

  // The answer to that argument, and the reason defaulting is safe rather than reckless: a wrong
  // default reaches your own machine -- normally a refused connection -- and cannot quietly send a
  // credential somewhere real.
  assert.ok(
    DEFAULT_GATEWAY_BASE_URL.startsWith("http://127.0.0.1"),
    "the default must stay a loopback address",
  );

  // Explicit still wins, and an empty string is a mistake with an answer rather than an omission.
  assert.equal(
    resolveConfig({ clientId: "i", clientSecret: "s", gatewayBaseURL: "https://explicit.test" })
      .gatewayBaseURL,
    "https://explicit.test",
  );
});
