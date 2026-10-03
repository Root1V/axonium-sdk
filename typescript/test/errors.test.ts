import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  APIError,
  AxoniumError,
  CLAIMED_OAUTH_CODES,
  CLAIMED_SUFFIXES,
  OAuthError,
  PredictBackendRejectedError,
  ServerError,
  errorFromOAuth,
  errorFromProblem,
  type ProblemDetails,
} from "../src/errors.ts";

/**
 * The shared catalogue, read from the repository rather than copied.
 *
 * This file is the source of truth for the taxonomy in every SDK of this family. Reading it means a
 * suffix cannot be claimed here and absent there, in either direction, which is the one check that
 * would have caught several defects in the other four.
 */
interface Catalogue {
  gateway_errors: Array<{
    status: number | string;
    suffix: string;
    retryable: boolean | string;
    retryable_statuses?: number[];
    probe_statuses?: number[];
  }>;
  oauth_errors: Array<{ status: number; error: string; retryable: boolean }>;
}

const catalogue: Catalogue = JSON.parse(
  readFileSync(new URL("../../spec/errors.json", import.meta.url), "utf8"),
) as Catalogue;

function problem(overrides: Partial<ProblemDetails> = {}): ProblemDetails {
  return {
    status: 400,
    typeSuffix: "unknown-model",
    title: "Bad Request",
    detail: "something",
    instance: "/v1/chat/completions",
    retryAfter: undefined,
    meta: {
      requestId: undefined,
      traceId: undefined,
      instance: undefined,
      instanceId: undefined,
      idempotentReplay: false,
      idempotentReplayOf: undefined,
      rateLimit: undefined,
    },
    raw: {},
    ...overrides,
  };
}

test("the catalogue is not empty, so the assertions below are about something", () => {
  // Guard the instrument first. Every test here passes vacuously against an empty file, which is
  // how a parity suite reports agreement about nothing.
  assert.ok(catalogue.gateway_errors.length >= 30, `only ${catalogue.gateway_errors.length} rows`);
  assert.ok(catalogue.oauth_errors.length >= 4);
});

test("every catalogued suffix has a class, with the catalogued retryability", () => {
  const missing: string[] = [];
  const wrong: string[] = [];

  for (const row of catalogue.gateway_errors) {
    const built = errorFromProblem(
      problem({
        typeSuffix: row.suffix,
        // The one row whose status is the engine's rather than a number. Probed separately below;
        // here it takes a status its own class will call retryable, so the comparison is meaningful.
        status: typeof row.status === "number" ? row.status : 429,
      }),
    );

    if (built.constructor === APIError || built.constructor === ServerError) {
      missing.push(row.suffix);
      continue;
    }
    const expected = row.retryable === "by_status" ? true : row.retryable === true;
    if (built.retryable !== expected) {
      wrong.push(`${row.suffix}: class says ${built.retryable}, catalogue says ${row.retryable}`);
    }
  }

  assert.deepEqual(missing, [], `catalogued suffixes with no class: ${missing.join(", ")}`);
  assert.deepEqual(wrong, [], wrong.join("\n"));
});

test("no claimed suffix is absent from the catalogue", () => {
  // The direction that matters more, and the one nothing else would tell anybody: a suffix this SDK
  // invented sends a caller to catch a case that never arrives, and their `catch` block is simply
  // dead. It cannot be found by reading this package alone.
  const catalogued = new Set(catalogue.gateway_errors.map((row) => row.suffix));
  const invented = CLAIMED_SUFFIXES.filter((suffix) => !catalogued.has(suffix));
  assert.deepEqual(
    invented,
    [],
    `claimed here, absent from spec/errors.json: ${invented.join(", ")}`,
  );
});

test("the claimed suffixes are read off the classes, and there are some", () => {
  // Without this, a refactor that emptied the list would make the test above pass by having nothing
  // to check -- which is the shape of defect this repository keeps finding inside its own checks.
  assert.ok(CLAIMED_SUFFIXES.length >= 30, `only ${CLAIMED_SUFFIXES.length} claimed`);
  assert.equal(
    new Set(CLAIMED_SUFFIXES).size,
    CLAIMED_SUFFIXES.length,
    "a suffix is claimed twice",
  );
});

test("every catalogued OAuth2 code has a class, and none is invented", () => {
  const catalogued = new Set(catalogue.oauth_errors.map((row) => row.error));
  for (const row of catalogue.oauth_errors) {
    const built = errorFromOAuth(row.status, row.error, "because");
    assert.notEqual(built.constructor, OAuthError, `${row.error} falls back to the base class`);
    assert.equal(built.retryable, row.retryable, `${row.error} retryability`);
  }
  const invented = CLAIMED_OAUTH_CODES.filter((code) => !catalogued.has(code));
  assert.deepEqual(invented, []);
});

test("an unknown suffix falls back by status rather than throwing", () => {
  // The catalogue grows. An error documented on a Tuesday must not become a parse failure in a
  // version already published, so the caller still gets the suffix, the detail and the ids.
  const client = errorFromProblem(problem({ typeSuffix: "invented-last-tuesday", status: 400 }));
  assert.equal(client.constructor, APIError);
  assert.equal(client.typeSuffix, "invented-last-tuesday");
  assert.equal(client.retryable, false);

  const server = errorFromProblem(problem({ typeSuffix: "also-new", status: 503 }));
  assert.equal(server.constructor, ServerError);
  assert.equal(server.retryable, true, "a 5xx with no name is worth retrying");
});

test("predict-backend-rejected decides retryability from the engine's status, not its name", () => {
  // The only row in the catalogue whose status is "4xx" rather than a number: the engine's status is
  // kept, so one suffix covers both a 422 that will never succeed and a 429 that will.
  const row = catalogue.gateway_errors.find((r) => r.suffix === "predict-backend-rejected");
  assert.ok(row, "the catalogue no longer has the row this test is about");
  assert.equal(row.retryable, "by_status");
  assert.deepEqual(row.retryable_statuses, [429]);

  for (const status of row.probe_statuses ?? []) {
    const built = errorFromProblem(problem({ typeSuffix: row.suffix, status }));
    assert.ok(built instanceof PredictBackendRejectedError);
    assert.equal(
      built.retryable,
      status === 429,
      `status ${status} should be ${status === 429 ? "" : "non-"}retryable`,
    );
  }
});

test("an OAuth failure is not catchable as an APIError", () => {
  // Deliberate: the shapes differ and so do the remedies. An inference failure may be worth
  // retrying with the same credential; a credential failure never is, so a `catch (e instanceof
  // APIError)` that swallowed it would retry forever against a secret that will never work.
  const oauth = errorFromOAuth(401, "invalid_client", "bad secret");
  assert.ok(oauth instanceof AxoniumError, "but it is still one AxoniumError for a single catch");
  assert.ok(!(oauth instanceof APIError));
});

test("the message carries the ids a caller needs to report a failure", () => {
  const built = errorFromProblem(
    problem({
      detail: "Model 'nope' is not registered.",
      meta: { ...problem().meta, requestId: "req-1", traceId: "trace-1" },
    }),
  );
  assert.match(built.message, /not registered/);
  assert.match(built.message, /request_id=req-1/);
  assert.match(built.message, /trace_id=trace-1/);
});

test("the subclasses keep their name through a prototype rebuild", () => {
  // `Object.setPrototypeOf` in the base constructor is what makes this hold once the package is
  // consumed from the CommonJS build, where the chain is rebuilt and `name` otherwise reports
  // "Error" in every stack trace a user pastes into a bug report.
  const built = errorFromProblem(problem({ typeSuffix: "forbidden", status: 403 }));
  assert.equal(built.name, "ForbiddenError");
  assert.ok(built.stack?.startsWith("ForbiddenError"));
});
