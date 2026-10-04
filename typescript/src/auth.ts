import { ConfigurationError, TransportError, errorFromOAuth, errorFromProblem } from "./errors.ts";
import type { ResolvedConfig, TokenProvider } from "./config.ts";
import { USER_AGENT } from "./version.ts";

/** A token and what is known about it. `scope` is what was **granted**, never what was asked for. */
export interface TokenSet {
  readonly accessToken: string;
  /** `performance.now()` reading at which this stops being usable. */
  readonly expiresAt: number;
  readonly expiresIn: number;
  readonly scope: readonly string[];
}

/** The claims this SDK reads out of an access token. Everything else stays in {@link TokenClaims.raw}. */
export interface TokenClaims {
  /**
   * The principal, which **is** the `client_id` — the thing model grants and every usage row are
   * keyed to.
   *
   * Read from `sub`, as the guide documents and as the other four SDKs in this family do. The first
   * version of this read a `client_id` claim, which **no token carries**: the accessor returned
   * `undefined` for every real token and looked like a gateway that had not sent it. Found by
   * printing the claims of a live token, not by any test, which is why there is now a test asserting
   * the names.
   */
  readonly subject: string | undefined;
  /** `azp`, the authorised party. The same value as {@link subject} on a client-credentials token. */
  readonly authorizedParty: string | undefined;
  readonly scope: readonly string[];
  readonly expiresAt: number | undefined;
  readonly issuedAt: number | undefined;
  readonly raw: Record<string, unknown>;
}

/** Refresh once this much of the token's life has gone. */
const REFRESH_AHEAD_RATIO = 0.8;
/** …or this little is left, whichever comes first. */
const REFRESH_AHEAD_MIN_SECONDS = 30;
/**
 * A ceiling on any advertised lifetime.
 *
 * An `expires_in` of a year is a misconfiguration rather than a gift, and honouring it would mean
 * holding a token that the gateway has long since forgotten, failing every request until the process
 * restarts.
 */
const MAX_PLAUSIBLE_TTL_SECONDS = 86_400;

/**
 * Decodes a JWT's payload without verifying it.
 *
 * **Not verification, and it must not be used as such.** The signature is the gateway's to check; this
 * reads the claims so a caller can see which `client_id` and scopes they are operating as, and so the
 * token's own `exp` can be used as a second reading of its lifetime.
 *
 * Done by hand because the alternative is a dependency, and because all that is needed is one
 * base64url segment. A malformed token yields empty claims rather than throwing: the gateway decides
 * whether a token is good, and a decode failure here must not become the reason a request never left.
 */
export function decodeClaims(accessToken: string): TokenClaims {
  const empty: TokenClaims = {
    subject: undefined,
    authorizedParty: undefined,
    scope: [],
    expiresAt: undefined,
    issuedAt: undefined,
    raw: {},
  };

  const parts = accessToken.split(".");
  if (parts.length !== 3) return empty;
  const payload = parts[1];
  if (!payload) return empty;

  let json: string;
  try {
    const padded = payload.replace(/-/g, "+").replace(/_/g, "/");
    const binary = atob(padded + "=".repeat((4 - (padded.length % 4)) % 4));
    // atob gives latin1; a claim with a non-ASCII character would otherwise come back mangled.
    json = new TextDecoder().decode(Uint8Array.from(binary, (c) => c.charCodeAt(0)));
  } catch {
    return empty;
  }

  let raw: Record<string, unknown>;
  try {
    const parsed: unknown = JSON.parse(json);
    if (typeof parsed !== "object" || parsed === null) return empty;
    raw = parsed as Record<string, unknown>;
  } catch {
    return empty;
  }

  const scope = raw["scope"];
  const text = (key: string): string | undefined =>
    typeof raw[key] === "string" ? (raw[key] as string) : undefined;
  return {
    subject: text("sub"),
    authorizedParty: text("azp"),
    scope: typeof scope === "string" ? scope.split(" ").filter(Boolean) : [],
    expiresAt: typeof raw["exp"] === "number" ? raw["exp"] : undefined,
    issuedAt: typeof raw["iat"] === "number" ? raw["iat"] : undefined,
    raw,
  };
}

/**
 * How long a token is really good for, in seconds.
 *
 * `expires_in` is the server's own answer and is normally used as-is. When the response's `Date`
 * header and the token's own `exp` are both present, their difference is a **second, independent
 * reading of the same lifetime** — and a skew-free one, because both come from the server's clock
 * rather than being compared against ours. The platform confirmed this is the right anchor.
 *
 * The shorter wins. Disagreement should not happen, and treating a token as expiring sooner only
 * costs an early refresh, while treating it as living longer costs a request.
 */
function effectiveLifetime(headers: Headers, accessToken: string, expiresIn: number): number {
  const date = headers.get("Date");
  const exp = decodeClaims(accessToken).expiresAt;
  if (!date || exp === undefined) return expiresIn;

  const serverNow = Date.parse(date);
  if (Number.isNaN(serverNow)) return expiresIn;

  const serverRemaining = exp - serverNow / 1000;
  // Already expired by the server's own reckoning: fail fast rather than spend a request finding out.
  if (serverRemaining <= 0) return 0;
  return Math.min(expiresIn, serverRemaining);
}

/**
 * Holds the credential and hands out tokens.
 *
 * **Concurrent callers share one in-flight refresh.** A promise is kept rather than a lock, which is
 * the JavaScript shape of double-checked locking: the second caller awaits the first one's request
 * instead of starting its own. Without it, a cold client answering ten simultaneous requests would
 * send ten token requests, and nine of them would be charged against the rate-limit budget for
 * nothing.
 */
export class TokenManager {
  private readonly config: ResolvedConfig;
  private readonly provider: TokenProvider | undefined;
  private current: TokenSet | undefined;
  private inFlight: Promise<TokenSet> | undefined;
  /** The last token a provider gave us, so a 401 can tell the provider *which* one was rejected. */
  private lastProvided: string | undefined;

  constructor(config: ResolvedConfig) {
    this.config = config;
    this.provider = config.tokenProvider;
  }

  /** The claims of the token currently held, or `undefined` before the first one is obtained. */
  get claims(): TokenClaims | undefined {
    const token = this.current?.accessToken ?? this.lastProvided;
    return token === undefined ? undefined : decodeClaims(token);
  }

  /** The scope the gateway granted, which may be narrower than the one requested. */
  get grantedScope(): readonly string[] {
    return this.current?.scope ?? this.claims?.scope ?? [];
  }

  /** A token to send now, fetching or refreshing if the one held is spent. */
  async token(): Promise<string> {
    if (this.provider) {
      const token = await this.provider.token();
      this.lastProvided = token;
      return token;
    }
    if (this.current && !this.needsRefresh(this.current)) return this.current.accessToken;
    return (await this.obtain()).accessToken;
  }

  /**
   * Discards the token the gateway just rejected and gets another, exactly once per rejection.
   *
   * Compares against what was rejected rather than refreshing unconditionally: when several
   * concurrent requests are all rejected with the same stale token, the first one's replacement
   * serves them all, and the rest must not each throw away a token that is already good.
   */
  async refreshAfterRejection(rejected: string): Promise<string> {
    if (this.provider) {
      const token = await this.provider.refresh(rejected);
      if (token === rejected) {
        throw new ConfigurationError(
          "The tokenProvider returned the same token the gateway had just rejected, so retrying " +
            "would fail identically. refresh() must obtain a new one.",
        );
      }
      this.lastProvided = token;
      return token;
    }
    if (this.current && this.current.accessToken !== rejected) {
      return this.current.accessToken;
    }
    this.current = undefined;
    return (await this.obtain()).accessToken;
  }

  private needsRefresh(token: TokenSet): boolean {
    const remaining = (token.expiresAt - performance.now()) / 1000;
    return (
      remaining <= Math.max(token.expiresIn * (1 - REFRESH_AHEAD_RATIO), 0) ||
      remaining <= REFRESH_AHEAD_MIN_SECONDS
    );
  }

  private async obtain(): Promise<TokenSet> {
    // The second caller joins the first one's request instead of starting another.
    if (this.inFlight) return this.inFlight;
    const attempt = this.fetchToken()
      .then((token) => {
        this.current = token;
        return token;
      })
      .finally(() => {
        this.inFlight = undefined;
      });
    this.inFlight = attempt;
    return attempt;
  }

  private async fetchToken(): Promise<TokenSet> {
    const body = new URLSearchParams({
      grant_type: "client_credentials",
      client_id: this.config.clientId,
      client_secret: this.config.clientSecret,
    });
    // Omitted rather than sent empty: an empty `scope` is a request for no scopes at all, while
    // omitting it asks for everything the account holds.
    if (this.config.scope) body.set("scope", this.config.scope);

    // Captured before the request so the round trip is charged against the token's life rather than
    // granted as extra margin. performance.now() rather than Date.now() so a system clock adjustment
    // mid-flight cannot move the deadline.
    const issuedAt = performance.now();

    const signal = AbortSignal.timeout(this.config.timeouts.connect + this.config.timeouts.request);
    let response: Response;
    try {
      response = await this.config.fetch(`${this.config.gatewayBaseURL}/oauth2/token`, {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          Accept: "application/json",
          "User-Agent": USER_AGENT,
        },
        body,
        signal,
      });
    } catch (cause) {
      throw new TransportError(
        `Could not reach the token endpoint at ${this.config.gatewayBaseURL}/oauth2/token.`,
        { cause },
      );
    }

    const text = await response.text();
    let parsed: unknown;
    try {
      parsed = text ? JSON.parse(text) : undefined;
    } catch {
      parsed = undefined;
    }
    const payload = (typeof parsed === "object" && parsed !== null ? parsed : {}) as Record<
      string,
      unknown
    >;

    if (!response.ok) throw tokenError(response, payload, text);

    const accessToken = payload["access_token"];
    const expiresIn = payload["expires_in"];
    if (typeof accessToken !== "string" || !accessToken) {
      throw new TransportError("The token response carried no access_token.");
    }
    if (typeof expiresIn !== "number" || !(expiresIn > 0)) {
      throw new TransportError(
        `The auth-service returned an unusable expires_in: ${JSON.stringify(expiresIn)}.`,
      );
    }

    // The GRANTED scope, never the requested one. Asking for a subset is honoured, so what came back
    // is what this token can actually do -- and a caller that assumed otherwise would diagnose a 403
    // as a platform fault.
    const granted = payload["scope"];
    const scope = typeof granted === "string" ? granted.split(" ").filter(Boolean) : [];

    const lifetime = Math.min(
      effectiveLifetime(response.headers, accessToken, expiresIn),
      MAX_PLAUSIBLE_TTL_SECONDS,
    );

    return {
      accessToken,
      expiresAt: issuedAt + lifetime * 1000,
      expiresIn: lifetime,
      scope,
    };
  }
}

/**
 * Types a failed token response, which arrives in either of two envelopes.
 *
 * A `4xx` is an RFC 6749 outcome — wrong credentials, a scope the account does not hold — and is
 * never worth retrying. A `5xx` is the gateway failing to do its job and comes back as problem+json,
 * so it maps into the gateway taxonomy where `upstream-unavailable` is retryable.
 */
function tokenError(response: Response, payload: Record<string, unknown>, text: string): Error {
  if (response.status >= 500) {
    const type = typeof payload["type"] === "string" ? payload["type"] : "";
    return errorFromProblem({
      status: response.status,
      typeSuffix: type.split("/").pop() ?? "",
      title: typeof payload["title"] === "string" ? payload["title"] : "",
      detail:
        typeof payload["detail"] === "string"
          ? payload["detail"]
          : `The token endpoint answered ${response.status}.`,
      instance: typeof payload["instance"] === "string" ? payload["instance"] : "",
      retryAfter: undefined,
      meta: {
        // Body first, then the headers. Same fallback as every other error path, and load-bearing for
        // the same reason: these envelopes carry the ids in the body and some non-gateway answers
        // carry them only in the headers.
        requestId:
          (typeof payload["request_id"] === "string" ? payload["request_id"] : undefined) ??
          response.headers.get("X-Request-ID") ??
          undefined,
        traceId:
          (typeof payload["trace_id"] === "string" ? payload["trace_id"] : undefined) ??
          response.headers.get("X-Trace-ID") ??
          undefined,
        instance: undefined,
        instanceId: undefined,
        idempotentReplay: false,
        idempotentReplayOf: undefined,
        rateLimit: undefined,
        ignoredParameters: undefined,
      },
      raw: payload,
    });
  }

  const code = typeof payload["error"] === "string" ? payload["error"] : "";
  if (!code) {
    // A 4xx that is not RFC 6749 at all. Said plainly, because the usual cause is that something
    // other than the gateway answered -- a proxy, a login page -- and the status alone misleads.
    return new TransportError(
      `The token endpoint answered ${response.status} without an RFC 6749 error code. ` +
        `Body: ${text.slice(0, 200)}`,
    );
  }
  const description =
    typeof payload["error_description"] === "string" ? payload["error_description"] : "";
  return errorFromOAuth(response.status, code, description);
}
