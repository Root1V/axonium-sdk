"""Exception hierarchy for the Prometheus inference platform.

One class per row of the gateway's error catalog: the gateway is the component that answers, so
its catalog is what an SDK can type against.

Two error envelopes exist and they are deliberately kept apart:

- The gateway returns RFC 9457 problem details for everything under ``/v1/`` — modeled by
  :class:`APIError` and its subclasses, selected by the last path segment of the ``type`` URI.
- The auth-service token endpoint returns the RFC 6749 ``{"error", "error_description"}`` shape —
  modeled by :class:`OAuthError`, which is *not* an :class:`APIError` because neither its fields
  nor its semantics carry over.

The catalog lives in ``spec/errors.json`` and is mirrored by every language SDK. Branch on the
exception type or on ``type_suffix``; never on ``detail``, which is human-readable prose that may
be reworded at any time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from axonium.models.common import RateLimitSnapshot

__all__ = [
    "APIError",
    "AuthTransportError",
    "AxoniumError",
    "BackendUnavailableError",
    "BadRequestError",
    "ConfigurationError",
    "ContextExceededError",
    "ForbiddenError",
    "IdempotencyInProgressError",
    "IdempotencyKeyReuseError",
    "IdempotencyResponseNotRetainedError",
    "InconsistentModelGroupError",
    "InvalidClientError",
    "InvalidDateError",
    "InvalidIdempotencyKeyError",
    "InvalidRangeError",
    "InvalidRequestError",
    "InvalidScopeError",
    "InvalidTokenError",
    "MissingCredentialsError",
    "ModalityMismatchError",
    "ModelNotLoadedError",
    "NotFoundError",
    "OAuthError",
    "RangeTooLargeError",
    "RateLimitError",
    "RateLimitingUnavailableError",
    "ServerError",
    "SpendCapExceededError",
    "StreamInterruptedError",
    "TimeoutError",
    "TokenEndpointNotConfiguredError",
    "TokenEndpointUnavailableError",
    "TokenExpiredError",
    "TokenRevokedError",
    "ToolCallArgumentsError",
    "TransportError",
    "UnauthorizedClientError",
    "UnauthorizedError",
    "UnauthorizedRequestError",
    "UnknownInstanceError",
    "UnknownModelError",
    "UnknownParameterError",
    "UnsupportedFieldWarning",
    "UnsupportedGrantTypeError",
    "UnusedCredentialWarning",
    "UpstreamError",
    "UsageStoreUnavailableError",
    "ValidationError",
    "error_from_response",
]


class AxoniumError(Exception):
    """Base class for every error raised by this SDK."""


class ConfigurationError(AxoniumError):
    """A required setting is missing or invalid.

    Raised before any network call, and names both the setting and the environment variable that
    can supply it, so a misconfigured deployment fails loudly at construction instead of
    surfacing later as a confusing request error.
    """


class UnsupportedFieldWarning(UserWarning):
    """A request field was dropped because the gateway does not accept it.

    The gateway's request schemas are allowlists and silently discard unknown fields, so the SDK
    warns rather than letting a caller believe a parameter took effect. This is a warning and not
    an error so that a newer gateway accepting more fields never breaks an older SDK.
    """


class UnusedCredentialWarning(UserWarning):
    """Credentials were found but will not be used, because a token provider takes precedence.

    Worth saying out loud: an operator who set them almost certainly believes they are in use, and
    the whole point of the provider mode is that the secret need not be in this process at all.
    """


class InvalidRequestError(AxoniumError):
    """A request was rejected by the SDK before it was sent.

    Raised where a value cannot be valid — a temperature outside the accepted range, an unknown
    role, a remote image URL — so the mistake surfaces at the call site instead of costing a round
    trip. Everything this SDK raises is an :class:`AxoniumError`, so a caller never has to catch a
    validation library's exceptions alongside ours.
    """


class ToolCallArgumentsError(AxoniumError):
    """A tool call's ``arguments`` string could not be decoded.

    Almost always a generation stopped by ``max_tokens`` partway through writing the call, leaving
    a string that was never going to parse. The offending ``ToolCall`` is attached so the raw value
    stays reachable — it is often enough to see what the model was trying to call.

    Raised rather than returning ``None`` so a truncated call cannot be mistaken for a call with no
    arguments, and typed rather than letting ``json``'s own error escape, so a caller never has to
    catch a standard-library exception alongside this SDK's.
    """

    def __init__(self, message: str, *, tool_call: Any = None) -> None:
        super().__init__(message)
        self.tool_call = tool_call


class TransportError(AxoniumError):
    """The request never produced an HTTP response (DNS, connection, or TLS failure)."""


class AuthTransportError(TransportError):
    """The auth-service could not be reached, or answered with something unusable.

    Distinct from :class:`OAuthError`, which is the auth-service correctly reporting that the
    credentials or the grant were rejected. This one means the token could not be obtained at all,
    so no request can be attempted.
    """


class TimeoutError(TransportError):
    """The client gave up waiting for a response.

    Not retried automatically: the backend may still be generating, and a retry would queue a
    second billable generation on top of the first.
    """


class APIError(AxoniumError):
    """An RFC 9457 problem-details error returned by the gateway."""

    #: Last path segment of the problem-details ``type`` URI, e.g. ``"unknown-model"``. ``None``
    #: when the response was not parseable problem+json.
    type_suffix: str | None = None

    #: Whether retrying this error can plausibly succeed. Subclasses override; see
    #: ``spec/errors.json`` for the per-error retry policy.
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        status: int,
        type_suffix: str | None = None,
        title: str | None = None,
        detail: str | None = None,
        instance: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        retry_after: float | None = None,
        rate_limit: RateLimitSnapshot | None = None,
        raw: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.title = title
        self.detail = detail
        self.instance = instance
        self.request_id = request_id
        #: Absent on the rate-limiting middleware's envelope, hence optional.
        self.trace_id = trace_id
        self.retry_after = retry_after
        self.rate_limit = rate_limit
        self.raw = raw or {}
        if type_suffix is not None:
            self.type_suffix = type_suffix

    #: Client-side diagnosis added where the SDK can say something the gateway's ``detail`` does
    #: not, such as exactly which scope a token is missing.
    hint: str | None = None

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.hint:
            parts.append(self.hint)
        if self.request_id:
            parts.append(f"request_id={self.request_id}")
        if self.trace_id:
            parts.append(f"trace_id={self.trace_id}")
        return " ".join(parts)


# --------------------------------------------------------------------------------------
# Status-keyed fallbacks.
#
# An unrecognized `type` suffix resolves to one of these rather than raising a parse error:
# the catalog is expected to grow, and an SDK that hard-fails on an unfamiliar error code
# would break the moment the gateway adds one.
# --------------------------------------------------------------------------------------


class BadRequestError(APIError):
    """4xx client error that is not covered by a more specific subclass."""


class UnauthorizedError(APIError):
    """401 that is not one of the specific token errors."""


class InconsistentModelGroupError(BadRequestError):
    """The replicas serving one model disagree about their modality.

    The gateway refuses the whole group rather than quietly dropping the odd one, which is the
    right call: answering a chat request from an embedding backend produces confident nonsense
    rather than an error, and nonsense is the expensive failure. ``detail`` names each instance and
    what it claims.
    """

    type_suffix = "inconsistent-model-group"
    retryable = False


class UnauthorizedRequestError(UnauthorizedError):
    """The request carried no verified claims.

    Distinct from :class:`MissingCredentialsError`, which the auth middleware raises earlier for a
    missing or malformed header. Not a token that aged out, so refreshing one does not help — which
    is why this is the one ``401`` this SDK will not retry after a refresh.
    """

    type_suffix = "unauthorized"
    retryable = False


class InvalidDateError(BadRequestError):
    """A usage query's ``start`` or ``end`` is not a ``YYYY-MM-DD`` date."""

    type_suffix = "invalid-date"
    retryable = False


class InvalidRangeError(BadRequestError):
    """A usage export's ``end`` is before its ``start``."""

    type_suffix = "invalid-range"
    retryable = False


class RangeTooLargeError(BadRequestError):
    """A usage export's range exceeds 366 days. Split it into several exports."""

    type_suffix = "range-too-large"
    retryable = False


class NotFoundError(APIError):
    """The addressed resource does not exist, or does not belong to this client.

    The gateway returns the same ``404`` for both on purpose: a ``403`` would confirm that an id
    exists, which is exactly what a probe wants to learn.

    On ``usage.retrieve`` there is a third case behind the same status, and it is the common one —
    **the id belongs to a replay**. A replay reaches no model and is not billed, so it has no row.
    Use ``meta.idempotent_replay_of`` to get the id of the generation that was charged.
    """

    type_suffix = "not-found"
    retryable = False


class ServerError(APIError):
    """5xx error that is not covered by a more specific subclass."""

    retryable = True


class TokenEndpointUnavailableError(ServerError):
    """The gateway could not reach the auth-service to issue a token.

    The **only** problem+json a token request can produce — every other token outcome uses the
    RFC 6749 OAuth2 shape. That distinction is what makes it safe to retry: an OAuth2 failure means
    the credentials or the grant are wrong and retrying cannot help, while this means the gateway
    momentarily could not do its job.
    """

    type_suffix = "upstream-unavailable"
    retryable = True


class TokenEndpointNotConfiguredError(ServerError):
    """This deployment has no token endpoint wired up.

    Shares a status with :class:`TokenEndpointUnavailableError` but not its retryability, which is
    why the suffix has to drive the decision rather than the status.
    """

    type_suffix = "not-configured"
    retryable = False


# --------------------------------------------------------------------------------------
# 400
# --------------------------------------------------------------------------------------


class UnknownModelError(BadRequestError):
    """The model ID is not registered.

    Checked before any scope check, so an unrecognized model is always a 400 — never a 403,
    and never a 404.
    """

    type_suffix = "unknown-model"


class ModalityMismatchError(BadRequestError):
    """The model's modality does not match the request.

    For example an image content part sent to a non-vision model, or a non-embedding model
    used on ``/v1/embeddings``.
    """

    type_suffix = "modality-mismatch"


class ContextExceededError(BadRequestError):
    """The request exceeds the model's context window."""

    type_suffix = "context-exceeded"


class UnknownParameterError(BadRequestError):
    """A request field outside the gateway's accepted subset, refused instead of dropped.

    Raised only when the request carried ``require_parameters: true``. Without that flag the same
    request succeeds and the dropped names come back in ``X-Prometheus-Ignored-Parameters``, which
    is the gateway's own answer about what it discarded — and a better one than any client-side
    allowlist, which ages.

    Deliberately not a ``422``: this says *that field does not exist here*, not *that value is
    wrong*. ``detail`` names every offending field.
    """

    type_suffix = "unknown-parameter"


class UnknownInstanceError(BadRequestError):
    """The pinned instance does not serve the requested model.

    A pin never silently falls back: asking for one instance gets that instance or an error.
    """

    type_suffix = "unknown-instance"


class InvalidIdempotencyKeyError(BadRequestError):
    """The ``Idempotency-Key`` is malformed — today, longer than 255 characters.

    A ``400`` rather than a conflict, because it never conflicted with anything. The SDK checks
    the length before sending, so this normally surfaces as
    :class:`~axonium.errors.InvalidRequestError` instead.
    """

    type_suffix = "invalid-idempotency-key"


class IdempotencyKeyReuseError(APIError):
    """The key was already used for a different request.

    The fingerprint covers the path as well as the body, so the same key on another endpoint
    counts as a different request. Never retryable: use a fresh key per logical request.
    """

    type_suffix = "idempotency-key-reuse"


class IdempotencyInProgressError(APIError):
    """The first request with this key is still running.

    The only idempotency refusal worth retrying, and the only one carrying ``retry_after`` — the
    model's observed p95 latency minus how long the first request has already run. Retrying cannot
    start a second generation: it waits, replays, or refuses again.
    """

    type_suffix = "idempotency-in-progress"
    retryable = True


class IdempotencyResponseNotRetainedError(APIError):
    """The original succeeded, but its response was too large to store.

    Not retryable, and it carries good news: this refusal is proof the original worked. Retrying
    would generate a second time for a result that already exists.
    """

    type_suffix = "idempotency-response-not-retained"


class ValidationError(BadRequestError):
    """The request body failed schema validation.

    ``raw["errors"]`` carries the field-level problems — each with ``loc``, ``msg``, ``type`` and
    the offending ``input`` — which is what names the field to fix rather than only saying the
    request was malformed.
    """

    type_suffix = "validation-error"


# --------------------------------------------------------------------------------------
# 401
# --------------------------------------------------------------------------------------


class MissingCredentialsError(UnauthorizedError):
    """No or malformed ``Authorization`` header, or a token passed as a query parameter."""

    type_suffix = "missing-credentials"


class InvalidTokenError(UnauthorizedError):
    """Signature, algorithm, issuer, audience or ``sub`` claim validation failed."""

    type_suffix = "invalid-token"


class TokenExpiredError(UnauthorizedError):
    """The token's ``exp`` has passed.

    The SDK refreshes and retries once on this automatically; seeing it surface to a caller means
    the retry also failed.
    """

    type_suffix = "token-expired"
    retryable = True


class TokenRevokedError(UnauthorizedError):
    """The token or client was explicitly revoked by an admin.

    Not retryable: new credentials are required from the platform operator.
    """

    type_suffix = "token-revoked"


# --------------------------------------------------------------------------------------
# 402 / 403
# --------------------------------------------------------------------------------------


class SpendCapExceededError(APIError):
    """The client hit its configured monthly spend cap.

    Distinct from a rate limit: waiting does not help within the billing period. The cap has to be
    raised by the operator.
    """

    type_suffix = "spend-cap-exceeded"


class ForbiddenError(APIError):
    """The token lacks a scope the request requires.

    Deny-by-default: ``inference:read`` or ``inference:stream`` grants no model access on its own,
    and every inference call additionally needs the ``model:<id>`` scope for the model being
    called. Streaming and non-streaming take *different* scopes, and holding one does not imply
    the other.
    """

    type_suffix = "forbidden"


# --------------------------------------------------------------------------------------
# 429
# --------------------------------------------------------------------------------------


class RateLimitError(APIError):
    """The requests-per-minute budget was exceeded.

    Enforced independently per ``client_id`` and per ``user_id``. ``retry_after`` carries how long
    to wait; the gateway emits the same value in the ``Retry-After`` header and the body's
    ``retry_after`` field, so the two cannot disagree.
    """

    type_suffix = "rate-limit-exceeded-requests"
    retryable = True


# --------------------------------------------------------------------------------------
# 5xx
# --------------------------------------------------------------------------------------


class UpstreamError(ServerError):
    """The backend failed and the gateway's own internal retries were exhausted.

    The gateway already made three attempts with backoff before returning this, so an SDK-side
    retry should be at most one attempt, and never for a request that already ran close to the
    full timeout.
    """

    type_suffix = "upstream-error"


class ModelNotLoadedError(ServerError):
    """The model is registered but not currently deployed.

    Not retryable despite the 5xx status: it needs operator action, not patience.
    """

    type_suffix = "model-not-loaded"
    retryable = False


class BackendUnavailableError(ServerError):
    """The gateway's circuit breaker is open for this backend, or the backend was unreachable.

    Two causes with different handling. With ``retry_after`` set, the gateway's circuit breaker is
    open: it fast-failed without attempting the backend, and the value is its expected recovery
    time, which makes retrying after that wait both cheap and well-informed. Without it, the
    backend was genuinely unreachable and the platform supplies no wait hint at all — any backoff
    there is the SDK's own conservative guess rather than an API fact.
    """

    type_suffix = "backend-unavailable"


class RateLimitingUnavailableError(ServerError):
    """The rate limiter's backing store is down and the deployment fails closed."""

    type_suffix = "rate-limiting-unavailable"


class UsageStoreUnavailableError(ServerError):
    """A usage-endpoint database read failed."""

    type_suffix = "usage-store-unavailable"


# --------------------------------------------------------------------------------------
# Streaming
# --------------------------------------------------------------------------------------


class StreamInterruptedError(AxoniumError):
    """The stream failed partway through.

    Mid-stream failures cannot use an HTTP status: by the time a backend fails, the ``200`` and
    ``text/event-stream`` headers are already committed. The gateway signals them in-band instead,
    so this is raised on an error chunk rather than on a status code.

    ``partial_content`` holds everything successfully received before the failure. Retrying means
    re-running a generation whose partial output was already delivered and billed.
    """

    def __init__(
        self,
        message: str,
        *,
        partial_content: str = "",
        request_id: str | None = None,
        trace_id: str | None = None,
        raw: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.partial_content = partial_content
        self.request_id = request_id
        self.trace_id = trace_id
        self.raw = raw or {}


# --------------------------------------------------------------------------------------
# OAuth2 token endpoint (RFC 6749) — a separate branch of the hierarchy on purpose
# --------------------------------------------------------------------------------------


class OAuthError(AxoniumError):
    """An error from the auth-service token endpoint."""

    #: RFC 6749 ``error`` code, e.g. ``"invalid_client"``.
    error: str | None = None

    def __init__(
        self,
        message: str,
        *,
        status: int,
        error: str | None = None,
        error_description: str | None = None,
        raw: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.error_description = error_description
        self.raw = raw or {}
        if error is not None:
            self.error = error


class UnsupportedGrantTypeError(OAuthError):
    """The grant type is not supported. The SDK always uses ``client_credentials``."""

    error = "unsupported_grant_type"


class InvalidScopeError(OAuthError):
    """An unrecognized scope, or one this account is not allowed to request.

    Requesting a scope the account does not hold is an error rather than a silent downgrade.
    """

    error = "invalid_scope"


class InvalidClientError(OAuthError):
    """Bad ``client_id`` or ``client_secret``."""

    error = "invalid_client"


class UnauthorizedClientError(OAuthError):
    """The account is deactivated."""

    error = "unauthorized_client"


# --------------------------------------------------------------------------------------
# Construction from responses
# --------------------------------------------------------------------------------------

_BY_SUFFIX: dict[str, type[APIError]] = {
    cls.type_suffix: cls
    for cls in (
        UnknownModelError,
        ModalityMismatchError,
        ContextExceededError,
        ValidationError,
        InvalidIdempotencyKeyError,
        IdempotencyKeyReuseError,
        IdempotencyInProgressError,
        IdempotencyResponseNotRetainedError,
        UnknownInstanceError,
        UnknownParameterError,
        MissingCredentialsError,
        InvalidTokenError,
        TokenExpiredError,
        TokenRevokedError,
        SpendCapExceededError,
        ForbiddenError,
        RateLimitError,
        UpstreamError,
        ModelNotLoadedError,
        NotFoundError,
        InconsistentModelGroupError,
        UnauthorizedRequestError,
        InvalidDateError,
        InvalidRangeError,
        RangeTooLargeError,
        TokenEndpointUnavailableError,
        TokenEndpointNotConfiguredError,
        BackendUnavailableError,
        RateLimitingUnavailableError,
        UsageStoreUnavailableError,
    )
    if cls.type_suffix is not None
}

_BY_OAUTH_CODE: dict[str, type[OAuthError]] = {
    cls.error: cls
    for cls in (
        UnsupportedGrantTypeError,
        InvalidScopeError,
        InvalidClientError,
        UnauthorizedClientError,
    )
    if cls.error is not None
}


def _fallback_for_status(status: int) -> type[APIError]:
    if status == 401:
        return UnauthorizedError
    if status >= 500:
        return ServerError
    return BadRequestError


def error_from_response(
    *,
    status: int,
    body: dict[str, Any] | None,
    retry_after: float | None = None,
    rate_limit: RateLimitSnapshot | None = None,
) -> APIError:
    """Build the most specific :class:`APIError` for a gateway error response.

    ``retry_after`` should already be resolved by the caller, which prefers the ``Retry-After``
    header over the body's ``retry_after`` field when both are present.
    """
    body = body or {}
    type_uri = body.get("type")
    suffix = type_uri.rstrip("/").rsplit("/", 1)[-1] if isinstance(type_uri, str) else None

    cls = _BY_SUFFIX.get(suffix) if suffix else None
    if cls is None:
        cls = _fallback_for_status(status)

    if retry_after is None:
        raw_retry = body.get("retry_after")
        if isinstance(raw_retry, (int, float)):
            retry_after = float(raw_retry)

    detail = body.get("detail")
    title = body.get("title")
    message = detail or title or f"HTTP {status}"

    return cls(
        message,
        status=status,
        type_suffix=suffix,
        title=title if isinstance(title, str) else None,
        detail=detail if isinstance(detail, str) else None,
        instance=body.get("instance") if isinstance(body.get("instance"), str) else None,
        request_id=body.get("request_id") if isinstance(body.get("request_id"), str) else None,
        trace_id=body.get("trace_id") if isinstance(body.get("trace_id"), str) else None,
        retry_after=retry_after,
        rate_limit=rate_limit,
        raw=body,
    )


def oauth_error_from_response(*, status: int, body: dict[str, Any] | None) -> OAuthError:
    """Build the most specific :class:`OAuthError` for a token-endpoint error response."""
    body = body or {}
    code = body.get("error")
    cls = _BY_OAUTH_CODE.get(code, OAuthError) if isinstance(code, str) else OAuthError

    description = body.get("error_description")
    message = description or code or f"HTTP {status}"

    return cls(
        message,
        status=status,
        error=code if isinstance(code, str) else None,
        error_description=description if isinstance(description, str) else None,
        raw=body,
    )
