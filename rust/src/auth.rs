//! Authentication, in two permanent and mutually exclusive modes.
//!
//! **Autonomous** -- this crate mints and refreshes its own tokens with OAuth2
//! `client_credentials`. The platform has no refresh-token grant: a new token is obtained by
//! repeating the request, and the TTL is always read from `expires_in`, never assumed.
//!
//! **Governed** -- a host that already owns the credential supplies tokens through a
//! [`TokenProvider`], and this crate never sees a secret. The provider is the sole authority: it
//! owns caching, refresh and rotation, and Axonium does no refresh-ahead of its own, because two
//! caches for one token is how a client ends up sending one its owner already retired.

use std::future::Future;
use std::pin::Pin;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde_json::Value;

use crate::config::Config;
use crate::error::{oauth_error_from_body, Error, Result};

/// The longest TTL an operator can configure. A larger `expires_in` is not something the platform
/// can legitimately issue, so it is clamped rather than trusted: believing it would mean never
/// refreshing proactively and falling back to the reactive 401 path forever.
const MAX_PLAUSIBLE_TTL: Duration = Duration::from_secs(24 * 60 * 60);

/// Supplies access tokens in the governed mode.
///
/// Called with the token the gateway just refused, or `None` on the first call, and returns one to
/// use. **The rejected token comes back rather than a flag**: with a boolean a provider cannot
/// tell whether two concurrent refreshes concern the same dead token or different ones, so it must
/// mint twice or guess with a time window. Given the token the answer is exact.
pub type TokenProvider = Box<
    dyn Fn(Option<String>) -> Pin<Box<dyn Future<Output = Result<String>> + Send>> + Send + Sync,
>;

/// Claims read out of an access token.
///
/// Decoded **without verifying the signature**, which is fine because nothing here makes an
/// access-control decision: the gateway is the sole authority on what a token may do.
#[derive(Debug, Clone, Default)]
pub struct TokenClaims {
    pub subject: String,
    pub client_name: String,
    pub role: String,
    pub scope: Vec<String>,
    pub expires_at: i64,
    pub issued_at: i64,
}

/// Reads a JWT's payload without verifying it. Malformed input yields empty claims rather than an
/// error: this is introspection for diagnostics, and it must never take a request down.
pub fn decode_claims(token: &str) -> TokenClaims {
    use base64::Engine as _;
    let Some(payload) = token.split('.').nth(1) else {
        return TokenClaims::default();
    };
    let Ok(bytes) =
        base64::engine::general_purpose::URL_SAFE_NO_PAD.decode(payload.trim_end_matches('='))
    else {
        return TokenClaims::default();
    };
    let Ok(Value::Object(map)) = serde_json::from_slice::<Value>(&bytes) else {
        return TokenClaims::default();
    };
    let text = |k: &str| {
        map.get(k)
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string()
    };
    TokenClaims {
        subject: text("sub"),
        client_name: text("client_name"),
        role: text("role"),
        scope: map
            .get("scope")
            .and_then(Value::as_str)
            .map(|s| s.split_whitespace().map(str::to_string).collect())
            .unwrap_or_default(),
        expires_at: map.get("exp").and_then(Value::as_i64).unwrap_or_default(),
        issued_at: map.get("iat").and_then(Value::as_i64).unwrap_or_default(),
    }
}

#[derive(Debug, Clone)]
pub(crate) struct TokenSet {
    access_token: String,
    /// An `Instant`, so the comparison measures elapsed time rather than wall clock: neither a
    /// constant offset against the auth-service nor an NTP step can make a live token look
    /// expired. The *length* of the life still comes from the server.
    expires_at: Instant,
    lifetime: Duration,
    scope: Vec<String>,
}

pub(crate) enum Auth {
    Autonomous {
        state: Mutex<Option<TokenSet>>,
    },
    Governed {
        provider: TokenProvider,
        /// Scopes of the last token applied, kept so a 403 can still be diagnosed. Only the scope
        /// strings, never the token: holding the token would recreate the second cache this mode
        /// exists to avoid, while holding what it granted is metadata.
        scopes: Mutex<Vec<String>>,
    },
}

impl Auth {
    /// A fresh autonomous authenticator. A constructor rather than an exposed field, so the cached
    /// token type stays private to this module.
    pub(crate) fn autonomous() -> Self {
        Self::Autonomous {
            state: Mutex::new(None),
        }
    }

    pub(crate) fn scopes(&self) -> Vec<String> {
        match self {
            Self::Autonomous { state } => state
                .lock()
                .unwrap()
                .as_ref()
                .map(|t| t.scope.clone())
                .unwrap_or_default(),
            Self::Governed { scopes, .. } => scopes.lock().unwrap().clone(),
        }
    }

    pub(crate) fn claims(&self) -> TokenClaims {
        match self {
            Self::Autonomous { state } => state
                .lock()
                .unwrap()
                .as_ref()
                .map(|t| decode_claims(&t.access_token))
                .unwrap_or_default(),
            // Nothing to describe: this mode holds no token, and reporting one seen a moment ago
            // would describe something the provider may already have replaced.
            Self::Governed { .. } => TokenClaims::default(),
        }
    }

    /// The token to use, minting or refreshing if needed.
    pub(crate) async fn token(
        &self,
        config: &Config,
        http: &reqwest::Client,
        rejected: Option<&str>,
    ) -> Result<String> {
        match self {
            Self::Autonomous { state } => {
                if rejected.is_none() {
                    let cached = state.lock().unwrap().clone();
                    if let Some(t) = cached {
                        if !stale(&t, config) {
                            return Ok(t.access_token);
                        }
                    }
                } else {
                    // Another caller may have replaced the token while this request was in flight;
                    // reusing theirs avoids a redundant mint under a concurrent 401 burst.
                    let cached = state.lock().unwrap().clone();
                    if let Some(t) = cached {
                        if Some(t.access_token.as_str()) != rejected {
                            return Ok(t.access_token);
                        }
                    }
                }
                let fresh = fetch(config, http).await?;
                let token = fresh.access_token.clone();
                *state.lock().unwrap() = Some(fresh);
                Ok(token)
            }
            Self::Governed { provider, scopes } => {
                let token = provider(rejected.map(str::to_string)).await?;
                if token.is_empty() {
                    // Left empty it becomes "Authorization: Bearer " and a 401 with no visible
                    // cause at the call site.
                    return Err(Error::AuthTransport(
                        "the token provider returned an empty token".into(),
                    ));
                }
                *scopes.lock().unwrap() = decode_claims(&token).scope;
                Ok(token)
            }
        }
    }
}

fn stale(token: &TokenSet, config: &Config) -> bool {
    let remaining = token.expires_at.saturating_duration_since(Instant::now());
    remaining <= token.lifetime.mul_f64(1.0 - config.refresh_ahead_ratio)
        || remaining < config.refresh_ahead_min
}

async fn fetch(config: &Config, http: &reqwest::Client) -> Result<TokenSet> {
    if config.client_id.is_empty() || config.client_secret.is_empty() {
        return Err(Error::AuthTransport(
            "this client has no credentials; supply client_id and client_secret, or a token provider".into(),
        ));
    }

    let mut form = vec![
        ("grant_type", "client_credentials"),
        ("client_id", config.client_id.as_str()),
        ("client_secret", config.client_secret.as_str()),
    ];
    if !config.scope.is_empty() {
        form.push(("scope", config.scope.as_str()));
    }

    // Captured before the request is sent, so the round trip is charged against the token's life
    // rather than granted as extra margin.
    let issued_at = Instant::now();
    let response = http
        .post(format!("{}/oauth2/token", config.auth_base_url))
        .form(&form)
        .timeout(config.timeouts.auth)
        .send()
        .await
        .map_err(|e| Error::AuthTransport(format!("could not reach the auth-service: {e}")))?;

    let status = response.status().as_u16();
    let headers = response.headers().clone();
    let body: Option<Value> = response.json().await.ok();

    if status != 200 {
        return Err(oauth_error_from_body(status, body.as_ref()));
    }
    let Some(body) = body else {
        return Err(Error::AuthTransport(format!(
            "the auth-service returned a non-JSON {status} response"
        )));
    };

    let access_token = body
        .get("access_token")
        .and_then(Value::as_str)
        .filter(|t| !t.is_empty())
        .ok_or_else(|| {
            Error::AuthTransport("the auth-service response contained no access_token".into())
        })?
        .to_string();

    let expires_in = body
        .get("expires_in")
        .and_then(Value::as_f64)
        .filter(|s| *s > 0.0)
        .ok_or_else(|| {
            Error::AuthTransport(format!(
                "the auth-service returned an unusable expires_in: {:?}",
                body.get("expires_in")
            ))
        })?;

    let lifetime = effective_lifetime(&headers, &access_token, Duration::from_secs_f64(expires_in))
        .min(MAX_PLAUSIBLE_TTL);

    Ok(TokenSet {
        access_token: access_token.clone(),
        expires_at: issued_at + lifetime,
        lifetime,
        // Always the granted scope, never the requested one.
        scope: body
            .get("scope")
            .and_then(Value::as_str)
            .map(|s| s.split_whitespace().map(str::to_string).collect())
            .unwrap_or_default(),
    })
}

/// How long a token is really good for.
///
/// `expires_in` is the server's own answer. When the response `Date` and the token's `exp` are
/// both present, their difference is a second, independent reading of the same lifetime -- and a
/// skew-free one, because both come from the server's clock rather than being compared to ours.
/// The shorter wins: treating a token as expiring sooner only costs an early refresh, whereas
/// treating it as living longer costs a failed request.
pub(crate) fn effective_lifetime(
    headers: &reqwest::header::HeaderMap,
    token: &str,
    expires_in: Duration,
) -> Duration {
    let Some(date) = headers.get("date").and_then(|v| v.to_str().ok()) else {
        return expires_in;
    };
    let exp = decode_claims(token).expires_at;
    if exp == 0 {
        return expires_in;
    }
    let Ok(sent) = httpdate::parse_http_date(date) else {
        return expires_in;
    };
    let Ok(since_epoch) = sent.duration_since(std::time::UNIX_EPOCH) else {
        return expires_in;
    };
    let remaining = exp - since_epoch.as_secs() as i64;
    if remaining <= 0 {
        // Already expired by the server's own reckoning: fail fast rather than spend a request
        // discovering it.
        return Duration::ZERO;
    }
    expires_in.min(Duration::from_secs(remaining as u64))
}
