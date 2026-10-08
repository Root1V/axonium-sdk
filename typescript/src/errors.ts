/**
 * The error hierarchy, mapped from the gateway's RFC 9457 problem-details envelope.
 *
 * Every gateway error carries a `type` URI whose **last path segment** is the discriminator:
 * `https://prometheus.internal/errors/unknown-model` → `unknown-model`. The full URI is not matched
 * because it is an identifier rather than an address, and the host in it has changed before.
 *
 * The suffixes here are the 34 rows of `spec/errors.json`, which is the catalogue all SDKs in this
 * family map and which a test in `test/errors.test.ts` asserts this file against in both directions:
 * a row with no class here, and a class here naming a suffix no row has. The second is the one worth
 * having --- an invented suffix sends a caller to catch a case that never arrives, and nothing else
 * would ever tell them.
 *
 * The token endpoint is deliberately a separate branch. It answers RFC 6749's `{error,
 * error_description}` rather than problem+json, so {@link OAuthError} does **not** extend
 * {@link APIError}: `catch (e) { if (e instanceof APIError) ... }` must not quietly swallow a
 * credential failure, whose remedy is nothing like an inference failure's.
 */

/** Everything this package throws. One `catch` for the whole surface. */
export class AxoniumError extends Error {
  override readonly name: string = "AxoniumError";

  constructor(message: string, options?: { cause?: unknown }) {
    super(message, options);
    // Without this, `instanceof` works but `err.name` and stack traces report "Error" when the
    // package is consumed from compiled CommonJS, where the prototype chain is rebuilt.
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

/** A setting is missing or contradictory, discovered before any request is made. */
export class ConfigurationError extends AxoniumError {
  override readonly name = "ConfigurationError";
}

/** The request was refused locally, so nothing reached the gateway and nothing was billed. */
export class InvalidRequestError extends AxoniumError {
  override readonly name = "InvalidRequestError";
}

/** The request never got an answer: DNS, TLS, a dropped connection, an abort. */
export class TransportError extends AxoniumError {
  // Annotated `string` rather than left to infer the literal: TimeoutError extends this, and an
  // inferred literal type on a subclassed field forbids the subclass from naming itself.
  override readonly name: string = "TransportError";
}

/**
 * The client gave up waiting.
 *
 * Its own class because the remedy is the opposite of a transport failure's: the backend is probably
 * **still generating**, so retrying starts a second billable generation rather than resuming the
 * first. The gateway allows 600s for a non-streaming request and some image backends legitimately
 * take minutes.
 */
export class TimeoutError extends TransportError {
  override readonly name = "TimeoutError";
}

/** A stream that began and then failed, carrying whatever had already arrived. */
export class StreamInterruptedError extends AxoniumError {
  override readonly name = "StreamInterruptedError";

  readonly partialContent: string;
  readonly meta: ResponseMeta;

  constructor(
    message: string,
    partialContent: string,
    meta: ResponseMeta,
    options?: { cause?: unknown },
  ) {
    super(message, options);
    this.partialContent = partialContent;
    this.meta = meta;
  }
}

/** The correlation metadata of one exchange. Present on successes too, not only on failures. */
export interface ResponseMeta {
  readonly requestId: string | undefined;
  readonly traceId: string | undefined;
  readonly instance: string | undefined;
  readonly instanceId: string | undefined;
  readonly idempotentReplay: boolean;
  readonly idempotentReplayOf: string | undefined;
  readonly rateLimit: RateLimitSnapshot | undefined;
  /**
   * How many HTTP attempts produced this response. `1` means it worked first time.
   */
  readonly attempts: number;
  /**
   * Milliseconds this SDK spent **deliberately asleep** before answering: respected `Retry-After`
   * waits and backoff, and nothing else.
   *
   * It is here because a respected `Retry-After` of up to 60 seconds looks, from outside, exactly
   * like one slow call among fast ones — three separate teams reported that as a hang. A log line
   * is invisible by default and a latency metric cannot read one, so the number travels on the
   * answer. Subtract it from your own wall clock to get what the platform actually spent.
   *
   * The other four SDKs have carried this since they had retries. This one computed both numbers
   * from `0.1.0` and dropped them on the floor, while exporting an `Attempts` type no public call
   * ever returned.
   */
  readonly waitedMs: number;
  /**
   * Request fields the gateway accepted, ignored, and named back.
   *
   * `undefined` when there were none --- the header is absent then, so **its presence always means
   * something** and an empty array would blur that into "it looked and found nothing".
   *
   * The endpoint takes an OpenAI-compatible *subset*: `n`, `presence_penalty`, `logit_bias`, `seed` and
   * the like neither fail the request nor reach the engine. Until `PRM-127` they were dropped in
   * silence, and the guide's own words on fixing that are the reason this is exposed rather than read
   * and discarded --- *a setting that does nothing and says nothing is indistinguishable from one that
   * works*. An SDK that swallowed the header would restore exactly that silence.
   *
   * Pass `requireParameters: true` to get a `400 unknown-parameter` instead, when being quietly given
   * something else is worse than failing.
   */
  readonly ignoredParameters: readonly string[] | undefined;
}

/**
 * The rate-limit budget as of one response.
 *
 * `scope` names **which** budget, and is an **open set read from the header** rather than a list
 * kept here. The previous version of this comment enumerated six names and one of them — `chat` —
 * was wrong; the header says `chat_completions`, so a caller keying a map by the documented name
 * would never have matched. Five SDKs kept five different hand-written lists, and the guide
 * contradicts itself about the set, so the header is the only answer that cannot be stale.
 *
 * What is worth knowing and is not a list: all three pass-through modalities share one `predict`
 * budget, so a classification request spends the same allowance as a typed decision. And **key it
 * before you cache it** — one logical operation touching embeddings, rerank and chat gets three
 * responses about three budgets, and a single "last seen" slot holds whichever answered last while
 * looking entirely plausible.
 *
 * The budget is a **fixed 60-second bucket aligned to the wall clock**, not a sliding window: the
 * whole allowance returns at second 0 of each minute, which is what `resetRequests` timestamps. A
 * burst can straddle a boundary and pass where the same burst seconds earlier is refused, so pace
 * against `remainingRequests` rather than against an assumed rate. The budget is counted per
 * credential; the limit *value* is platform configuration per endpoint, so a 429 means your own
 * credential emptied its own bucket and raising it is an operator action.
 */
export interface RateLimitSnapshot {
  readonly scope: string | undefined;
  readonly limitRequests: number | undefined;
  readonly remainingRequests: number | undefined;
  readonly resetRequests: number | undefined;
  readonly limitTokens: number | undefined;
  readonly remainingTokens: number | undefined;
  readonly resetTokens: number | undefined;
}

/** The fields of a problem-details envelope, after the `type` URI has been reduced to its suffix. */
export interface ProblemDetails {
  readonly status: number;
  readonly typeSuffix: string;
  readonly title: string;
  readonly detail: string;
  readonly instance: string;
  readonly retryAfter: number | undefined;
  readonly meta: ResponseMeta;
  /** The decoded body as received, so an extension member this SDK does not model stays reachable. */
  readonly raw: unknown;
}

/** A gateway error. One subclass per catalogued `type` suffix; unknown suffixes fall back by status. */
export class APIError extends AxoniumError {
  override readonly name: string = "APIError";

  /** The suffix this subclass claims. `undefined` on the status-keyed fallbacks. */
  static readonly typeSuffix: string | undefined = undefined;
  /** Whether retrying can help, as the catalogue declares it. */
  static readonly retryable: boolean = false;

  readonly status: number;
  readonly typeSuffix: string;
  readonly title: string;
  readonly detail: string;
  readonly instance: string;
  readonly retryAfter: number | undefined;
  readonly meta: ResponseMeta;
  readonly raw: unknown;

  constructor(problem: ProblemDetails, options?: { cause?: unknown }) {
    super(APIError.describe(problem), options);
    this.status = problem.status;
    this.typeSuffix = problem.typeSuffix;
    this.title = problem.title;
    this.detail = problem.detail;
    this.instance = problem.instance;
    this.retryAfter = problem.retryAfter;
    this.meta = problem.meta;
    this.raw = problem.raw;
  }

  /**
   * Whether retrying this particular failure can help.
   *
   * An instance method rather than only the static, because one row of the catalogue cannot answer
   * from its name: `predict-backend-rejected` keeps the **engine's** status, so a `429` from the
   * engine is retryable and a `422` is not, under one suffix. {@link PredictBackendRejectedError}
   * overrides this.
   */
  get retryable(): boolean {
    return (this.constructor as typeof APIError).retryable;
  }

  private static describe(problem: ProblemDetails): string {
    const parts = [problem.detail || problem.title || `HTTP ${problem.status}`];
    if (problem.meta.requestId) parts.push(`request_id=${problem.meta.requestId}`);
    if (problem.meta.traceId) parts.push(`trace_id=${problem.meta.traceId}`);
    return parts.join(" ");
  }
}

/* --- 400 ---------------------------------------------------------------------------------- */

export class UnknownModelError extends APIError {
  override readonly name = "UnknownModelError";
  static override readonly typeSuffix = "unknown-model";
}

export class ModalityMismatchError extends APIError {
  override readonly name = "ModalityMismatchError";
  static override readonly typeSuffix = "modality-mismatch";
}

export class ContextExceededError extends APIError {
  override readonly name = "ContextExceededError";
  static override readonly typeSuffix = "context-exceeded";
}

/** Only when the request carried `require_parameters: true`; otherwise the names come back in a header. */
export class UnknownParameterError extends APIError {
  override readonly name = "UnknownParameterError";
  static override readonly typeSuffix = "unknown-parameter";
}

export class UnknownInstanceError extends APIError {
  override readonly name = "UnknownInstanceError";
  static override readonly typeSuffix = "unknown-instance";
}

export class InvalidIdempotencyKeyError extends APIError {
  override readonly name = "InvalidIdempotencyKeyError";
  static override readonly typeSuffix = "invalid-idempotency-key";
}

/** Replicas of one model disagree about their modality, so the gateway refuses the whole group. */
export class InconsistentModelGroupError extends APIError {
  override readonly name = "InconsistentModelGroupError";
  static override readonly typeSuffix = "inconsistent-model-group";
}

export class InvalidDateError extends APIError {
  override readonly name = "InvalidDateError";
  static override readonly typeSuffix = "invalid-date";
}

export class InvalidRangeError extends APIError {
  override readonly name = "InvalidRangeError";
  static override readonly typeSuffix = "invalid-range";
}

export class RangeTooLargeError extends APIError {
  override readonly name = "RangeTooLargeError";
  static override readonly typeSuffix = "range-too-large";
}

/* --- 401 / 402 / 403 ---------------------------------------------------------------------- */

export class MissingCredentialsError extends APIError {
  override readonly name = "MissingCredentialsError";
  static override readonly typeSuffix = "missing-credentials";
}

export class InvalidTokenError extends APIError {
  override readonly name = "InvalidTokenError";
  static override readonly typeSuffix = "invalid-token";
}

/** Retryable because the remedy is mechanical: fetch a new token and send the request again. */
export class TokenExpiredError extends APIError {
  override readonly name = "TokenExpiredError";
  static override readonly typeSuffix = "token-expired";
  static override readonly retryable = true;
}

export class TokenRevokedError extends APIError {
  override readonly name = "TokenRevokedError";
  static override readonly typeSuffix = "token-revoked";
}

export class UnauthorizedRequestError extends APIError {
  override readonly name = "UnauthorizedRequestError";
  static override readonly typeSuffix = "unauthorized";
}

export class SpendCapExceededError extends APIError {
  override readonly name = "SpendCapExceededError";
  static override readonly typeSuffix = "spend-cap-exceeded";
}

/**
 * The token is valid and does not carry the scope this call needs.
 *
 * Two different causes behind one status: a missing `model:<id>` grant, or a missing
 * `inference:stream` on an otherwise-readable model. The gateway's `detail` names which.
 */
export class ForbiddenError extends APIError {
  override readonly name = "ForbiddenError";
  static override readonly typeSuffix = "forbidden";
}

/* --- 404 / 405 / 409 ---------------------------------------------------------------------- */

export class NotFoundError extends APIError {
  override readonly name = "NotFoundError";
  static override readonly typeSuffix = "not-found";
}

/**
 * No route at that URL --- a mistake in the calling code rather than a fact about the caller's data.
 *
 * Deliberately **not** {@link NotFoundError}, and the platform split them for these SDKs' benefit:
 * `not-found` is about data, which a caller may read as an empty result or retry. A bad URL is
 * neither, and on `usage.get` the common cause of `not-found` is an id belonging to a replay.
 */
export class UnknownRouteError extends APIError {
  override readonly name = "UnknownRouteError";
  static override readonly typeSuffix = "unknown-route";
}

/** The URL exists, the verb does not. The `Allow` response header lists the ones that do. */
export class MethodNotAllowedError extends APIError {
  override readonly name = "MethodNotAllowedError";
  static override readonly typeSuffix = "method-not-allowed";
}

/**
 * The gateway's fingerprint for this key does not match the one it stored.
 *
 * Usually the key was sent with a different request — the fingerprint covers the path as well as
 * the payload, so another endpoint counts. But it also happens with a byte-identical request: the
 * fingerprint is taken over the *gateway's* parsed request model including its defaults, not over
 * what the client sent, so an additive change to that model invalidates every key stored before
 * it. Measured by Veritium on 2026-10-08, after `PRM-235` added two optional fields.
 *
 * So do **not** mint a fresh key reflexively. If the body genuinely did not change, a new key buys
 * a second billable generation for work the first request may already have finished. Never
 * retryable, and deliberately never auto-recovered with a new key.
 */
export class IdempotencyKeyReuseError extends APIError {
  override readonly name = "IdempotencyKeyReuseError";
  static override readonly typeSuffix = "idempotency-key-reuse";
}

/** The first request with this key is still running. Retryable: wait and ask again. */
export class IdempotencyInProgressError extends APIError {
  override readonly name = "IdempotencyInProgressError";
  static override readonly typeSuffix = "idempotency-in-progress";
  static override readonly retryable = true;
}

export class IdempotencyResponseNotRetainedError extends APIError {
  override readonly name = "IdempotencyResponseNotRetainedError";
  static override readonly typeSuffix = "idempotency-response-not-retained";
}

/* --- 422 ---------------------------------------------------------------------------------- */

export class ValidationError extends APIError {
  override readonly name = "ValidationError";
  static override readonly typeSuffix = "validation-error";
}

/**
 * The engine behind the pass-through route refused the request, and the gateway wrapped it.
 *
 * **The only row in the catalogue whose status is not fixed**: the engine's status is kept, so a
 * `422` stays a `422` and a `429` stays a `429`. The type is named for what the gateway can see and
 * does not claim a cause.
 *
 * The engine's own error body survives verbatim under the `backend_error` extension member, reachable
 * through {@link APIError.raw} --- and it is usually the only thing that names the offending field,
 * because the gateway cannot know what the engine wanted.
 */
export class PredictBackendRejectedError extends APIError {
  override readonly name = "PredictBackendRejectedError";
  static override readonly typeSuffix = "predict-backend-rejected";

  /** Decided from the status rather than the name, which is why this override exists. */
  override get retryable(): boolean {
    return this.status === 429;
  }
}

/* --- 5xx --------------------------------------------------------------------------------- */

/** A `5xx` with no more specific subclass. Retryable by default, as the catalogue has it. */
export class ServerError extends APIError {
  override readonly name: string = "ServerError";
  // Likewise `boolean`: two 5xx subclasses below are deliberately NOT retryable, and an inferred
  // `true` would make those overrides a type error rather than the point.
  static override readonly retryable: boolean = true;
}

export class UpstreamError extends ServerError {
  override readonly name = "UpstreamError";
  static override readonly typeSuffix = "upstream-error";
}

/** Every replica is busy rather than broken, so waiting is the whole remedy. */
export class CapacityExhaustedError extends ServerError {
  override readonly name = "CapacityExhaustedError";
  static override readonly typeSuffix = "capacity-exhausted";
}

/** A `5xx` that is **not** retryable: it needs operator action, not patience. */
export class ModelNotLoadedError extends ServerError {
  override readonly name = "ModelNotLoadedError";
  static override readonly typeSuffix = "model-not-loaded";
  static override readonly retryable = false;
}

/** Circuit breaker open, or the backend is unreachable. `Retry-After` is present for the first. */
export class BackendUnavailableError extends ServerError {
  override readonly name = "BackendUnavailableError";
  static override readonly typeSuffix = "backend-unavailable";
}

export class RateLimitingUnavailableError extends ServerError {
  override readonly name = "RateLimitingUnavailableError";
  static override readonly typeSuffix = "rate-limiting-unavailable";
}

export class UsageStoreUnavailableError extends ServerError {
  override readonly name = "UsageStoreUnavailableError";
  static override readonly typeSuffix = "usage-store-unavailable";
}

/**
 * A reranker running on an engine whose rerank request shape the gateway has not recorded. Only on
 * `POST /v1/rerank`.
 *
 * **The one 5xx in the catalogue that is not retryable**, which is why it is named rather than left
 * to fall through to {@link ServerError} — that fallback *is* retryable, so without this class the
 * SDK would retry through its whole attempt budget and report a timeout for a condition that was
 * never going to clear. The gateway records each engine's dialect deliberately, because a reranker
 * on a new engine is not llama.cpp's shape just because the last one was. An operator registers it.
 */
export class RerankDialectUnknownError extends ServerError {
  override readonly name = "RerankDialectUnknownError";
  static override readonly typeSuffix = "rerank-dialect-unknown";
  static override readonly retryable = false;
}

/** The gateway could not reach the auth-service to issue a token. */
export class TokenEndpointUnavailableError extends ServerError {
  override readonly name = "TokenEndpointUnavailableError";
  static override readonly typeSuffix = "upstream-unavailable";
}

/** The deployment has no auth-service configured. Operator action; retrying cannot help. */
export class TokenEndpointNotConfiguredError extends ServerError {
  override readonly name = "TokenEndpointNotConfiguredError";
  static override readonly typeSuffix = "not-configured";
  static override readonly retryable = false;
}

/* --- 429 --------------------------------------------------------------------------------- */

/**
 * A rate-limit budget is exhausted.
 *
 * `retryAfter` is honoured verbatim rather than replaced by this SDK's backoff: the gateway computes
 * it from the real reset time, which is better information than any local guess.
 */
export class RateLimitError extends APIError {
  override readonly name = "RateLimitError";
  static override readonly typeSuffix = "rate-limit-exceeded-requests";
  static override readonly retryable = true;
}

/* --- the token endpoint, which is a different contract ------------------------------------- */

/**
 * An RFC 6749 failure from `POST /oauth2/token`.
 *
 * **Deliberately not an {@link APIError}.** The shapes differ --- `{error, error_description}`
 * against problem+json --- and so do the remedies: an inference failure may be worth retrying with
 * the same credential, and a credential failure never is.
 */
export class OAuthError extends AxoniumError {
  override readonly name: string = "OAuthError";

  static readonly code: string | undefined = undefined;
  static readonly retryable: boolean = false;

  readonly status: number;
  readonly code: string;
  readonly description: string;

  constructor(
    message: string,
    status: number,
    code: string,
    description: string,
    options?: { cause?: unknown },
  ) {
    super(message, options);
    this.status = status;
    this.code = code;
    this.description = description;
  }

  get retryable(): boolean {
    return (this.constructor as typeof OAuthError).retryable;
  }
}

export class UnsupportedGrantTypeError extends OAuthError {
  override readonly name = "UnsupportedGrantTypeError";
  static override readonly code = "unsupported_grant_type";
}

/** Asking for a scope you do not hold is an error here, not a silent downgrade. */
export class InvalidScopeError extends OAuthError {
  override readonly name = "InvalidScopeError";
  static override readonly code = "invalid_scope";
}

export class InvalidClientError extends OAuthError {
  override readonly name = "InvalidClientError";
  static override readonly code = "invalid_client";
}

export class UnauthorizedClientError extends OAuthError {
  override readonly name = "UnauthorizedClientError";
  static override readonly code = "unauthorized_client";
}

/* --- dispatch ----------------------------------------------------------------------------- */

/** Every gateway subclass, in one list, so the tables below and the parity test read one source. */
const GATEWAY_CLASSES = [
  UnknownModelError,
  ModalityMismatchError,
  ContextExceededError,
  UnknownParameterError,
  UnknownInstanceError,
  InvalidIdempotencyKeyError,
  InconsistentModelGroupError,
  InvalidDateError,
  InvalidRangeError,
  RangeTooLargeError,
  MissingCredentialsError,
  InvalidTokenError,
  TokenExpiredError,
  TokenRevokedError,
  UnauthorizedRequestError,
  SpendCapExceededError,
  ForbiddenError,
  NotFoundError,
  UnknownRouteError,
  MethodNotAllowedError,
  IdempotencyKeyReuseError,
  IdempotencyInProgressError,
  IdempotencyResponseNotRetainedError,
  ValidationError,
  PredictBackendRejectedError,
  UpstreamError,
  CapacityExhaustedError,
  ModelNotLoadedError,
  BackendUnavailableError,
  RateLimitingUnavailableError,
  UsageStoreUnavailableError,
  RerankDialectUnknownError,
  TokenEndpointUnavailableError,
  TokenEndpointNotConfiguredError,
  RateLimitError,
] as const;

const OAUTH_CLASSES = [
  UnsupportedGrantTypeError,
  InvalidScopeError,
  InvalidClientError,
  UnauthorizedClientError,
] as const;

const BY_SUFFIX: ReadonlyMap<string, typeof APIError> = new Map(
  GATEWAY_CLASSES.map((cls) => [cls.typeSuffix as string, cls as unknown as typeof APIError]),
);

const BY_OAUTH_CODE: ReadonlyMap<string, typeof OAuthError> = new Map(
  OAUTH_CLASSES.map((cls) => [cls.code as string, cls as unknown as typeof OAuthError]),
);

/**
 * The suffixes this SDK claims, read off the classes rather than listed again.
 *
 * Exported for the parity test. A second hand-kept list is how a suffix ends up claimed by a class
 * and absent from the catalogue, or the reverse --- which is the failure this repository keeps
 * finding, and it has found it inside a test whose job was to catch it.
 */
export const CLAIMED_SUFFIXES: readonly string[] = GATEWAY_CLASSES.map(
  (cls) => cls.typeSuffix as string,
);

/** The OAuth2 codes this SDK claims, likewise read off the classes. */
export const CLAIMED_OAUTH_CODES: readonly string[] = OAUTH_CLASSES.map(
  (cls) => cls.code as string,
);

/**
 * Picks the class for a problem-details envelope.
 *
 * An unknown suffix falls back **by status** rather than throwing: the catalogue grows, and a new
 * error the platform documents on a Tuesday must not become a parse failure in a published SDK. The
 * caller still gets the suffix, the detail and the ids.
 */
export function errorFromProblem(problem: ProblemDetails, options?: { cause?: unknown }): APIError {
  const named = BY_SUFFIX.get(problem.typeSuffix);
  if (named) return new named(problem, options);
  if (problem.status >= 500) return new ServerError(problem, options);
  return new APIError(problem, options);
}

/** Picks the class for an RFC 6749 token-endpoint failure. */
export function errorFromOAuth(
  status: number,
  code: string,
  description: string,
  options?: { cause?: unknown },
): OAuthError {
  const message = description ? `${code}: ${description}` : code;
  const named = BY_OAUTH_CODE.get(code);
  if (named) return new named(message, status, code, description, options);
  return new OAuthError(message, status, code, description, options);
}
