import {
  APIError,
  InvalidRequestError,
  TimeoutError,
  TransportError,
  errorFromProblem,
  type ProblemDetails,
  type RateLimitSnapshot,
  type ResponseMeta,
} from "./errors.ts";
import type { ResolvedConfig } from "./config.ts";
import { TokenManager } from "./auth.ts";
import {
  CooldownRegistry,
  DEFAULT_RETRY,
  delayFor,
  retryAfterMs,
  type RetryPolicy,
} from "./retry.ts";

/** The gateway accepts at most this many characters in an `Idempotency-Key`. */
export const MAX_IDEMPOTENCY_KEY_LENGTH = 255;

/** Per-call options every request method accepts. */
export interface CallOptions {
  /**
   * Makes a retry safe: a repeat with the same key and body returns the stored result without
   * reaching a model, recording usage, or counting against the spend cap.
   */
  idempotencyKey?: string;
  /** Pins the request to one replica. Diagnostic; omit to let the gateway route. */
  instance?: string;
  /** Overrides the configured timeout for this call, in ms. */
  timeout?: number;
  /** Cancels the request. Composed with the timeout rather than replacing it. */
  signal?: AbortSignal;
  /** Asks the gateway to refuse unsupported fields instead of silently dropping them. */
  requireParameters?: boolean;
}

/** What a request method gets back: the decoded body, undecoded, and the exchange's metadata. */
export interface RawResponse {
  readonly value: unknown;
  readonly meta: ResponseMeta;
  readonly status: number;
}

function readNumber(headers: Headers, name: string): number | undefined {
  const raw = headers.get(name);
  if (raw === null) return undefined;
  const value = Number(raw);
  return Number.isFinite(value) ? value : undefined;
}

/**
 * The rate-limit budget from the `X-RateLimit-*` headers.
 *
 * `undefined` when not one of them is present, which is different from a budget of zero: the headers
 * are absent on the token endpoint and on some error paths, and reporting zeros there would tell a
 * caller they are rate-limited when nobody said so.
 */
export function rateLimitFrom(headers: Headers): RateLimitSnapshot | undefined {
  const snapshot: RateLimitSnapshot = {
    scope: headers.get("X-RateLimit-Scope") ?? undefined,
    limitRequests: readNumber(headers, "X-RateLimit-Limit-Requests"),
    remainingRequests: readNumber(headers, "X-RateLimit-Remaining-Requests"),
    resetRequests: readNumber(headers, "X-RateLimit-Reset-Requests"),
    limitTokens: readNumber(headers, "X-RateLimit-Limit-Tokens"),
    remainingTokens: readNumber(headers, "X-RateLimit-Remaining-Tokens"),
    resetTokens: readNumber(headers, "X-RateLimit-Reset-Tokens"),
  };
  // `scope` is excluded from the emptiness test on purpose: it labels a budget rather than being one,
  // so a response carrying only a scope has still reported no numbers.
  const hasNumbers = Object.entries(snapshot).some(
    ([key, value]) => key !== "scope" && value !== undefined,
  );
  return hasNumbers || snapshot.scope !== undefined ? snapshot : undefined;
}

/** The correlation metadata of one response. */
/**
 * `attempts` and `waitedMs` default to "first try, waited for nothing" and are overwritten by
 * {@link Transport.send} once the retry loop knows better. They are not optional: a caller reading
 * `meta.waitedMs` to subtract deliberate sleep from its own latency must never get `undefined` and
 * silently subtract nothing.
 */
export function metaFrom(headers: Headers, attempts = 1, waitedMs = 0): ResponseMeta {
  return {
    attempts,
    waitedMs,
    requestId: headers.get("X-Request-ID") ?? undefined,
    traceId: headers.get("X-Trace-ID") ?? undefined,
    instance: headers.get("X-Prometheus-Instance") ?? undefined,
    instanceId: headers.get("X-Prometheus-Instance-Id") ?? undefined,
    idempotentReplay: headers.get("Idempotent-Replay") === "true",
    idempotentReplayOf: headers.get("X-Idempotent-Replay-Of") ?? undefined,
    rateLimit: rateLimitFrom(headers),
    ignoredParameters: ignoredParametersFrom(headers),
  };
}

/**
 * The fields the gateway dropped, from `X-Prometheus-Ignored-Parameters: logit_bias, seed`.
 *
 * `undefined` rather than `[]` when the header is absent, because the contract says the header is only
 * present when there is something to report --- so an empty array would claim the gateway looked and
 * found nothing, which is a different statement from the gateway not having said.
 */
function ignoredParametersFrom(headers: Headers): readonly string[] | undefined {
  const raw = headers.get("X-Prometheus-Ignored-Parameters");
  if (raw === null) return undefined;
  const names = raw
    .split(",")
    .map((name) => name.trim())
    .filter(Boolean);
  // A present-but-empty header is the gateway contradicting itself. Reported as an empty list rather
  // than as absence, so it reads as "said nothing" instead of "did not say".
  return names;
}

/**
 * Turns a failed response into a typed error.
 *
 * **The correlation ids are read from the body and then from the headers.** That fallback is
 * load-bearing: a body that is not a problem+json envelope at all still arrives — an HTML page from a
 * proxy that never reached the gateway — and on those the ids exist only in the headers, if at all.
 * Reading the body alone left a caller with nothing to report on exactly the failures where they need
 * it most.
 */
export async function errorFrom(response: Response): Promise<APIError> {
  const text = await response.text().catch(() => "");
  let body: Record<string, unknown> = {};
  try {
    const parsed: unknown = text ? JSON.parse(text) : undefined;
    if (typeof parsed === "object" && parsed !== null) body = parsed as Record<string, unknown>;
  } catch {
    // Not JSON. The status and the headers are still the contract, so this is not a failure to parse
    // the gateway -- it is evidence that something else answered.
  }

  const headerMeta = metaFrom(response.headers);
  const str = (key: string): string => (typeof body[key] === "string" ? (body[key] as string) : "");

  const type = str("type");
  const problem: ProblemDetails = {
    status: response.status,
    // The LAST path segment of the type URI. Matching the whole URI would break when the host in it
    // changes, and it has.
    typeSuffix: type ? (type.split("/").pop() ?? "") : "",
    title: str("title"),
    detail: str("detail") || (text ? text.slice(0, 300) : `HTTP ${response.status}`),
    instance: str("instance"),
    retryAfter: retryAfterMs(response.headers),
    meta: {
      ...headerMeta,
      requestId: str("request_id") || headerMeta.requestId,
      traceId: str("trace_id") || headerMeta.traceId,
      // The 429 envelope omits the scope header and carries `scope` in the body instead.
      rateLimit: headerMeta.rateLimit
        ? {
            ...headerMeta.rateLimit,
            scope: headerMeta.rateLimit.scope ?? (str("scope") || undefined),
          }
        : undefined,
    },
    raw: Object.keys(body).length > 0 ? body : text,
  };

  return errorFromProblem(problem);
}

/** Builds the per-request headers. Absent rather than empty when a value was not supplied. */
function requestHeaders(config: ResolvedConfig, token: string, options: CallOptions): Headers {
  const headers = new Headers({
    Authorization: `Bearer ${token}`,
    Accept: "application/json",
    "User-Agent": config.userAgent,
  });
  if (options.idempotencyKey) headers.set("Idempotency-Key", options.idempotencyKey);
  if (options.instance) headers.set("X-Prometheus-Instance", options.instance);
  if (options.requireParameters) headers.set("X-Prometheus-Require-Parameters", "true");
  return headers;
}

/**
 * Composes the caller's signal with a timeout, without taking ownership of theirs.
 *
 * `AbortSignal.any` is used where available so an abort from either source wins. The fallback exists
 * because it reached Node in 20.3 and this package claims 20: a listener does the same job with one
 * more object.
 */
/**
 * Node's fetch (undici) timing out while waiting for response headers.
 *
 * Matched on the error CODE rather than the message, because the message is localised and has
 * changed between Node releases. `fetch` wraps it, so the code is on `cause`; checked one level
 * down as well, since a dispatcher can wrap it again.
 */
function isHeadersTimeout(error: unknown): boolean {
  const codes = new Set(["UND_ERR_HEADERS_TIMEOUT", "UND_ERR_BODY_TIMEOUT"]);
  let current: unknown = error;
  for (let depth = 0; depth < 4 && current instanceof Error; depth += 1) {
    const code = (current as Error & { code?: unknown }).code;
    if (typeof code === "string" && codes.has(code)) return true;
    if (current.name === "HeadersTimeoutError" || current.name === "BodyTimeoutError") return true;
    current = current.cause;
  }
  return false;
}

function combineSignals(timeoutMs: number, caller: AbortSignal | undefined): AbortSignal {
  const timeout = AbortSignal.timeout(timeoutMs);
  if (!caller) return timeout;
  const any = (AbortSignal as { any?: (signals: AbortSignal[]) => AbortSignal }).any;
  if (typeof any === "function") return any([timeout, caller]);

  const controller = new AbortController();
  const forward = (reason: unknown): void => controller.abort(reason);
  if (caller.aborted) forward(caller.reason);
  else caller.addEventListener("abort", () => forward(caller.reason), { once: true });
  timeout.addEventListener("abort", () => forward(timeout.reason), { once: true });
  return controller.signal;
}

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

/** What the retry loop records about what it did, so a caller can see the cost of a slow call. */
export interface Attempts {
  readonly attempts: number;
  readonly waitedMs: number;
}

/** One logical call, retried where the platform says no generation occurred. */
export class Transport {
  readonly config: ResolvedConfig;
  readonly tokens: TokenManager;
  readonly retry: RetryPolicy;
  private readonly cooldowns = new CooldownRegistry();
  /** The budget as of the most recent response, whichever call produced it. */
  lastRateLimit: RateLimitSnapshot | undefined;

  constructor(config: ResolvedConfig, retry: RetryPolicy = DEFAULT_RETRY) {
    this.config = config;
    this.tokens = new TokenManager(config);
    this.retry = retry;
  }

  /**
   * Sends one request, retrying where the contract says nothing was generated.
   *
   * `streaming` does **not** exclude a request from this loop, and this is the one place to say so
   * because the reverse looks like the safer reading. A streamed request that fails *here* failed
   * before its first byte of body existed: the gateway reads the engine's status before sending the
   * `200`/`text/event-stream` headers, so an error at this point means nothing was generated and
   * nothing was billed. A retry is therefore a first generation rather than a second — and it is the
   * only retry there is, since the gateway performs none of its own on a streamed request. Once the
   * stream has begun the failure arrives in band, where this function cannot see it, and is never
   * retried. Agreed across every SDK in this family; the shared corpus pins the attempt count.
   */
  async send(
    method: string,
    path: string,
    init: { body?: unknown; model?: string; streaming?: boolean } & CallOptions,
  ): Promise<{ response: Response; meta: ResponseMeta; attempts: Attempts }> {
    if (
      init.idempotencyKey !== undefined &&
      init.idempotencyKey.length > MAX_IDEMPOTENCY_KEY_LENGTH
    ) {
      throw new InvalidRequestError(
        `idempotencyKey is ${init.idempotencyKey.length} characters; the gateway accepts at most ` +
          `${MAX_IDEMPOTENCY_KEY_LENGTH}. Refused here to save the round trip -- and because the ` +
          `gateway reports an over-long key as a conflict, which misdirects.`,
      );
    }

    const key = `${this.config.gatewayBaseURL}|${init.model ?? ""}`;
    const cooling = this.cooldowns.remaining(key);
    if (cooling > 0) {
      throw errorFromProblem({
        status: 503,
        typeSuffix: "backend-unavailable",
        title: "Service Unavailable",
        detail:
          `The gateway reported this backend as unavailable and asked to wait; ` +
          `${Math.round(cooling)}ms of that wait remains. Refused locally, without a request, to ` +
          `honour the wait the gateway supplied.`,
        instance: path,
        retryAfter: cooling,
        meta: metaFrom(new Headers()),
        raw: {},
      });
    }

    const timeout =
      init.timeout ?? (init.streaming ? this.config.timeouts.stream : this.config.timeouts.request);
    const serialised = init.body === undefined ? undefined : JSON.stringify(init.body);

    let attempt = 1;
    let waitedMs = 0;
    // Stamps the loop's counters onto an error before it leaves. A call that waited 40s across three
    // attempts and then failed is the one whose duration most needs explaining, and until this
    // existed it was also the only one that explained nothing: four of the five SDKs still carry
    // that limit, and the docs say so.
    const stamp = <E extends Error>(failure: E): E => {
      const carrier = failure as E & { meta?: ResponseMeta };
      if (carrier.meta) carrier.meta = { ...carrier.meta, attempts: attempt, waitedMs };
      return failure;
    };
    let rejectedToken: string | undefined;
    // Bounded, and the bound is the point. The condition below compares the rejected token with the
    // one just used, which is false again on every refresh -- so without a counter a gateway that
    // answers 401 to every token loops forever, fetching a new one each time. A test written to
    // assert "raised rather than looped" found it by hanging.
    let refreshes = 0;

    for (;;) {
      const token = rejectedToken
        ? await this.tokens.refreshAfterRejection(rejectedToken)
        : await this.tokens.token();
      const headers = requestHeaders(this.config, token, init);
      if (serialised !== undefined) headers.set("Content-Type", "application/json");

      let response: Response;
      try {
        response = await this.config.fetch(`${this.config.gatewayBaseURL}${path}`, {
          method,
          headers,
          ...(serialised === undefined ? {} : { body: serialised }),
          signal: combineSignals(timeout, init.signal),
        });
      } catch (cause) {
        const failure = this.translate(cause, timeout, init);
        // A timeout is never retried: the backend is probably still generating, so a retry queues a
        // second billable generation rather than resuming the first.
        const delay =
          failure instanceof TimeoutError ? undefined : delayFor(this.retry, attempt, undefined);
        if (delay === undefined) throw stamp(failure);
        await sleep(delay);
        waitedMs += delay;
        attempt += 1;
        continue;
      }

      const meta = metaFrom(response.headers);
      if (meta.rateLimit) this.lastRateLimit = meta.rateLimit;

      if (response.ok) {
        this.cooldowns.clear(key);
        // Rebuilt rather than mutated: ResponseMeta is readonly, and the retry loop is the only
        // place that knows the final numbers. `stamp` above does the same for the errors it throws.
        return {
          response,
          meta: { ...meta, attempts: attempt, waitedMs },
          attempts: { attempts: attempt, waitedMs },
        };
      }

      const failure = await errorFrom(response);

      // One reactive refresh per rejected token, then the loop retries with the new one. Separate
      // from the backoff path because nothing needs waiting for: the token was stale, not the model
      // busy.
      if (response.status === 401 && refreshes === 0 && failure.retryable) {
        rejectedToken = token;
        refreshes += 1;
        attempt += 1;
        continue;
      }

      if (failure.typeSuffix === "backend-unavailable" && failure.retryAfter !== undefined) {
        this.cooldowns.record(key, failure.retryAfter);
      }

      if (!failure.retryable) throw stamp(failure);
      const delay = delayFor(this.retry, attempt, failure.retryAfter);
      if (delay === undefined) throw stamp(failure);
      await sleep(delay);
      waitedMs += delay;
      attempt += 1;
    }
  }

  /** Sends a request and decodes a JSON body, whatever its top-level shape. */
  async sendJSON(
    method: string,
    path: string,
    init: { body?: unknown; model?: string } & CallOptions,
  ): Promise<RawResponse> {
    const { response, meta } = await this.send(method, path, init);
    const text = await response.text();
    let value: unknown;
    try {
      value = text ? JSON.parse(text) : undefined;
    } catch (cause) {
      throw new TransportError(
        `The gateway returned a ${response.status} body that is not JSON: ${text.slice(0, 200)}`,
        { cause },
      );
    }
    return { value, meta, status: response.status };
  }

  private translate(cause: unknown, timeout: number, init: CallOptions): Error {
    const aborted =
      cause instanceof Error && (cause.name === "AbortError" || cause.name === "TimeoutError");
    if (aborted && init.signal?.aborted) {
      return new TransportError("The request was cancelled by the caller's signal.", { cause });
    }
    // Node's own fetch gives up on a response that has sent no headers after 300 s, whatever this
    // SDK's `timeouts.request` says, and reports it as `TypeError: fetch failed`. On a NON-STREAMING
    // generation the headers arrive at the end, so every generation longer than five minutes hits it.
    //
    // It is classified here as a timeout, which means NEVER RETRIED, and that is the whole point.
    // Before this it read as an unrecognised transport failure and was retried: measured, one call
    // became THREE BILLABLE GENERATIONS while the error said "Could not reach the gateway", which is
    // false — the gateway answered and was still generating. The retry policy exists to prevent
    // exactly that, and its own default timeout was unreachable underneath it.
    if (isHeadersTimeout(cause)) {
      return new TimeoutError(
        `Node's fetch gave up after 300s waiting for response headers, which is undici's ` +
          `\`headersTimeout\` and not this SDK's \`timeouts.request\` (${timeout}ms). A non-streaming ` +
          `generation sends its headers only when it finishes, so anything slower than five minutes ` +
          `fails here however high you set the timeout. The backend is probably still generating. ` +
          `Two fixes: use \`chat.completions.stream()\`, where headers arrive immediately and the ` +
          `limit never applies; or pass your own \`fetch\` with a larger \`headersTimeout\` — ` +
          `\`new Axonium({ fetch: (u, i) => undiciFetch(u, { ...i, dispatcher: new Agent({ headersTimeout: 900_000 }) }) })\`. ` +
          `This SDK cannot raise it for you without taking a dependency on undici.`,
        { cause },
      );
    }
    if (aborted) {
      return new TimeoutError(
        `The request timed out after ${timeout}ms. The backend may still be generating, so retrying ` +
          `would start a second billable generation rather than resuming this one -- pass an ` +
          `idempotencyKey if you need a repeat to be safe.`,
        { cause },
      );
    }
    return new TransportError(`Could not reach the gateway at ${this.config.gatewayBaseURL}.`, {
      cause,
    });
  }
}
