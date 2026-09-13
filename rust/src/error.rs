//! The error taxonomy, mirrored from `spec/errors.json` so it cannot drift from the other SDKs.
//!
//! Two envelopes exist and are deliberately kept apart. The gateway returns RFC 9457 problem
//! details for everything under `/v1/`, modelled by [`ApiError`] and matched by the last path
//! segment of the `type` URI. The auth-service token endpoint returns the RFC 6749
//! `{"error", "error_description"}` shape, modelled by [`Error::OAuth`], which is a different
//! variant on purpose: neither its fields nor its meaning carry over.
//!
//! Match on [`ApiError::kind`], never on `detail` — that is human-readable prose that may be
//! reworded at any time, and the platform says so explicitly.

use std::collections::BTreeMap;
use std::fmt;

use serde_json::Value;

/// Every error the gateway catalogues, plus the fallbacks an unknown one lands on.
///
/// Unknown suffixes resolve to a status-keyed variant rather than failing to parse: the catalogue
/// grows, and an SDK that hard-failed on an unfamiliar code would break the day one is added.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorKind {
    // 400
    UnknownModel,
    ModalityMismatch,
    ContextExceeded,
    ValidationError,
    UnknownInstance,
    InvalidIdempotencyKey,
    // 401
    MissingCredentials,
    InvalidToken,
    TokenExpired,
    TokenRevoked,
    // 402 / 403
    SpendCapExceeded,
    Forbidden,
    // 409
    IdempotencyKeyReuse,
    IdempotencyInProgress,
    IdempotencyResponseNotRetained,
    // 429
    RateLimitExceeded,
    // 5xx
    UpstreamError,
    ModelNotLoaded,
    BackendUnavailable,
    RateLimitingUnavailable,
    UsageStoreUnavailable,
    /// A 4xx this build does not know, or an error carrying no `type` at all — which happens:
    /// request validation used to arrive without one.
    OtherClientError,
    /// A 401 that is none of the specific token errors.
    Unauthorized,
    /// A 5xx this build does not know.
    OtherServerError,
}

impl ErrorKind {
    fn from_suffix(suffix: &str, status: u16) -> Self {
        match suffix {
            "unknown-model" => Self::UnknownModel,
            "modality-mismatch" => Self::ModalityMismatch,
            "context-exceeded" => Self::ContextExceeded,
            "validation-error" => Self::ValidationError,
            "unknown-instance" => Self::UnknownInstance,
            "invalid-idempotency-key" => Self::InvalidIdempotencyKey,
            "missing-credentials" => Self::MissingCredentials,
            "invalid-token" => Self::InvalidToken,
            "token-expired" => Self::TokenExpired,
            "token-revoked" => Self::TokenRevoked,
            "spend-cap-exceeded" => Self::SpendCapExceeded,
            "forbidden" => Self::Forbidden,
            "idempotency-key-reuse" => Self::IdempotencyKeyReuse,
            "idempotency-in-progress" => Self::IdempotencyInProgress,
            "idempotency-response-not-retained" => Self::IdempotencyResponseNotRetained,
            "rate-limit-exceeded-requests" => Self::RateLimitExceeded,
            "upstream-error" => Self::UpstreamError,
            "model-not-loaded" => Self::ModelNotLoaded,
            "backend-unavailable" => Self::BackendUnavailable,
            "rate-limiting-unavailable" => Self::RateLimitingUnavailable,
            "usage-store-unavailable" => Self::UsageStoreUnavailable,
            _ if status == 401 => Self::Unauthorized,
            _ if status >= 500 => Self::OtherServerError,
            _ => Self::OtherClientError,
        }
    }

    /// Whether retrying this can plausibly succeed at all.
    ///
    /// A necessary condition, not a sufficient one: [`crate::RetryPolicy`] additionally requires
    /// that no generation occurred, because this API has no idempotency by default and a retried
    /// generation is billable rather than a replay.
    pub fn retryable(self) -> bool {
        matches!(
            self,
            Self::TokenExpired
                | Self::RateLimitExceeded
                | Self::UpstreamError
                | Self::BackendUnavailable
                | Self::RateLimitingUnavailable
                | Self::UsageStoreUnavailable
                | Self::IdempotencyInProgress
                | Self::OtherServerError
        )
    }
}

/// An RFC 9457 problem-details error returned by the gateway.
#[derive(Debug, Clone)]
pub struct ApiError {
    pub status: u16,
    pub kind: ErrorKind,
    /// Last path segment of the `type` URI. Empty when the response carried none.
    pub type_suffix: String,
    pub title: String,
    pub detail: String,
    pub instance: String,
    pub request_id: String,
    /// Omitted by the rate-limiting middleware's envelope, hence often empty.
    pub trace_id: String,
    /// Resolved wait in seconds, or `None` when the platform supplied one.
    pub retry_after: Option<f64>,
    /// A client-side diagnosis added where the SDK can say something `detail` does not, such as
    /// exactly which scope a token is missing.
    pub hint: String,
    /// The decoded body, so a field this SDK does not model stays reachable.
    pub raw: BTreeMap<String, Value>,
}

impl ApiError {
    pub fn retryable(&self) -> bool {
        self.kind.retryable()
    }
}

impl fmt::Display for ApiError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let message = if !self.detail.is_empty() {
            &self.detail
        } else if !self.title.is_empty() {
            &self.title
        } else {
            return write!(f, "HTTP {}", self.status);
        };
        write!(f, "{message}")?;
        if !self.hint.is_empty() {
            write!(f, " {}", self.hint)?;
        }
        if !self.request_id.is_empty() {
            write!(f, " request_id={}", self.request_id)?;
        }
        if !self.trace_id.is_empty() {
            write!(f, " trace_id={}", self.trace_id)?;
        }
        Ok(())
    }
}

/// Everything this SDK can fail with.
#[derive(Debug)]
pub enum Error {
    /// A required setting is missing or invalid. Raised before any network call, naming both the
    /// field and the environment variable that can supply it.
    Configuration(String),
    /// Rejected by the SDK before it was sent, so the mistake surfaces at the call site rather
    /// than costing a round trip.
    InvalidRequest(String),
    /// The gateway answered with a problem-details error.
    ///
    /// Boxed because it is by far the largest variant -- a decoded body plus half a dozen strings
    /// -- and an unboxed one would make every `Result<T>` in this crate pay for it.
    Api(Box<ApiError>),
    /// The auth-service rejected the credentials or the grant. Deliberately not an [`Error::Api`]:
    /// a caller handling "the gateway is unhappy" must not silently absorb "your credentials are
    /// wrong".
    OAuth {
        status: u16,
        code: String,
        description: String,
    },
    /// The auth-service could not be reached, or answered with something unusable, so no token
    /// could be obtained at all.
    AuthTransport(String),
    /// The request never produced a response (DNS, connection, TLS).
    Transport(String),
    /// The client gave up waiting. Not retried unless an idempotency key was supplied: without
    /// one the backend is probably still generating, and a retry is a second billable generation.
    Timeout(String),
    /// The stream failed partway through. Mid-stream failures cannot use an HTTP status -- by the
    /// time a backend fails, the `200` and `text/event-stream` headers are already committed --
    /// so the gateway signals them in band.
    StreamInterrupted {
        message: String,
        /// Everything successfully received before the failure. Retrying means re-running a
        /// generation whose partial output was already delivered and billed.
        partial_content: String,
        request_id: String,
        trace_id: String,
    },
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Configuration(m) => write!(f, "invalid Axonium configuration: {m}"),
            Self::InvalidRequest(m) => write!(f, "invalid request: {m}"),
            Self::Api(e) => write!(f, "{e}"),
            Self::OAuth {
                status,
                code,
                description,
            } => {
                let what = if !description.is_empty() {
                    description
                } else if !code.is_empty() {
                    code
                } else {
                    return write!(f, "token endpoint: HTTP {status}");
                };
                write!(f, "token endpoint: {what}")
            }
            Self::AuthTransport(m) => write!(f, "auth transport: {m}"),
            Self::Transport(m) => write!(f, "transport: {m}"),
            Self::Timeout(m) => write!(f, "timeout: {m}"),
            Self::StreamInterrupted {
                message,
                request_id,
                trace_id,
                ..
            } => {
                write!(f, "the stream was interrupted: {message}")?;
                if !request_id.is_empty() {
                    write!(f, " request_id={request_id}")?;
                }
                if !trace_id.is_empty() {
                    write!(f, " trace_id={trace_id}")?;
                }
                Ok(())
            }
        }
    }
}

impl std::error::Error for Error {}

impl Error {
    /// The gateway error kind, when this is one. Lets a caller match without destructuring.
    pub fn kind(&self) -> Option<ErrorKind> {
        match self {
            Self::Api(e) => Some(e.kind),
            _ => None,
        }
    }
}

pub(crate) fn api_error_from_body(
    status: u16,
    body: Option<&Value>,
    retry_after: Option<f64>,
) -> ApiError {
    let map: BTreeMap<String, Value> = body
        .and_then(Value::as_object)
        .map(|o| o.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
        .unwrap_or_default();

    let string = |key: &str| {
        map.get(key)
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string()
    };

    let type_suffix = map
        .get("type")
        .and_then(Value::as_str)
        .map(|uri| {
            uri.trim_end_matches('/')
                .rsplit('/')
                .next()
                .unwrap_or(uri)
                .to_string()
        })
        .unwrap_or_default();

    let retry_after = retry_after.or_else(|| map.get("retry_after").and_then(Value::as_f64));

    ApiError {
        status,
        kind: ErrorKind::from_suffix(&type_suffix, status),
        type_suffix,
        title: string("title"),
        detail: string("detail"),
        instance: string("instance"),
        request_id: string("request_id"),
        trace_id: string("trace_id"),
        retry_after,
        hint: String::new(),
        raw: map,
    }
}

pub(crate) fn oauth_error_from_body(status: u16, body: Option<&Value>) -> Error {
    let get = |key: &str| {
        body.and_then(|b| b.get(key))
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string()
    };
    Error::OAuth {
        status,
        code: get("error"),
        description: get("error_description"),
    }
}

/// A convenient alias for everything this crate returns.
pub type Result<T> = std::result::Result<T, Error>;
