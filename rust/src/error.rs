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
    /// Only when the request carried `require_parameters: true`. Without it the same request
    /// succeeds and the dropped names come back in `X-Prometheus-Ignored-Parameters`.
    UnknownParameter,
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
    /// The resource does not exist, or does not belong to this client -- deliberately the same
    /// answer for both, since a 403 would confirm an id exists. On a usage lookup there is a
    /// third case behind it and it is the common one: the id belongs to a replay, which is
    /// not billed and has no row.
    NotFound,
    /// The replicas serving one model disagree about their modality. The gateway refuses the
    /// group rather than dropping the odd one: answering a chat request from an embedding
    /// backend produces confident nonsense, which is the expensive failure.
    InconsistentModelGroup,
    /// The request carried no verified claims. Distinct from `MissingCredentials`, which the
    /// auth middleware raises earlier, and not a token that aged out -- refreshing does not help.
    UnauthorizedRequest,
    /// The usage-export range errors.
    InvalidDate,
    InvalidRange,
    RangeTooLarge,
    /// The gateway could not reach the auth-service to issue a token. The only problem+json a
    /// token request can produce -- every other token outcome uses the RFC 6749 shape -- and
    /// the distinction is what makes it safe to retry, where an OAuth2 failure never is.
    TokenEndpointUnavailable,
    /// This deployment has no token endpoint wired up. Shares a status with
    /// `TokenEndpointUnavailable` but not its retryability.
    TokenEndpointNotConfigured,
    // 429
    RateLimitExceeded,
    /// The engine behind `predict` refused the request, wrapped rather than forwarded.
    ///
    /// The one kind whose status is not fixed: `/v1/models/{model}/predict` passes the body
    /// through, so when the engine refuses, its status is kept -- a 422 stays a 422 -- and its
    /// error body is preserved under the `backend_error` extension member. The name therefore
    /// claims no cause, and [`ErrorKind::retryable`] cannot answer for it; use
    /// [`ApiError::retryable`], which reads the status.
    PredictBackendRejected,
    // 5xx
    UpstreamError,
    /// Every replica of the model is above its pending-work headroom, so the request was refused
    /// rather than queued behind work that would outlive its own timeout. Distinct from
    /// [`ErrorKind::BackendUnavailable`] on purpose: that means the replicas are broken, this
    /// means they are working. `retry_after` is always 1 and is a hint, not a promise.
    CapacityExhausted,
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
            "unknown-parameter" => Self::UnknownParameter,
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
            "capacity-exhausted" => Self::CapacityExhausted,
            "predict-backend-rejected" => Self::PredictBackendRejected,
            "model-not-loaded" => Self::ModelNotLoaded,
            "backend-unavailable" => Self::BackendUnavailable,
            "rate-limiting-unavailable" => Self::RateLimitingUnavailable,
            "usage-store-unavailable" => Self::UsageStoreUnavailable,
            "not-found" => Self::NotFound,
            "inconsistent-model-group" => Self::InconsistentModelGroup,
            "unauthorized" => Self::UnauthorizedRequest,
            "invalid-date" => Self::InvalidDate,
            "invalid-range" => Self::InvalidRange,
            "range-too-large" => Self::RangeTooLarge,
            "upstream-unavailable" => Self::TokenEndpointUnavailable,
            "not-configured" => Self::TokenEndpointNotConfigured,
            _ if status == 401 => Self::Unauthorized,
            _ if status >= 500 => Self::OtherServerError,
            _ => Self::OtherClientError,
        }
    }

    /// Whether retrying this can plausibly succeed at all.
    ///
    /// A necessary condition, not a sufficient one: [`crate::RetryPolicy`] additionally requires
    /// either that no generation occurred, or that an `Idempotency-Key` makes the repeat a replay
    /// rather than a second billing.
    ///
    /// [`Self::PredictBackendRejected`] is deliberately absent and answers `false` here: its
    /// status belongs to an engine this gateway only wraps, so the name cannot decide. Ask
    /// [`ApiError::retryable`] instead, which has the status to read.
    pub fn retryable(self) -> bool {
        matches!(
            self,
            Self::TokenExpired
                | Self::CapacityExhausted
                | Self::RateLimitExceeded
                | Self::UpstreamError
                | Self::BackendUnavailable
                | Self::RateLimitingUnavailable
                | Self::UsageStoreUnavailable
                | Self::IdempotencyInProgress
                // The gateway failing to reach the auth-service, not an OAuth2 outcome.
                // TokenEndpointNotConfigured shares its status and is deliberately absent:
                // it needs operator action, so retrying cannot help.
                | Self::TokenEndpointUnavailable
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
    /// Empty when the response carried none. The rate-limiting envelope used to omit it always;
    /// since guide 2026-09-19b it does not, so on a current deployment this is populated there too.
    pub trace_id: String,
    /// Resolved wait in seconds, or `None` when the platform supplied one.
    pub retry_after: Option<f64>,
    /// A client-side diagnosis added where the SDK can say something `detail` does not, such as
    /// exactly which scope a token is missing.
    pub hint: String,
    /// The rate-limit budget as of this failure, when the response reported one.
    ///
    /// Present so a `429` can say *which* budget it exhausted, which is the difference between
    /// backing off the right endpoint and backing off all of them. Python and Go have carried this
    /// on their errors since the beginning; Rust did not, and the shared contract corpus is what
    /// noticed.
    pub rate_limit: Option<crate::types::RateLimit>,
    /// The decoded body, so a field this SDK does not model stays reachable.
    pub raw: BTreeMap<String, Value>,
}

/// The engine statuses worth repeating when the gateway wraps a refusal as
/// `predict-backend-rejected`. Everything else the engine rejects is the request to fix.
const PREDICT_BACKEND_RETRYABLE_STATUSES: &[u16] = &[429];

impl ApiError {
    pub fn retryable(&self) -> bool {
        if self.kind == ErrorKind::PredictBackendRejected {
            return PREDICT_BACKEND_RETRYABLE_STATUSES.contains(&self.status);
        }
        self.kind.retryable()
    }

    /// The engine's own error body, preserved verbatim, on an error from
    /// `POST /v1/models/{model}/predict`.
    ///
    /// `None` when the extension member is absent: a gateway that wrapped the refusal without
    /// capturing it, in which case the status is all there is. The shape is the engine's and this
    /// SDK does not model it -- that is what the pass-through route means.
    pub fn backend_error(&self) -> Option<&Value> {
        self.raw.get("backend_error")
    }

    /// The engine's status when it differs from this error's.
    ///
    /// Present on the 502 path, where the gateway reports its own status because a 500 the engine
    /// produced is not one a caller can act on. On the 4xx path the two are the same and this is
    /// `None`.
    pub fn backend_status(&self) -> Option<u16> {
        self.raw.get("backend_status")?.as_u64().map(|v| v as u16)
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
    /// A tool call's `arguments` string could not be decoded.
    ///
    /// Almost always a generation stopped by `max_tokens` partway through writing the call,
    /// leaving a string that was never going to parse. Returned rather than yielding an empty map
    /// so a truncated call cannot be mistaken for one that genuinely took no arguments. The raw
    /// string is left untouched and stays reachable on the call itself.
    ToolCallArguments {
        tool_call_id: String,
        arguments: String,
        reason: String,
    },
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
            Self::ToolCallArguments {
                tool_call_id,
                arguments,
                reason,
            } => write!(
                f,
                "the arguments for tool call {tool_call_id:?} are not a JSON object ({reason}). \
                 A generation stopped by max_tokens leaves them truncated -- check finish_reason. \
                 Raw value: {arguments:?}"
            ),
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

/// The body's value when it has one, the header's otherwise.
fn non_empty(from_body: String, from_header: &str) -> String {
    if from_body.is_empty() {
        from_header.to_string()
    } else {
        from_body
    }
}

/// `request_id` and `trace_id` are what the response headers carried; the body wins when it has
/// them. Not every error body is a complete problem+json envelope -- a validation failure
/// forwarded from a backend, or an HTML page from a proxy that never reached the gateway -- and
/// without them an error that *did* carry a request id hands the caller nothing to take to the
/// platform team.
pub(crate) fn api_error_from_body(
    status: u16,
    body: Option<&Value>,
    retry_after: Option<f64>,
    rate_limit: Option<crate::types::RateLimit>,
    request_id: &str,
    trace_id: &str,
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
        request_id: non_empty(string("request_id"), request_id),
        trace_id: non_empty(string("trace_id"), trace_id),
        retry_after,
        hint: String::new(),
        rate_limit,
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

#[cfg(test)]
mod catalog_parity {
    //! The error catalog and this SDK's mapping have to agree, and a test has to say so.
    //!
    //! A contract that can drift from the code without anyone noticing is not a contract. Go has
    //! had this guard from the start and it earned its keep immediately: when the platform added
    //! two token errors to `spec/errors.json`, Go refused the change until both had a mapping, and
    //! refused again until their retryability matched. This crate had no such guard and **silently
    //! shipped `upstream-unavailable` as not retryable** until a hand-written test caught it.
    //!
    //! A unit test rather than an integration one because it calls the real constructor, which is
    //! `pub(crate)`. Testing a copy of it would prove only that the copy works.

    use super::*;

    fn catalog() -> Vec<Value> {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("workspace root")
            .join("spec/errors.json");
        let raw: Value =
            serde_json::from_str(&std::fs::read_to_string(path).expect("reading spec/errors.json"))
                .expect("parsing spec/errors.json");
        raw["gateway_errors"]
            .as_array()
            .expect("gateway_errors is an array")
            .clone()
    }

    /// The statuses a catalogued entry should be exercised at.
    ///
    /// Every entry but one fixes a single number. `predict-backend-rejected` keeps whatever the
    /// engine returned, so the guide's row reads `4xx` and the catalog says so literally; that
    /// entry carries `probe_statuses` instead, and `as_u64` on its `status` would panic.
    fn probe_statuses(entry: &Value) -> Vec<u16> {
        if let Some(listed) = entry["probe_statuses"].as_array() {
            return listed
                .iter()
                .map(|s| s.as_u64().expect("probe status") as u16)
                .collect();
        }
        vec![entry["status"].as_u64().expect("status") as u16]
    }

    /// What the catalog says retrying this entry at this status should do.
    ///
    /// A bool for every entry whose retryability is a property of the error itself, and the
    /// string `"by_status"` for the one where it is not.
    fn want_retryable(entry: &Value, status: u16) -> bool {
        if let Some(declared) = entry["retryable"].as_bool() {
            return declared;
        }
        assert_eq!(
            entry["retryable"].as_str(),
            Some("by_status"),
            "retryable is neither a bool nor \"by_status\""
        );
        entry["retryable_statuses"]
            .as_array()
            .expect("retryable_statuses")
            .iter()
            .any(|s| s.as_u64() == Some(u64::from(status)))
    }

    #[test]
    fn the_catalog_is_not_empty() {
        // Every assertion below is vacuously true against an empty list, which is exactly how a
        // guard like this stops guarding without failing.
        assert!(!catalog().is_empty());
    }

    #[test]
    fn every_catalogued_error_maps_to_a_kind_with_the_catalogued_retryability() {
        let mut problems = Vec::new();

        for entry in catalog() {
            let suffix = entry["suffix"].as_str().expect("suffix");

            for status in probe_statuses(&entry) {
                let body = serde_json::json!({
                    "type": format!("https://gateway.example/errors/{suffix}"),
                    "title": suffix,
                    "detail": "something went wrong",
                });
                let error = api_error_from_body(status, Some(&body), None, None, "", "");

                // A suffix this build does not know falls back to a status-keyed kind, which is
                // right for an unknown error and wrong for a catalogued one.
                let fell_back = matches!(
                    error.kind,
                    ErrorKind::OtherClientError
                        | ErrorKind::OtherServerError
                        | ErrorKind::Unauthorized
                );
                if fell_back {
                    problems.push(format!(
                        "{suffix} is in spec/errors.json but this SDK maps no kind for it"
                    ));
                    continue;
                }
                let want_retryable = want_retryable(&entry, status);
                if error.retryable() != want_retryable {
                    problems.push(format!(
                        "{suffix} at {status}: retryable is {} here, {want_retryable} in the catalog",
                        error.retryable()
                    ));
                }
            }
        }

        assert!(problems.is_empty(), "{}", problems.join("\n"));
    }
}

#[cfg(test)]
mod correlation {
    use super::api_error_from_body;

    /// An error whose body is not a full problem+json still has to carry its request id.
    ///
    /// Measured 2026-09-25 on `POST /v1/models/{model}/predict`, which forwards a backend's
    /// validation failure verbatim: the body is FastAPI's `{"detail": [...]}` with no
    /// `request_id`, while the header carried one all along.
    #[test]
    fn it_falls_back_to_the_headers() {
        let body = serde_json::json!({"detail": "x"});

        let error = api_error_from_body(422, Some(&body), None, None, "req-header", "trace-header");

        assert_eq!(error.request_id, "req-header");
        assert_eq!(error.trace_id, "trace-header");
    }

    /// The gateway's own envelope echoes the header, so this changes nothing where the contract is
    /// honoured -- the fallback only fires where the body fell short.
    #[test]
    fn the_body_wins_when_it_has_them() {
        let body = serde_json::json!({"request_id": "from-body", "trace_id": "trace-body"});

        let error = api_error_from_body(400, Some(&body), None, None, "from-header", "t-header");

        assert_eq!(error.request_id, "from-body");
        assert_eq!(error.trace_id, "trace-body");
    }

    #[test]
    fn neither_source_invents_one() {
        let error = api_error_from_body(500, None, None, None, "", "");

        assert!(error.request_id.is_empty());
        assert!(error.trace_id.is_empty());
    }
}
