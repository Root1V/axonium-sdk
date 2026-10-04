import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { Axonium } from "../src/client.ts";
import { APIError, OAuthError, StreamInterruptedError, TransportError } from "../src/errors.ts";
import { DEFAULT_RETRY } from "../src/retry.ts";
import type { ChatStream } from "../src/sse.ts";
import { Stub, type Reply } from "./stub.ts";

/**
 * The shared contract corpus, replayed through the real client.
 *
 * Every SDK in this family replays these same cases against the same recorded bytes, so identical
 * behaviour is enforced by construction rather than by parallel hand-written suites that drift.
 *
 * **Built with the SDK's DEFAULT retry policy, deliberately.** A case asserting `expect.requests`
 * counts attempts whose denominator is that policy, and the manifest's `$request_counts` note says so
 * — a runner that disables retries to keep its error cases fast sends one request and fails the case
 * on its own configuration rather than on the SDK. The price is that one case waits out the whole
 * attempt budget, which is what not inventing a backoff signal the gateway never sent costs.
 */
const SPEC = new URL("../../spec/", import.meta.url);

interface Case {
  id: string;
  operation: string;
  request?: Record<string, unknown>;
  response?: ResponseSpec;
  responses?: ResponseSpec[];
  expect: Expectation;
}

interface ResponseSpec {
  status: number;
  headers?: Record<string, string>;
  body_file?: string;
  sse_file?: string;
}

interface Expectation {
  kind: string;
  fields?: Record<string, unknown>;
  content?: string;
  chunks?: number;
  usage?: Record<string, unknown> | null;
  tool_calls?: unknown;
  partial_content?: string;
  error?: string;
  error_type_suffix?: string | null;
  retryable?: boolean;
  has_request_id?: boolean;
  has_trace_id?: boolean;
  requests?: number;
  attempts?: number;
  request_headers?: Record<string, string>;
  request_headers_absent?: string[];
  request_headers_present?: string[];
  request_form?: Record<string, string>;
  request_form_absent?: string[];
  request_content_type?: string;
  oauth_code?: string;
}

const manifest = JSON.parse(readFileSync(new URL("cases/manifest.json", SPEC), "utf8")) as {
  version: number;
  cases: Case[];
};

const ENDPOINTS: Record<string, string> = {
  "chat.completions.create": "/v1/chat/completions",
  "chat.completions.stream": "/v1/chat/completions",
  "embeddings.create": "/v1/embeddings",
  "images.generate": "/v1/images/generations",
  "models.list": "/v1/models",
  "models.mine": "/v1/models/mine",
  "rerank.create": "/v1/rerank",
  "token.fetch": "/oauth2/token",
};

function endpointFor(testCase: Case): string {
  if (testCase.operation === "usage.retrieve") {
    return `/v1/usage/${String(testCase.request?.["request_id"] ?? "")}`;
  }
  if (testCase.operation === "predict.create") {
    return `/v1/models/${String(testCase.request?.["model"] ?? "m")}/predict`;
  }
  const path = ENDPOINTS[testCase.operation];
  assert.ok(path, `${testCase.id}: no endpoint known for ${testCase.operation}`);
  return path;
}

function repliesOf(testCase: Case): ResponseSpec[] {
  if (testCase.responses) return testCase.responses;
  assert.ok(testCase.response, `${testCase.id}: neither response nor responses`);
  return [testCase.response];
}

function toReply(spec: ResponseSpec): Reply {
  const headers = { ...spec.headers };
  if (spec.sse_file) {
    // Read as bytes and handed over verbatim. The .sse fixtures are literal wire captures, blank-line
    // separators included, and reformatting one silently changes what every SDK is tested against.
    return {
      status: spec.status,
      headers: { "Content-Type": "text/event-stream", ...headers },
      body: readFileSync(new URL(`fixtures/${spec.sse_file}`, SPEC), "utf8"),
    };
  }
  return {
    status: spec.status,
    headers: { "Content-Type": "application/json", ...headers },
    body: spec.body_file ? readFileSync(new URL(`fixtures/${spec.body_file}`, SPEC), "utf8") : "{}",
  };
}

function buildStub(testCase: Case): Stub {
  const stub = new Stub();
  const path = endpointFor(testCase);
  const replies = repliesOf(testCase).map(toReply);

  if (testCase.operation === "token.fetch") {
    stub.on("/oauth2/token", ...replies);
    // Any authenticated call drives the exchange; the failure under test is the token's, not this.
    stub.on("/v1/models", { body: JSON.stringify({ object: "list", data: [] }) });
  } else {
    stub.token();
    stub.on(path, ...replies);
  }
  return stub;
}

function client(stub: Stub, testCase?: Case): Axonium {
  // A token case states the scope to request in its `request`, and it is the thing under test: the
  // form assertion is about what this SDK put on the wire.
  const scope = testCase?.operation === "token.fetch" ? testCase.request?.["scope"] : undefined;
  return new Axonium({
    gatewayBaseURL: "https://gateway.test.invalid",
    clientId: "contract",
    clientSecret: "contract",
    ...(typeof scope === "string" ? { scope } : {}),
    fetch: stub.fetch,
    retry: DEFAULT_RETRY,
  });
}

/* --- path resolution ---------------------------------------------------------------------- */

/**
 * Walks a dotted path, integer segments indexing arrays.
 *
 * Resolved against the SDK's **own accessors** where it exposes them, and against the raw body
 * otherwise. That distinction is the whole point: `usage.cache_read_tokens` is not a field the
 * platform sends — it is one this SDK lifts out of `prompt_tokens_details.cached_tokens` — so
 * resolving it by walking the body would test the fixture rather than the SDK.
 */
function resolve(path: string, result: unknown): unknown {
  const accessor = accessorFor(path, result);
  if (accessor !== NOT_AN_ACCESSOR) return accessor;
  return walk(path, (result as { raw?: unknown }).raw ?? result);
}

const NOT_AN_ACCESSOR = Symbol("not an accessor");

function accessorFor(path: string, result: unknown): unknown {
  const r = result as Record<string, unknown>;
  switch (path) {
    case "content":
      return r["content"];
    case "model":
      return r["model"];
    case "request_id":
      return r["requestId"];
    case "request_kind":
      return r["requestKind"];
    case "interrupted":
      return r["interrupted"];
    case "termination_reason":
      return r["terminationReason"];
    case "output_format":
      return r["outputFormat"];
    case "usage.cache_read_tokens":
      return (r["usage"] as { cacheReadTokens?: number } | undefined)?.cacheReadTokens;
    case "usage.prompt_tokens":
      return (r["usage"] as { promptTokens?: number } | undefined)?.promptTokens;
    case "usage.completion_tokens":
      return (r["usage"] as { completionTokens?: number } | undefined)?.completionTokens;
    case "usage.total_tokens":
      return (r["usage"] as { totalTokens?: number } | undefined)?.totalTokens;
    default:
      break;
  }
  if (path.startsWith("meta.")) return metaPath(path.slice(5), r["meta"]);
  // Derived, like `content` and `usage.cache_read_tokens`: the body nests these under
  // choices[0].message, so walking the payload would test the fixture rather than the reassembly.
  if (path === "tool_calls" || path.startsWith("tool_calls.")) {
    return walk(path === "tool_calls" ? "" : path.slice(11), r["toolCalls"]);
  }
  if (path.startsWith("rate_limit.")) return metaPath(`rate_limit.${path.slice(11)}`, r["meta"]);
  if (path === "value" || path.startsWith("value.")) {
    return walk(path === "value" ? "" : path.slice(6), r["value"]);
  }
  // `data.N.b64_json` and the rest fall through to the raw body, which is where they live.
  return NOT_AN_ACCESSOR;
}

function metaPath(rest: string, meta: unknown): unknown {
  const camel = rest.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase());
  return walk(
    camel.replace(/\.([a-z])/g, (m) => m),
    toCamelDeep(meta),
  );
}

function toCamelDeep(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(toCamelDeep);
  if (typeof value !== "object" || value === null) return value;
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).map(([k, v]) => [
      k.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase()),
      toCamelDeep(v),
    ]),
  );
}

function walk(path: string, root: unknown): unknown {
  if (path === "") return root;
  let current: unknown = root;
  for (const segment of path.split(".")) {
    if (current === undefined || current === null) return undefined;
    if (/^\d+$/.test(segment)) {
      current = Array.isArray(current) ? current[Number(segment)] : undefined;
    } else {
      current = (current as Record<string, unknown>)[segment];
    }
  }
  return current;
}

/** Compared through JSON so the manifest's numbers and this SDK's values share one representation. */
function same(got: unknown, want: unknown): boolean {
  return JSON.stringify(got ?? null) === JSON.stringify(want ?? null);
}

/* --- invocation --------------------------------------------------------------------------- */

async function invoke(api: Axonium, testCase: Case): Promise<unknown> {
  const request = (testCase.request ?? {}) as Record<string, never>;
  switch (testCase.operation) {
    case "chat.completions.create":
      return api.chat.completions.create(request as never, optionsOf(testCase));
    case "chat.completions.stream": {
      const stream = await api.chat.completions.stream(request as never, optionsOf(testCase));
      // Opened AND iterated: an SDK that handed back a stream where a status belongs must not pass by
      // reading the refusal as an empty body.
      await stream.finalMessage();
      return stream;
    }
    case "models.list":
      return api.models.list();
    case "models.mine":
      return api.models.mine();
    case "embeddings.create":
      return api.embeddings.create(request as never, optionsOf(testCase));
    case "images.generate":
      return api.images.generate(request as never, optionsOf(testCase));
    case "rerank.create":
      return api.rerank.create(request as never, optionsOf(testCase));
    case "predict.create":
      return api.predict.create(
        String(testCase.request?.["model"] ?? ""),
        (testCase.request?.["body"] ?? {}) as Record<string, unknown>,
        optionsOf(testCase),
      );
    case "usage.retrieve":
      return api.usage.get(String(testCase.request?.["request_id"] ?? ""));
    case "token.fetch":
      // The token exchange is driven by any authenticated call; the body under test is the token's.
      return api.models.list();
    default:
      throw new Error(`${testCase.id}: unimplemented operation ${testCase.operation}`);
  }
}

/** Carried through so a case naming a key or a pin exercises the header path, not just the envelope. */
function optionsOf(testCase: Case): { idempotencyKey?: string; instance?: string } {
  const request = testCase.request ?? {};
  const options: { idempotencyKey?: string; instance?: string } = {};
  const key = request["idempotency_key"];
  if (typeof key === "string") options.idempotencyKey = key;
  const instance = request["instance"];
  if (typeof instance === "string") options.instance = instance;
  return options;
}

/* --- the cases ---------------------------------------------------------------------------- */

const byKind = (kind: string): Case[] => manifest.cases.filter((c) => c.expect.kind === kind);

function checkRequestSide(stub: Stub, testCase: Case, problems: string[]): void {
  const expect = testCase.expect;
  const path = endpointFor(testCase);
  const sent = stub.lastFor(path);

  if (expect.requests !== undefined) {
    const got = stub.countFor(path);
    if (got !== expect.requests) {
      problems.push(`${testCase.id}: ${got} requests reached ${path}, want ${expect.requests}`);
    }
  }

  for (const [name, want] of Object.entries(expect.request_headers ?? {})) {
    const got = sent?.headers.get(name);
    if (got !== want) problems.push(`${testCase.id}: header ${name} was ${got}, want ${want}`);
  }
  for (const name of expect.request_headers_absent ?? []) {
    if (sent?.headers.has(name)) {
      problems.push(`${testCase.id}: header ${name} was sent and must be absent`);
    }
  }
  // Name only, no value: an Authorization bearer is each runner's own test token, so pinning the value
  // would assert about the harness rather than about the SDK.
  for (const name of expect.request_headers_present ?? []) {
    if (!sent?.headers.has(name)) problems.push(`${testCase.id}: header ${name} was not sent`);
  }

  if (expect.request_content_type !== undefined) {
    const got = sent?.headers.get("Content-Type");
    if (got !== expect.request_content_type) {
      problems.push(`${testCase.id}: content type was ${got}, want ${expect.request_content_type}`);
    }
  }
  if (expect.request_form || expect.request_form_absent) {
    const form = new URLSearchParams(sent?.body ?? "");
    for (const [name, want] of Object.entries(expect.request_form ?? {})) {
      if (form.get(name) !== want) {
        problems.push(`${testCase.id}: form ${name} was ${form.get(name)}, want ${want}`);
      }
    }
    for (const name of expect.request_form_absent ?? []) {
      if (form.has(name)) problems.push(`${testCase.id}: form ${name} was sent and must be absent`);
    }
  }
}

test("every non-streaming success case produces the fields the manifest expects", async () => {
  const problems: string[] = [];
  const selected = byKind("ok");
  let replayed = 0;

  for (const testCase of selected) {
    if (testCase.expect.kind === "stream") continue;
    const stub = buildStub(testCase);
    try {
      const result = await invoke(client(stub, testCase), testCase);
      replayed += 1;
      for (const [path, want] of Object.entries(testCase.expect.fields ?? {})) {
        const got = resolve(path, result);
        if (!same(got, want)) {
          problems.push(
            `${testCase.id}: ${path} was ${JSON.stringify(got)}, want ${JSON.stringify(want)}`,
          );
        }
      }
      checkRequestSide(stub, testCase, problems);
    } catch (err) {
      problems.push(`${testCase.id}: threw ${String(err)}`);
    }
  }

  assert.equal(replayed, selected.length, `replayed ${replayed} of ${selected.length} ok cases`);
  assert.deepEqual(problems, [], problems.join("\n"));
});

test("every streaming case assembles to the content, chunks and usage expected", async () => {
  const problems: string[] = [];
  const selected = byKind("stream");
  let replayed = 0;

  for (const testCase of selected) {
    const stub = buildStub(testCase);
    try {
      const api = client(stub);
      const stream = await api.chat.completions.stream(
        (testCase.request ?? {}) as never,
        optionsOf(testCase),
      );
      let chunks = 0;
      for await (const _ of stream) chunks += 1;
      replayed += 1;

      const expect = testCase.expect;
      if (expect.content !== undefined && stream.content !== expect.content) {
        problems.push(
          `${testCase.id}: content was ${JSON.stringify(stream.content)}, want ${JSON.stringify(expect.content)}`,
        );
      }
      if (expect.chunks !== undefined && chunks !== expect.chunks) {
        problems.push(`${testCase.id}: ${chunks} chunks, want ${expect.chunks}`);
      }
      checkUsage(testCase, stream, problems);

      // `fields` on a streamed case had no route in any runner at first, so a stream that dropped its
      // whole meta -- ids, rate limit, replay flags -- passed every case in the corpus.
      for (const [path, want] of Object.entries(expect.fields ?? {})) {
        const got = resolve(path, stream);
        if (!same(got, want)) {
          problems.push(
            `${testCase.id}: ${path} was ${JSON.stringify(got)}, want ${JSON.stringify(want)}`,
          );
        }
      }
      if (expect.tool_calls !== undefined) {
        const got = (stream.toolCalls ?? []).map((c) => ({
          function: { name: c.function.name, arguments: c.function.arguments },
        }));
        const want = (expect.tool_calls as Array<Record<string, unknown>>).map((c) => ({
          function: {
            name: (c["function"] as Record<string, unknown>)["name"],
            arguments: (c["function"] as Record<string, unknown>)["arguments"],
          },
        }));
        if (!same(got, want)) {
          problems.push(
            `${testCase.id}: tool_calls were ${JSON.stringify(got)}, want ${JSON.stringify(want)}`,
          );
        }
      }
      checkRequestSide(stub, testCase, problems);
    } catch (err) {
      problems.push(`${testCase.id}: threw ${String(err)}`);
    }
  }

  assert.equal(
    replayed,
    selected.length,
    `replayed ${replayed} of ${selected.length} stream cases`,
  );
  assert.deepEqual(problems, [], problems.join("\n"));
});

function checkUsage(testCase: Case, stream: ChatStream, problems: string[]): void {
  const want = testCase.expect.usage;
  const got = stream.usage;
  if (want === undefined) return;
  if (want === null) {
    if (got !== undefined)
      problems.push(`${testCase.id}: usage was ${JSON.stringify(got)}, want none`);
    return;
  }
  if (!got) {
    problems.push(`${testCase.id}: no usage, want ${JSON.stringify(want)}`);
    return;
  }
  const map: Record<string, unknown> = {
    prompt_tokens: got.promptTokens,
    completion_tokens: got.completionTokens,
    total_tokens: got.totalTokens,
    cache_read_tokens: got.cacheReadTokens,
    estimated: got.estimated,
  };
  for (const [key, expected] of Object.entries(want)) {
    assert.ok(key in map, `${testCase.id}: the manifest asserts an unknown usage key ${key}`);
    if (!same(map[key], expected)) {
      problems.push(
        `${testCase.id}: usage.${key} was ${JSON.stringify(map[key])}, want ${JSON.stringify(expected)}`,
      );
    }
  }
}

test("every interrupted stream throws, carrying what had already arrived", async () => {
  const problems: string[] = [];
  const selected = byKind("stream_error");
  let replayed = 0;

  for (const testCase of selected) {
    const stub = buildStub(testCase);
    try {
      const stream = await client(stub).chat.completions.stream((testCase.request ?? {}) as never);
      await stream.finalMessage();
      problems.push(`${testCase.id}: the stream finished instead of throwing`);
    } catch (err) {
      replayed += 1;
      if (!(err instanceof StreamInterruptedError)) {
        problems.push(`${testCase.id}: threw ${String(err)}, want a StreamInterruptedError`);
        continue;
      }
      const want = testCase.expect.partial_content;
      if (want !== undefined && err.partialContent !== want) {
        problems.push(
          `${testCase.id}: partial content was ${JSON.stringify(err.partialContent)}, want ${JSON.stringify(want)}`,
        );
      }
    }
  }

  assert.equal(replayed, selected.length);
  assert.deepEqual(problems, [], problems.join("\n"));
});

test("every recorded failure maps to the taxonomy the catalogue declares", async () => {
  const problems: string[] = [];
  const selected = byKind("error");
  let replayed = 0;

  for (const testCase of selected) {
    const stub = buildStub(testCase);
    try {
      await invoke(client(stub, testCase), testCase);
      problems.push(`${testCase.id}: succeeded instead of failing`);
    } catch (err) {
      replayed += 1;
      if (!(err instanceof APIError)) {
        problems.push(`${testCase.id}: threw ${String(err)}, want an APIError`);
        continue;
      }
      const expect = testCase.expect;
      const last = repliesOf(testCase).at(-1) as ResponseSpec;
      if (err.status !== last.status) {
        problems.push(`${testCase.id}: status was ${err.status}, want ${last.status}`);
      }
      // null on purpose on one case: the body carries no `type`, so an SDK that invents one fails.
      const wantSuffix = expect.error_type_suffix ?? "";
      if (err.typeSuffix !== wantSuffix) {
        problems.push(
          `${testCase.id}: suffix was ${JSON.stringify(err.typeSuffix)}, want ${JSON.stringify(wantSuffix)}`,
        );
      }
      if (expect.retryable !== undefined && err.retryable !== expect.retryable) {
        problems.push(`${testCase.id}: retryable was ${err.retryable}, want ${expect.retryable}`);
      }
      if (
        expect.has_request_id !== undefined &&
        Boolean(err.meta.requestId) !== expect.has_request_id
      ) {
        problems.push(
          `${testCase.id}: request_id present was ${Boolean(err.meta.requestId)}, want ${expect.has_request_id}`,
        );
      }
      if (expect.has_trace_id !== undefined && Boolean(err.meta.traceId) !== expect.has_trace_id) {
        problems.push(
          `${testCase.id}: trace_id present was ${Boolean(err.meta.traceId)}, want ${expect.has_trace_id}`,
        );
      }
      for (const [path, want] of Object.entries(expect.fields ?? {})) {
        const got = resolve(path, {
          raw: err.raw,
          meta: err.meta,
          rate_limit: err.meta.rateLimit,
          request_id: err.meta.requestId,
        });
        if (!same(got, want)) {
          problems.push(
            `${testCase.id}: ${path} was ${JSON.stringify(got)}, want ${JSON.stringify(want)}`,
          );
        }
      }
      checkRequestSide(stub, testCase, problems);
    }
  }

  assert.equal(replayed, selected.length);
  assert.deepEqual(problems, [], problems.join("\n"));
});

test("every token-endpoint failure maps to its RFC 6749 code", async () => {
  const problems: string[] = [];
  const selected = [...byKind("oauth_error"), ...byKind("auth_transport_error")];
  let replayed = 0;

  for (const testCase of selected) {
    const stub = buildStub(testCase);
    try {
      await client(stub, testCase).models.list();
      problems.push(`${testCase.id}: succeeded instead of failing`);
    } catch (err) {
      replayed += 1;
      if (testCase.expect.kind === "oauth_error") {
        if (!(err instanceof OAuthError)) {
          problems.push(`${testCase.id}: threw ${String(err)}, want an OAuthError`);
          continue;
        }
        if (err.code !== testCase.expect.oauth_code) {
          problems.push(`${testCase.id}: code was ${err.code}, want ${testCase.expect.oauth_code}`);
        }
        // Deliberate: an OAuth failure must not be catchable as an APIError, because retrying with the
        // same credential can never work.
        if (err instanceof APIError)
          problems.push(`${testCase.id}: an OAuthError is also an APIError`);
      } else if (!(err instanceof TransportError) && !(err instanceof APIError)) {
        problems.push(`${testCase.id}: threw ${String(err)}, want a transport or gateway error`);
      }
    }
  }

  assert.equal(replayed, selected.length);
  assert.deepEqual(problems, [], problems.join("\n"));
});

/* --- the guard on the selections, without which every test above passes vacuously ---------- */

test("every case in the manifest is claimed by one of the suites above", () => {
  // The suites all follow the same shape: select, replay, collect, assert empty. Every one passes
  // against an empty selection. This is the guard on the selections themselves -- it names the total,
  // names who claims each case, and fails when a case is claimed by nobody. A case using a kind this
  // runner does not implement is otherwise silently skipped, looking covered while verifying nothing.
  const claimed = new Set([
    "ok",
    "stream",
    "stream_error",
    "error",
    "oauth_error",
    "auth_transport_error",
  ]);
  const unclaimed = manifest.cases
    .filter((c) => !claimed.has(c.expect.kind))
    .map((c) => `${c.id} (kind: ${c.expect.kind})`);
  assert.deepEqual(unclaimed, [], `no suite replays these: ${unclaimed.join(", ")}`);

  const operations = new Set(manifest.cases.map((c) => c.operation));
  const implemented = new Set([...Object.keys(ENDPOINTS), "usage.retrieve", "predict.create"]);
  const missing = [...operations].filter((op) => !implemented.has(op));
  assert.deepEqual(missing, [], `the manifest exercises ${missing.join(", ")}, unimplemented here`);
});

test("the corpus is the size this SDK was verified against", () => {
  // A corpus that shrinks is as much a signal as one that grows, and neither shows in a run that only
  // reports passes.
  assert.equal(manifest.version, 27, `manifest is v${manifest.version}`);
  assert.equal(manifest.cases.length, 49, `${manifest.cases.length} cases`);
});
