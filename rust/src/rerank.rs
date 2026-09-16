//! The rerank endpoint.
//!
//! A reranker is a cross-encoder: it scores a query against each document and returns them
//! ordered. It generates nothing, so there are no completion tokens and billing is prompt-only.
//!
//! Worth knowing if you are coming from a chat-based workaround: the whole document set is **one**
//! request rather than one per document, which against a 60 RPM budget is the difference between
//! scoring 50 candidates for 1 unit and for 50.

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::client::{CallOptions, Client};
use crate::error::{Error, Result};
use crate::types::{ResponseMeta, Usage};

/// A rerank request.
#[derive(Debug, Clone, Default, Serialize)]
pub struct RerankRequest {
    pub model: String,
    pub query: String,
    /// The whole set in one request. Must be non-empty: the gateway answers
    /// `400 validation-error` otherwise, and this SDK refuses before the wire.
    pub documents: Vec<String>,
    /// Omit to get every document back.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub top_n: Option<u32>,

    /// Makes a retry safe: a repeat with the same key and body returns the stored result without
    /// reaching a model, recording usage, or counting against the spend cap.
    #[serde(skip)]
    pub idempotency_key: String,
}

/// One document's score against the query.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct RerankResult {
    /// Position in the `documents` slice **you sent**, which is what keeps a reordered result
    /// attributable to its input. Never an index into `results`.
    #[serde(default)]
    pub index: usize,
    /// A probability in `[0, 1]`, computed by the engine rather than reconstructed from
    /// `logprobs`.
    #[serde(default)]
    pub relevance_score: f64,
}

/// Documents scored against a query, best first.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct RerankResponse {
    #[serde(default)]
    pub object: String,
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub results: Vec<RerankResult>,
    /// `total_tokens` equals `prompt_tokens`: a reranker generates nothing.
    #[serde(default)]
    pub usage: Option<Usage>,

    /// The decoded body as received, so a field this SDK does not model stays reachable.
    #[serde(skip)]
    pub raw: Value,
    #[serde(skip)]
    pub meta: ResponseMeta,
}

impl RerankResponse {
    /// The indices of your `documents`, best first.
    ///
    /// The common case is reordering the list you already hold, and doing that through `results`
    /// means remembering that `index` points into the input rather than the output.
    pub fn ranking(&self) -> Vec<usize> {
        self.results.iter().map(|r| r.index).collect()
    }
}

impl Client {
    /// Scores `documents` against `query`, best first.
    ///
    /// The model must have `rerank` modality; a rerank model returns `400 modality-mismatch` from
    /// the chat endpoint, and a chat model returns it from here.
    pub async fn rerank(&self, request: &RerankRequest) -> Result<RerankResponse> {
        if request.model.trim().is_empty() {
            return Err(Error::InvalidRequest("model is required".into()));
        }
        if request.query.trim().is_empty() {
            return Err(Error::InvalidRequest("query is required".into()));
        }
        // The gateway answers 400 validation-error for an empty list. Refusing here saves the
        // round trip and says which field, which the envelope does too but only after the fact.
        if request.documents.is_empty() {
            return Err(Error::InvalidRequest(
                "documents must contain at least one document".into(),
            ));
        }
        self.check_modality(&request.model, &["rerank"]).await?;

        let body = serde_json::to_value(request)
            .map_err(|e| Error::InvalidRequest(format!("could not encode the request: {e}")))?;
        let options = CallOptions {
            model: request.model.clone(),
            idempotency_key: request.idempotency_key.clone(),
            ..Default::default()
        };

        let (response, meta) = self.send("/v1/rerank", Some(&body), &options).await?;
        let raw: Value = response.json().await.map_err(|e| {
            Error::Transport(format!(
                "the gateway returned a rerank response this SDK could not parse: {e}"
            ))
        })?;
        let mut out: RerankResponse = serde_json::from_value(raw.clone()).map_err(|e| {
            Error::Transport(format!(
                "the gateway returned a rerank response this SDK could not parse: {e}"
            ))
        })?;
        if let (Some(usage), Some(raw_usage)) = (out.usage.as_mut(), raw.get("usage")) {
            usage.absorb_details(raw_usage);
        }
        out.raw = raw;
        out.meta = meta;
        Ok(out)
    }
}
