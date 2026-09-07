//! Rust SDK for the Prometheus Gateway inference API.
//!
//! This crate is a placeholder. The Python SDK under `python/` is being built first as the
//! reference implementation; this crate will be implemented against the same contract and
//! validated with the shared fixtures in `spec/` so that both SDKs behave identically.
//!
//! Planned surface:
//!
//! ```ignore
//! let client = Axonium::builder()
//!     .auth_base_url(auth_url)
//!     .gateway_base_url(gateway_url)
//!     .credentials(client_id, client_secret)
//!     .build()?;
//!
//! let models = client.models().list().await?;
//! let resp = client.chat().completions().create(req).await?;
//! let mut stream = client.chat().completions().stream(req).await?;
//! ```
//!
//! Implementation notes carried over from the specification:
//!
//! - `reqwest` over `tokio`; reqwest does not parse SSE natively, so streaming needs a manual
//!   byte-stream parser or a crate such as `eventsource-stream`.
//! - Token cache behind a `tokio::sync::RwLock`, refreshed lazily on the read path with
//!   double-checked locking to avoid a refresh stampede under concurrent requests. The TTL
//!   always comes from the token response's `expires_in`, never a hardcoded constant.
//! - Errors are a typed enum over the gateway's RFC 9457 problem-details envelope, distinct from
//!   the RFC 6749 error shape returned by the OAuth2 token endpoint.

#![forbid(unsafe_code)]

/// Crate version, mirroring `Cargo.toml`.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
