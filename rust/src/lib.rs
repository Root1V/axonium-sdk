//! Rust SDK for the Prometheus inference platform.
//!
//! The SDK is **pure transport**. It speaks the gateway's contract faithfully -- authentication,
//! retries that cannot double-bill, typed errors, streaming with real cancellation -- and does not
//! reshape responses into a vocabulary of its own. Normalisation belongs above it, so there is one
//! implementation of that vocabulary rather than one per language SDK.
//!
//! No host, port or certificate is baked in: every deployment supplies its own, by field or by
//! `AXONIUM_*` environment variable.
//!
//! ```no_run
//! # async fn demo() -> axonium::Result<()> {
//! use axonium::{ChatRequest, Client, Config, Message};
//!
//! let client = Client::new(Config {
//!     client_id: "...".into(),
//!     client_secret: "...".into(),
//!     ..Default::default()
//! })?;
//!
//! let completion = client
//!     .chat(&ChatRequest {
//!         model: "qwen3-0.6b".into(),
//!         messages: vec![Message::text("user", "Hello")],
//!         ..Default::default()
//!     })
//!     .await?;
//! println!("{}", completion.content());
//! # Ok(())
//! # }
//! ```

mod auth;
mod catalog;
mod chat;
mod client;
mod config;
mod error;
mod inference;
mod observability;
mod rerank;
mod retry;
mod stream;
mod types;
mod usage;

pub use auth::{decode_claims, TokenClaims, TokenProvider};
pub use catalog::{Model, ModelList};
pub use chat::{ChatCompletion, ChatRequest, Choice, FunctionCall, Message, ToolCall};
pub use client::Client;
pub use config::{Config, Timeouts, DEFAULT_GATEWAY_BASE_URL};
pub use error::{ApiError, Error, ErrorKind, Result};
pub use inference::{Embedding, EmbeddingList, EmbeddingRequest, Image, ImageList, ImageRequest};
pub use rerank::{RerankRequest, RerankResponse, RerankResult};
pub use retry::RetryPolicy;
pub use stream::{ChatStream, Chunk};
pub use types::{RateLimit, ResponseMeta, Usage};
pub use usage::RequestUsage;

/// This crate's version.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
