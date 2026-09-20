//! The client, its transport, and the two credential modes.

use std::sync::Mutex;
use std::time::Duration;

use serde_json::Value;

use crate::auth::{Auth, TokenClaims, TokenProvider};
use crate::config::Config;
use crate::error::{api_error_from_body, ApiError, Error, ErrorKind, Result};
use crate::retry::CooldownRegistry;
use crate::types::{RateLimit, ResponseMeta};

pub(crate) const INSTANCE_HEADER: &str = "X-Prometheus-Instance";
pub(crate) const IDEMPOTENCY_HEADER: &str = "Idempotency-Key";

/// The gateway's limit. Checked here to save a round trip: exceeding it is a real error either
/// way, so the only question is whether the caller learns before or after the request.
pub(crate) const MAX_IDEMPOTENCY_KEY_LENGTH: usize = 255;

/// A Prometheus Gateway client. Cheap to clone conceptually; share one per process.
pub struct Client {
    pub(crate) config: Config,
    pub(crate) http: reqwest::Client,
    pub(crate) auth: Auth,
    pub(crate) cooldowns: CooldownRegistry,
    last_rate_limit: Mutex<Option<RateLimit>>,
    rate_limits: Mutex<std::collections::HashMap<String, RateLimit>>,
    pub(crate) catalog: tokio::sync::OnceCell<crate::catalog::ModelList>,
}

/// What one call needs beyond its body: which model, which instance, which idempotency key.
#[derive(Debug, Default, Clone)]
pub(crate) struct CallOptions {
    pub model: String,
    pub instance: String,
    pub idempotency_key: String,
    pub streaming: bool,
}

impl Client {
    /// Builds a client, resolving unset fields from `AXONIUM_*`.
    ///
    /// Asking for both credential modes by name is a contradiction and is refused. Credentials
    /// that merely happen to be in the environment are not: the provider wins and they are
    /// **discarded**, so "this crate never holds a long-lived secret" is a fact about the value
    /// rather than a claim about which branch reads what.
    pub fn new(config: Config) -> Result<Self> {
        Self::build(config, None)
    }

    /// Builds a client in the governed mode, where a host supplies tokens.
    pub fn with_token_provider(config: Config, provider: TokenProvider) -> Result<Self> {
        Self::build(config, Some(provider))
    }

    fn build(config: Config, provider: Option<TokenProvider>) -> Result<Self> {
        let (mut config, credentials_were_explicit) = config.resolve()?;

        if provider.is_some() && credentials_were_explicit {
            return Err(Error::Configuration(
                "both a token provider and explicit credentials were supplied, and they are \
                 mutually exclusive modes; pass one or the other"
                    .into(),
            ));
        }

        let auth = match provider {
            Some(provider) => {
                if !config.client_id.is_empty() || !config.client_secret.is_empty() {
                    config.client_id.clear();
                    config.client_secret.clear();
                    eprintln!(
                        "axonium: credentials found in the environment were discarded; the \
                         supplied token provider is the sole authority in governed mode"
                    );
                }
                Auth::Governed {
                    provider,
                    scopes: Mutex::new(Vec::new()),
                }
            }
            None => {
                if config.client_id.is_empty() || config.client_secret.is_empty() {
                    return Err(Error::Configuration(format!(
                        "no credentials. Set client_id and client_secret (or {p}CLIENT_ID and \
                         {p}CLIENT_SECRET) for the autonomous mode, or supply a token provider for \
                         the governed one",
                        p = crate::config::ENV_PREFIX
                    )));
                }
                Auth::autonomous()
            }
        };

        let mut builder = reqwest::Client::builder().connect_timeout(config.timeouts.connect);
        if !config.ca_bundle.is_empty() {
            let pem = std::fs::read(&config.ca_bundle).map_err(|e| {
                Error::Configuration(format!(
                    "could not read ca_bundle {}: {e}",
                    config.ca_bundle
                ))
            })?;
            let cert = reqwest::Certificate::from_pem(&pem).map_err(|e| {
                Error::Configuration(format!(
                    "ca_bundle {} contained no usable certificate: {e}",
                    config.ca_bundle
                ))
            })?;
            builder = builder.add_root_certificate(cert);
        }
        let http = builder
            .build()
            .map_err(|e| Error::Configuration(format!("could not build the HTTP client: {e}")))?;

        Ok(Self {
            config,
            http,
            auth,
            cooldowns: CooldownRegistry::default(),
            last_rate_limit: Mutex::new(None),
            rate_limits: Mutex::new(std::collections::HashMap::new()),
            catalog: tokio::sync::OnceCell::new(),
        })
    }

    /// The resolved configuration. In governed mode the credential fields are empty, which is the
    /// point rather than an omission.
    pub fn config(&self) -> &Config {
        &self.config
    }

    /// What the token in use says about itself. Empty in governed mode: this crate holds none
    /// there, and describing one it saw a moment ago would describe something already replaced.
    pub fn token_claims(&self) -> TokenClaims {
        self.auth.claims()
    }

    /// The rate-limit budget from the most recent response that carried one, so a caller can slow
    /// down before a 429 rather than only react to one.
    pub fn last_rate_limit(&self) -> Option<RateLimit> {
        self.last_rate_limit.lock().unwrap().clone()
    }

    /// The most recent budget seen for each scope, keyed by [`RateLimit::scope`].
    ///
    /// [`Self::last_rate_limit`] answers "what did the call I just made report", which stopped
    /// being the same question as "how much of my embeddings budget is left" once the endpoints
    /// gained separate budgets: a chat call overwrites it with a number from another bucket, and
    /// the numbers themselves do not say so. Use this to ask about a particular budget.
    ///
    /// A snapshot whose scope the gateway did not report is not indexed here, because it cannot be
    /// attributed to a bucket. It is still visible through [`Self::last_rate_limit`].
    #[must_use]
    pub fn rate_limits(&self) -> std::collections::HashMap<String, RateLimit> {
        self.rate_limits.lock().unwrap().clone()
    }

    pub(crate) fn cooldown_key(&self, model: &str) -> String {
        format!(
            "{}|{}",
            self.config.gateway_base_url,
            if model.is_empty() { "-" } else { model }
        )
    }

    /// Sends one logical call, retrying only where the platform says no generation occurred.
    pub(crate) async fn send(
        &self,
        path: &str,
        body: Option<&Value>,
        opts: &CallOptions,
    ) -> Result<(reqwest::Response, ResponseMeta)> {
        if opts.idempotency_key.len() > MAX_IDEMPOTENCY_KEY_LENGTH {
            return Err(Error::InvalidRequest(format!(
                "idempotency_key is {} characters; the gateway accepts at most {}",
                opts.idempotency_key.len(),
                MAX_IDEMPOTENCY_KEY_LENGTH
            )));
        }

        let key = self.cooldown_key(&opts.model);
        let waiting = self.cooldowns.remaining(&key);
        if !waiting.is_zero() {
            return Err(Error::Api(Box::new(ApiError {
                status: 503,
                kind: ErrorKind::BackendUnavailable,
                type_suffix: "backend-unavailable".into(),
                detail: format!(
                    "the gateway reported this backend as unavailable and asked to wait; {:?} of that wait remains",
                    waiting
                ),
                hint: "Refused locally, without a request, to honour the wait the gateway supplied.".into(),
                retry_after: Some(waiting.as_secs_f64()),
                ..Default::default()
            })));
        }

        let op = crate::observability::Operation::start(
            crate::observability::operation_name(path),
            &opts.model,
        );
        let method = if body.is_some() { "POST" } else { "GET" };

        let mut attempt = 1u32;
        let mut waited = std::time::Duration::ZERO;
        loop {
            let started = std::time::Instant::now();
            let outcome = self.attempt(path, body, opts).await;
            record(&op, method, path, opts, attempt, started, &outcome);

            match outcome {
                Ok(mut pair) => {
                    self.cooldowns.clear(&key);
                    // What the caller needs to explain their own wall clock, carried on the answer
                    // rather than left in a trace event they may never have subscribed to.
                    pair.1.waited_for = waited;
                    pair.1.attempts = attempt;
                    op.record_response(&pair.1);
                    return Ok(pair);
                }
                Err(Error::Api(api)) => {
                    self.cooldowns.note(&key, &api);
                    match self.config.retry.delay_for(&api, attempt) {
                        Some(delay) => {
                            op.record_retry_wait(
                                &opts.model,
                                api.status,
                                attempt,
                                &api.type_suffix,
                                delay,
                            );
                            tokio::time::sleep(delay).await;
                            waited += delay;
                            attempt += 1;
                        }
                        None => return Err(Error::Api(api)),
                    }
                }
                // A client-side timeout is retried only under an idempotency key: without one the
                // backend is probably still generating and a retry is a second billable
                // generation. With one, the repeat returns the stored result instead.
                Err(Error::Timeout(message)) => {
                    if opts.idempotency_key.is_empty() || attempt >= self.config.retry.max_attempts
                    {
                        return Err(Error::Timeout(message));
                    }
                    let backoff = self.config.retry.backoff(attempt);
                    op.record_retry_wait(&opts.model, 0, attempt, "", backoff);
                    tokio::time::sleep(backoff).await;
                    waited += backoff;
                    attempt += 1;
                }
                Err(other) => return Err(other),
            }
        }
    }

    async fn attempt(
        &self,
        path: &str,
        body: Option<&Value>,
        opts: &CallOptions,
    ) -> Result<(reqwest::Response, ResponseMeta)> {
        let token = self.auth.token(&self.config, &self.http, None).await?;
        let response = self.dispatch(path, body, opts, &token).await?;

        // Reactive fallback for a token revoked mid-flight, or an expiry refresh-ahead missed.
        // Replayed once, and only once: a second 401 means the credential itself is the problem.
        let response = if response.status() == reqwest::StatusCode::UNAUTHORIZED {
            let fresh = self
                .auth
                .token(&self.config, &self.http, Some(&token))
                .await?;
            self.dispatch(path, body, opts, &fresh).await?
        } else {
            response
        };

        let meta = ResponseMeta::from_headers(response.headers());
        if let Some(rl) = meta.rate_limit.clone() {
            if let Some(scope) = rl.scope.clone() {
                self.rate_limits.lock().unwrap().insert(scope, rl.clone());
            }
            *self.last_rate_limit.lock().unwrap() = Some(rl);
        }

        if response.status().is_client_error() || response.status().is_server_error() {
            let status = response.status().as_u16();
            let retry_after = retry_after_seconds(response.headers());
            let parsed: Option<Value> = response.json().await.ok();
            // The 429 omits X-RateLimit-Scope and puts it in the body instead, so the headers alone
            // would leave the one error that names a budget unable to say which.
            let rate_limit = meta
                .rate_limit
                .clone()
                .map(|rl| rl.with_scope_from(parsed.as_ref()));
            let mut api = api_error_from_body(status, parsed.as_ref(), retry_after, rate_limit);
            self.explain_forbidden(&mut api);
            return Err(Error::Api(Box::new(api)));
        }
        Ok((response, meta))
    }

    async fn dispatch(
        &self,
        path: &str,
        body: Option<&Value>,
        opts: &CallOptions,
        token: &str,
    ) -> Result<reqwest::Response> {
        let url = format!("{}{}", self.config.gateway_base_url, path);
        let mut request = match body {
            Some(json) => self.http.post(&url).json(json),
            None => self.http.get(&url),
        }
        .bearer_auth(token)
        .header(
            reqwest::header::ACCEPT,
            if opts.streaming {
                "text/event-stream"
            } else {
                "application/json"
            },
        )
        .header(
            reqwest::header::USER_AGENT,
            concat!("axonium-rust/", env!("CARGO_PKG_VERSION")),
        );

        if !opts.instance.is_empty() {
            request = request.header(INSTANCE_HEADER, &opts.instance);
        }
        if !opts.idempotency_key.is_empty() {
            request = request.header(IDEMPOTENCY_HEADER, &opts.idempotency_key);
        }
        // Streaming gets no whole-request timeout: a long generation is not a stalled one.
        if !opts.streaming {
            request = request.timeout(self.config.timeouts.request);
        }

        request.send().await.map_err(|e| {
            if e.is_timeout() {
                Error::Timeout(format!(
                    "{e}. The backend may still be generating, so retrying without an \
                     idempotency key would start a second billable generation rather than \
                     resuming this one"
                ))
            } else {
                Error::Transport(e.to_string())
            }
        })
    }

    /// Adds the scopes actually held to a 403, so the error says what is missing rather than only
    /// that access was refused. Access is deny-by-default and granted per model, and streaming
    /// takes a different scope from non-streaming.
    fn explain_forbidden(&self, error: &mut ApiError) {
        if error.status != 403 {
            return;
        }
        let held = self.auth.scopes();
        if !held.is_empty() {
            error.hint = format!("Scope check: the token holds {}.", held.join(" "));
        }
    }
}

/// Emits one record per attempt, with the status taken from the response or from a typed error so
/// a failure is reported with the code that caused it rather than as a bare failure.
fn record(
    op: &crate::observability::Operation,
    method: &str,
    path: &str,
    opts: &CallOptions,
    attempt: u32,
    started: std::time::Instant,
    outcome: &Result<(reqwest::Response, ResponseMeta)>,
) {
    let blank = ResponseMeta::default();
    let (status, meta, error) = match outcome {
        Ok((response, meta)) => (response.status().as_u16(), meta, None),
        Err(Error::Api(api)) => (api.status, &blank, Some(api.to_string())),
        Err(other) => (0, &blank, Some(other.to_string())),
    };
    op.record_attempt(&crate::observability::AttemptRecord {
        method,
        path,
        model: &opts.model,
        status,
        attempt,
        elapsed: started.elapsed(),
        meta,
        error: error.as_deref(),
    });
}

/// Reads `Retry-After`, which may be delta-seconds or an HTTP date.
///
/// Always a finite, non-negative number or `None`: a caller passes this straight to a sleep, where
/// a negative value is meaningless and an infinite one never returns.
pub(crate) fn retry_after_seconds(headers: &reqwest::header::HeaderMap) -> Option<f64> {
    let raw = headers.get("retry-after")?.to_str().ok()?;
    if let Ok(seconds) = raw.parse::<f64>() {
        return sane(seconds);
    }
    let target = httpdate::parse_http_date(raw).ok()?;
    // Both sides come from the server, so the wait is unaffected by any difference with our clock.
    let reference = httpdate::parse_http_date(headers.get("date")?.to_str().ok()?).ok()?;
    sane(
        target
            .duration_since(reference)
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0),
    )
}

fn sane(seconds: f64) -> Option<f64> {
    seconds.is_finite().then(|| seconds.max(0.0))
}

impl Default for ApiError {
    fn default() -> Self {
        Self {
            status: 0,
            kind: ErrorKind::OtherClientError,
            type_suffix: String::new(),
            title: String::new(),
            detail: String::new(),
            instance: String::new(),
            request_id: String::new(),
            trace_id: String::new(),
            retry_after: None,
            hint: String::new(),
            rate_limit: None,
            raw: Default::default(),
        }
    }
}

/// Unused today, kept so the timeout constant has one home.
pub(crate) const _STREAM_IDLE: Duration = Duration::from_secs(180);

/// Debug for a client shows how it is configured and which credential mode it is in, never a
/// credential: `Config`'s own Debug redacts the secret, and the governed mode holds none at all.
impl std::fmt::Debug for Client {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Client")
            .field("config", &self.config)
            .field(
                "mode",
                &match self.auth {
                    Auth::Autonomous { .. } => "autonomous",
                    Auth::Governed { .. } => "governed",
                },
            )
            .finish()
    }
}
