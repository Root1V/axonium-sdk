//! Configuration.
//!
//! No host, port or certificate is baked into this crate. Base URLs, credentials and TLS trust are
//! deployment-specific and always supplied by the caller, either through [`Config`] or through
//! `AXONIUM_*` environment variables. A missing required setting fails at construction, naming
//! both the field and the variable that can supply it.

use std::time::Duration;

use crate::error::{Error, Result};

pub(crate) const ENV_PREFIX: &str = "AXONIUM_";

/// Where the official Prometheus platform lives, used when a caller supplies no URL of their own.
///
/// This is what makes credentials the only thing most callers need to configure: an official SDK
/// should point at the official platform, and asking every consumer to repeat the same two URLs is
/// friction for nothing.
///
/// **These are provisional.** The platform is not yet on its cloud host, so today they address a
/// local deployment. When it moves, these two constants change and a consumer who upgrades follows
/// automatically -- which is why they live here, in one place, rather than spread through examples
/// and documentation.
///
/// Two consequences worth knowing. A consumer who *pins* an old version keeps pointing at the old
/// address after the migration, so the release that changes them will say so loudly. And because
/// the default is a loopback address, anyone running this without the platform on their own
/// machine reaches their own localhost -- normally a refused connection, which is clear enough,
/// but set `AXONIUM_AUTH_BASE_URL` and `AXONIUM_GATEWAY_BASE_URL` for any deployment that is not
/// this one.
pub const DEFAULT_GATEWAY_BASE_URL: &str = "http://127.0.0.1:8020";

/// Where tokens come from, which is now **the same host as the gateway**.
///
/// The platform used to run a separate auth-service on its own address, so every consumer
/// configured two hosts. The gateway issues tokens itself now, at the same `/oauth2/token` path,
/// so there is one address to know instead of two -- and a deployment can stop exposing the
/// service that holds the credentials, which was a second public surface offering nothing the
/// gateway cannot.
pub const DEFAULT_AUTH_BASE_URL: &str = DEFAULT_GATEWAY_BASE_URL;

/// The gateway's own backend-forwarding timeout for non-streaming requests. A client-side timeout
/// below this is a known failure mode: the backend keeps computing after the client gives up.
const GATEWAY_NON_STREAMING_TIMEOUT: Duration = Duration::from_secs(600);

/// Per-phase timeouts.
///
/// Defaults follow the gateway's own limits. Image generation legitimately takes minutes, so the
/// non-streaming timeout is deliberately long.
#[derive(Debug, Clone, Copy)]
pub struct Timeouts {
    pub connect: Duration,
    /// Bounds a whole non-streaming call.
    pub request: Duration,
    /// Bounds a streaming call, kept above the gateway's own 120s backend read timeout.
    pub stream: Duration,
    /// Bounds a token request. Deliberately short: the auth-service does no inference, so it must
    /// not inherit the long timeout image generation needs.
    pub auth: Duration,
}

impl Default for Timeouts {
    fn default() -> Self {
        Self {
            connect: Duration::from_secs(10),
            request: GATEWAY_NON_STREAMING_TIMEOUT,
            stream: Duration::from_secs(180),
            auth: Duration::from_secs(30),
        }
    }
}

/// Resolved client configuration. Unset fields fall back to `AXONIUM_*`.
#[derive(Clone, Default)]
pub struct Config {
    /// Base URL of the auth-service. Defaults to [`DEFAULT_AUTH_BASE_URL`]; override it for a
    /// self-hosted deployment.
    pub auth_base_url: String,
    /// Base URL of the gateway serving the `/v1/` inference API. Defaults to
    /// [`DEFAULT_GATEWAY_BASE_URL`].
    pub gateway_base_url: String,
    /// Required in autonomous mode. Absent in governed mode, where a caller-supplied token
    /// provider is the authority and this crate never sees a secret.
    pub client_id: String,
    pub client_secret: String,
    /// Optional space-separated scope request. The effective scope is the intersection with what
    /// the account is allowed, and asking for one it lacks is an error rather than a downgrade --
    /// so always read the granted scope back from the token.
    pub scope: String,
    /// Path to a CA bundle, for deployments fronted by a self-signed certificate.
    pub ca_bundle: String,
    /// Check a model's modality against the endpoint before sending. Costs one catalog request per
    /// client, which is why it is opt-in.
    pub verify_modality: bool,
    /// Refresh the token once this fraction of its lifetime has elapsed.
    pub refresh_ahead_ratio: f64,
    /// ...or once less than this remains, whichever comes first.
    pub refresh_ahead_min: Duration,
    pub timeouts: Timeouts,
    pub retry: crate::retry::RetryPolicy,
}

fn env(name: &str) -> String {
    std::env::var(format!("{ENV_PREFIX}{name}")).unwrap_or_default()
}

impl Config {
    /// Fills unset fields from the environment and validates the result.
    ///
    /// Returns whether the credentials were named on the struct rather than read from the
    /// environment. That is an observation about how this config was built, not a setting, so it
    /// is returned rather than stored: a private field on a public struct would break
    /// `..Default::default()` for every consumer.
    pub(crate) fn resolve(mut self) -> Result<(Self, bool)> {
        // Whether credentials were named explicitly, as opposed to merely being in the
        // environment. Asking for both modes by name is a contradiction; credentials that happen
        // to be in the environment are discarded instead, because refusing to start there would
        // make the governed mode the hardest one to deploy.
        let explicit = !self.client_id.is_empty() || !self.client_secret.is_empty();

        // Precedence: the field if set, then the environment, then the fallback.
        //
        // Sequential rather than a loop, because the second depends on the first: the token host
        // falls back to the *resolved* gateway rather than to a constant, so a self-hosted
        // deployment that sets one address does not silently ask the official platform for its
        // tokens. Nothing errors in that shape, which is what makes the order load-bearing.
        fn resolve_url(field: &mut String, var: &str, fallback: &str) {
            if field.is_empty() {
                let from_env = env(var);
                *field = if from_env.is_empty() {
                    fallback.to_string()
                } else {
                    from_env
                };
            }
        }

        resolve_url(
            &mut self.gateway_base_url,
            "GATEWAY_BASE_URL",
            DEFAULT_GATEWAY_BASE_URL,
        );
        // The token host is deliberately left alone here. It is resolved after the gateway has
        // been validated, below, so that one malformed gateway URL produces one complaint rather
        // than two -- the second of which would name a variable the caller never set.
        resolve_url(&mut self.auth_base_url, "AUTH_BASE_URL", "");

        for (field, var) in [
            (&mut self.client_id, "CLIENT_ID"),
            (&mut self.client_secret, "CLIENT_SECRET"),
            (&mut self.scope, "SCOPE"),
            (&mut self.ca_bundle, "CA_BUNDLE"),
        ] {
            if field.is_empty() {
                *field = env(var);
            }
        }
        if !self.verify_modality {
            self.verify_modality = matches!(env("VERIFY_MODALITY").as_str(), "true" | "1" | "yes");
        }
        if self.refresh_ahead_ratio == 0.0 {
            self.refresh_ahead_ratio = 0.8;
        }
        if self.refresh_ahead_min.is_zero() {
            self.refresh_ahead_min = Duration::from_secs(30);
        }
        if self.timeouts.request.is_zero() {
            self.timeouts = Timeouts::default();
        }
        if self.retry.max_attempts == 0 {
            self.retry = crate::retry::RetryPolicy::default();
        }

        let mut problems = Vec::new();
        normalise(
            &mut self.gateway_base_url,
            "gateway_base_url",
            "GATEWAY_BASE_URL",
            &mut problems,
        );
        if self.auth_base_url.is_empty() {
            // Follows the *normalised* gateway, so it inherits a value already known to be a URL
            // and needs no second check. A self-hosted deployment that sets one address therefore
            // does not silently ask the official platform for its tokens.
            self.auth_base_url = self.gateway_base_url.clone();
        } else {
            normalise(
                &mut self.auth_base_url,
                "auth_base_url",
                "AUTH_BASE_URL",
                &mut problems,
            );
        }
        if self.refresh_ahead_ratio <= 0.0 || self.refresh_ahead_ratio > 1.0 {
            problems.push("refresh_ahead_ratio must be within (0, 1]".to_string());
        }
        if !problems.is_empty() {
            return Err(Error::Configuration(problems.join("; ")));
        }

        Ok((self, explicit))
    }

    /// The requested scope as a list, empty when none was requested.
    pub fn scopes(&self) -> Vec<&str> {
        self.scope.split_whitespace().collect()
    }
}

fn normalise(value: &mut String, field: &str, var: &str, problems: &mut Vec<String>) {
    if value.is_empty() {
        problems.push(format!(
            "{field} is required (set it or export {ENV_PREFIX}{var})"
        ));
        return;
    }
    if !value.starts_with("http://") && !value.starts_with("https://") {
        problems.push(format!(
            "{field} must start with http:// or https:// (from the field or {ENV_PREFIX}{var})"
        ));
        return;
    }
    *value = value.trim_end_matches('/').to_string();
}

/// Written by hand rather than derived, because a derived one prints `client_secret` in full --
/// which is what a panic message, a tracing span, or a stray `dbg!` would then carry into
/// whatever collects them. Redacting it here means the secret cannot leak through the one trait
/// everybody reaches for while debugging.
impl std::fmt::Debug for Config {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Config")
            .field("auth_base_url", &self.auth_base_url)
            .field("gateway_base_url", &self.gateway_base_url)
            .field("client_id", &self.client_id)
            .field(
                "client_secret",
                &if self.client_secret.is_empty() {
                    "(unset)"
                } else {
                    "(redacted)"
                },
            )
            .field("scope", &self.scope)
            .field("ca_bundle", &self.ca_bundle)
            .field("verify_modality", &self.verify_modality)
            .field("refresh_ahead_ratio", &self.refresh_ahead_ratio)
            .field("refresh_ahead_min", &self.refresh_ahead_min)
            .field("timeouts", &self.timeouts)
            .field("retry", &self.retry)
            .finish()
    }
}
