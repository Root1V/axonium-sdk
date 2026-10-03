/**
 * TypeScript SDK for the Prometheus inference platform.
 *
 * Server-side by default: a credential is an account, and this package refuses one where it cannot
 * establish that it is running somewhere its owner controls. See `allowInsecureCredential`.
 */
export { VERSION, USER_AGENT } from "./version.ts";

export type { AxoniumOptions, ResolvedConfig, Timeouts, TokenProvider } from "./config.ts";
export { DEFAULT_TIMEOUTS, resolveConfig } from "./config.ts";

export type { ProblemDetails, RateLimitSnapshot, ResponseMeta } from "./errors.ts";
export {
  APIError,
  AxoniumError,
  BackendUnavailableError,
  CapacityExhaustedError,
  ConfigurationError,
  ContextExceededError,
  ForbiddenError,
  IdempotencyInProgressError,
  IdempotencyKeyReuseError,
  IdempotencyResponseNotRetainedError,
  InconsistentModelGroupError,
  InvalidClientError,
  InvalidDateError,
  InvalidIdempotencyKeyError,
  InvalidRangeError,
  InvalidRequestError,
  InvalidScopeError,
  InvalidTokenError,
  MethodNotAllowedError,
  MissingCredentialsError,
  ModalityMismatchError,
  ModelNotLoadedError,
  NotFoundError,
  OAuthError,
  PredictBackendRejectedError,
  RangeTooLargeError,
  RateLimitError,
  RateLimitingUnavailableError,
  ServerError,
  SpendCapExceededError,
  StreamInterruptedError,
  TimeoutError,
  TokenEndpointNotConfiguredError,
  TokenEndpointUnavailableError,
  TokenExpiredError,
  TokenRevokedError,
  TransportError,
  UnauthorizedClientError,
  UnauthorizedRequestError,
  UnknownInstanceError,
  UnknownModelError,
  UnknownParameterError,
  UnknownRouteError,
  UnsupportedGrantTypeError,
  UpstreamError,
  UsageStoreUnavailableError,
  ValidationError,
  errorFromOAuth,
  errorFromProblem,
} from "./errors.ts";
