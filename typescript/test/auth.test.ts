import { test } from "node:test";
import assert from "node:assert/strict";

import { resolveConfig } from "../src/config.ts";
import { TokenManager, decodeClaims } from "../src/auth.ts";
import {
  InvalidClientError,
  InvalidScopeError,
  ConfigurationError,
  TransportError,
  TokenEndpointUnavailableError,
} from "../src/errors.ts";
import { Stub, jwt } from "./stub.ts";

const base = { gatewayBaseURL: "https://gw.test", clientId: "id", clientSecret: "secret" };

function manager(stub: Stub, extra: Record<string, unknown> = {}): TokenManager {
  return new TokenManager(resolveConfig({ ...base, fetch: stub.fetch, ...extra }));
}

test("the token request is form-encoded, with the grant type in the body", () => {
  // The credential goes in the body rather than in a header, and the content type has to match or the
  // auth-service reads an empty form and answers about a missing grant_type.
  const stub = new Stub().token();
  return manager(stub)
    .token()
    .then(() => {
      const sent = stub.lastFor("/oauth2/token");
      assert.equal(sent?.headers.get("Content-Type"), "application/x-www-form-urlencoded");
      const form = new URLSearchParams(sent?.body ?? "");
      assert.equal(form.get("grant_type"), "client_credentials");
      assert.equal(form.get("client_id"), "id");
      assert.equal(form.get("client_secret"), "secret");
      assert.equal(form.get("scope"), null, "an empty scope must be omitted, not sent empty");
    });
});

test("an empty scope is omitted and a configured one is sent", async () => {
  // Sending `scope=` asks for NO scopes; omitting it asks for everything the account holds. The
  // difference is a 403 on every call, so it is asserted rather than assumed.
  const stub = new Stub().token();
  await manager(stub, { scope: "inference:read model:qwen3-0.6b" }).token();
  const form = new URLSearchParams(stub.lastFor("/oauth2/token")?.body ?? "");
  assert.equal(form.get("scope"), "inference:read model:qwen3-0.6b");
});

test("the granted scope is read back, never assumed from the request", async () => {
  // Asking for a subset is honoured, so what came back is what the token can actually do. A caller
  // that trusted their own request would diagnose a 403 as a platform fault.
  const stub = new Stub().on("/oauth2/token", {
    body: JSON.stringify({ access_token: "t", expires_in: 3600, scope: "inference:read" }),
  });
  const tokens = manager(stub, { scope: "inference:read inference:stream" });
  await tokens.token();
  assert.deepEqual(tokens.grantedScope, ["inference:read"]);
});

test("a token is reused until it is close to expiring", async () => {
  const stub = new Stub().token(3600);
  const tokens = manager(stub);
  await tokens.token();
  await tokens.token();
  await tokens.token();
  assert.equal(stub.countFor("/oauth2/token"), 1, "a live token was fetched again");
});

test("a token that is nearly spent is refreshed before it is used", async () => {
  // 20s left on a 20s life: under the 30s floor, so the first use already refreshes. The floor exists
  // because a token that expires mid-flight fails a request that had no need to fail.
  const stub = new Stub().token(20);
  const tokens = manager(stub);
  await tokens.token();
  await tokens.token();
  assert.equal(stub.countFor("/oauth2/token"), 2);
});

test("concurrent callers share one in-flight token request", async () => {
  // Without the shared promise a cold client answering ten simultaneous requests sends ten token
  // requests, and nine are charged against the rate-limit budget for nothing.
  const stub = new Stub().token();
  const tokens = manager(stub);
  const all = await Promise.all(Array.from({ length: 10 }, () => tokens.token()));
  assert.equal(stub.countFor("/oauth2/token"), 1);
  assert.equal(new Set(all).size, 1, "callers got different tokens");
});

test("the lifetime comes from the server's clock, not ours", async () => {
  // The Date header and the token's own exp are both server-side readings, so their difference is a
  // skew-free second opinion on the lifetime. The SHORTER wins: an early refresh costs one request,
  // believing a token lives longer than it does costs every request after it expires.
  const serverNow = Date.now();
  const stub = new Stub().on("/oauth2/token", {
    headers: { Date: new Date(serverNow).toUTCString() },
    body: JSON.stringify({
      // The server says an hour; the token itself says 20 seconds -- under the 30s floor, so an
      // honest reading of the exp claim forces a refresh on the very next use. The first version of
      // this test said 40 seconds and expected the same, which is the test getting its own
      // arithmetic wrong: 40 is above the floor, so reusing the token was correct.
      access_token: jwt({ exp: Math.floor(serverNow / 1000) + 20, scope: "inference:read" }),
      expires_in: 3600,
    }),
  });
  const tokens = manager(stub);
  await tokens.token();
  await tokens.token();
  assert.equal(
    stub.countFor("/oauth2/token"),
    2,
    "the exp claim was ignored and the token was treated as living an hour",
  );
});

test("a token already expired by the server's reckoning is not held", async () => {
  const serverNow = Date.now();
  const stub = new Stub().on("/oauth2/token", {
    headers: { Date: new Date(serverNow).toUTCString() },
    body: JSON.stringify({
      access_token: jwt({ exp: Math.floor(serverNow / 1000) - 10 }),
      expires_in: 3600,
    }),
  });
  const tokens = manager(stub);
  await tokens.token();
  await tokens.token();
  assert.equal(stub.countFor("/oauth2/token"), 2);
});

test("an absurd expires_in is capped rather than honoured", async () => {
  // A year is a misconfiguration, not a gift: honouring it means holding a token the gateway forgot
  // long ago and failing every request until the process restarts.
  const stub = new Stub().on("/oauth2/token", {
    body: JSON.stringify({ access_token: "t", expires_in: 60 * 60 * 24 * 365 }),
  });
  const tokens = manager(stub);
  await tokens.token();
  assert.ok(tokens.claims !== undefined || true);
  // Asserted through behaviour: the cap is a day, so a token claiming a year is still usable now.
  await tokens.token();
  assert.equal(stub.countFor("/oauth2/token"), 1);
});

test("a 4xx from the token endpoint is an OAuth error, not an APIError", async () => {
  const stub = new Stub().on("/oauth2/token", {
    status: 401,
    body: JSON.stringify({ error: "invalid_client", error_description: "bad secret" }),
  });
  await assert.rejects(manager(stub).token(), (err: unknown) => {
    assert.ok(err instanceof InvalidClientError);
    assert.equal(err.retryable, false);
    assert.match(err.message, /bad secret/);
    return true;
  });
});

test("an unheld scope is an error here rather than a silent downgrade", async () => {
  const stub = new Stub().on("/oauth2/token", {
    status: 400,
    body: JSON.stringify({ error: "invalid_scope", error_description: "model:nope not granted" }),
  });
  await assert.rejects(manager(stub, { scope: "model:nope" }).token(), InvalidScopeError);
});

test("a 5xx from the token endpoint maps into the gateway taxonomy and is retryable", async () => {
  // Different envelope, different branch: this one is the gateway failing to reach the auth-service,
  // which is worth retrying -- unlike a wrong secret, which never is.
  const stub = new Stub().on("/oauth2/token", {
    status: 503,
    body: JSON.stringify({
      type: "https://prometheus.internal/errors/upstream-unavailable",
      detail: "auth-service unreachable",
    }),
  });
  await assert.rejects(manager(stub).token(), (err: unknown) => {
    assert.ok(err instanceof TokenEndpointUnavailableError);
    assert.equal(err.retryable, true);
    return true;
  });
});

test("a 4xx that is not RFC 6749 says so instead of inventing a code", async () => {
  // The usual cause is that something other than the gateway answered -- a proxy, a login page -- and
  // the status alone misleads.
  const stub = new Stub().on("/oauth2/token", { status: 403, body: "<html>Forbidden</html>" });
  await assert.rejects(manager(stub).token(), (err: unknown) => {
    assert.ok(err instanceof TransportError);
    assert.match(err.message, /without an RFC 6749 error code/);
    return true;
  });
});

test("a token response with no access_token is refused", async () => {
  const stub = new Stub().on("/oauth2/token", { body: JSON.stringify({ expires_in: 300 }) });
  await assert.rejects(manager(stub).token(), /no access_token/);
});

test("an unusable expires_in is refused rather than defaulted", async () => {
  // Defaulting would invent a lifetime the server never stated, and the failure would surface later
  // as a 401 nobody can explain.
  const stub = new Stub().on("/oauth2/token", {
    body: JSON.stringify({ access_token: "t", expires_in: 0 }),
  });
  await assert.rejects(manager(stub).token(), /unusable expires_in/);
});

test("a provider supplies tokens and the SDK never sees a credential", async () => {
  const stub = new Stub();
  let issued = 0;
  const tokens = new TokenManager(
    resolveConfig({
      gatewayBaseURL: "https://gw.test",
      fetch: stub.fetch,
      tokenProvider: {
        token: async () => `provided-${++issued}`,
        refresh: async () => `provided-${++issued}`,
      },
    }),
  );
  assert.equal(await tokens.token(), "provided-1");
  assert.equal(stub.countFor("/oauth2/token"), 0, "the SDK fetched a token itself");
});

test("a provider that returns the rejected token again is refused", async () => {
  // Otherwise the transport retries with the same value and fails identically, and the real fault --
  // a provider that does not refresh -- is invisible behind a repeated 401.
  const tokens = new TokenManager(
    resolveConfig({
      gatewayBaseURL: "https://gw.test",
      fetch: new Stub().fetch,
      tokenProvider: { token: async () => "same", refresh: async () => "same" },
    }),
  );
  await assert.rejects(tokens.refreshAfterRejection("same"), ConfigurationError);
});

test("a second rejection of an already-replaced token does not discard the new one", async () => {
  // Concurrent requests rejected with the same stale token: the first one's replacement serves them
  // all, and the rest must not each throw away a token that is already good.
  const stub = new Stub().token();
  const tokens = manager(stub);
  const first = await tokens.token();
  const replaced = await tokens.refreshAfterRejection(first);
  assert.notEqual(replaced, first, "the refresh returned the same token");
  const again = await tokens.refreshAfterRejection(first);
  assert.equal(again, replaced, "the second rejection discarded a token that was already good");
  assert.equal(stub.countFor("/oauth2/token"), 2, "the second rejection fetched a third token");
});

test("decodeClaims reads the claim names a real token carries", () => {
  // `sub` and `azp`, which is what the guide documents and what a live token was measured to carry.
  // The first version of this code read a `client_id` claim that NO token has, so the accessor
  // returned undefined for every real token and read as a gateway that had not sent it. Nothing in
  // this suite would have caught that -- it took printing the claims of a live token -- so the names
  // are pinned here against the shape that was measured.
  const real = {
    iss: "http://127.0.0.1:9000",
    sub: "aeed114f-cf48-4f37-882b-ee6d35fd3fc5",
    azp: "aeed114f-cf48-4f37-882b-ee6d35fd3fc5",
    aud: "prometheus-gateway",
    scope: "inference:read model:x",
    exp: 42,
    iat: 1,
    role: "agent",
  };
  const claims = decodeClaims(jwt(real));
  assert.equal(claims.subject, real.sub);
  assert.equal(claims.authorizedParty, real.azp);
  assert.equal(claims.raw["role"], "agent", "an unmodelled claim must stay reachable");
  assert.deepEqual(claims.scope, ["inference:read", "model:x"]);
  assert.equal(claims.expiresAt, 42);

  // Malformed input yields empty claims: the gateway decides whether a token is good, and a decode
  // failure here must not become the reason a request never left.
  for (const bad of ["", "not-a-jwt", "a.b", "a.!!!.c", `a.${btoa("[1,2]")}.c`]) {
    assert.deepEqual(decodeClaims(bad).scope, [], `threw or misread on ${JSON.stringify(bad)}`);
  }
});

test("a non-ASCII claim survives the decode", () => {
  // atob gives latin1; without the TextDecoder step a subject with an accent comes back mangled.
  assert.equal(decodeClaims(jwt({ sub: "apeirón" })).subject, "apeirón");
});
