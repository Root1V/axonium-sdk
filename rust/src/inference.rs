//! Embeddings and image generation.

use serde::Deserialize;
use serde_json::{json, Value};

use crate::client::{CallOptions, Client};
use crate::error::{Error, Result};
use crate::types::{ResponseMeta, Usage};

/// A request for vector embeddings.
#[derive(Debug, Clone, Default)]
pub struct EmbeddingRequest {
    pub model: String,
    pub input: Vec<String>,
    pub instance: String,
    pub idempotency_key: String,
}

/// One vector.
#[derive(Debug, Clone, Deserialize)]
pub struct Embedding {
    #[serde(default)]
    pub index: u32,
    #[serde(default)]
    pub embedding: Vec<f64>,
}

/// An embeddings response.
///
/// `usage` carries no completion count: there is no generation phase, so the field is absent
/// rather than zero -- "not applicable", not "measured and free".
#[derive(Debug, Clone, Deserialize)]
pub struct EmbeddingList {
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub data: Vec<Embedding>,
    #[serde(default)]
    pub usage: Option<Usage>,
    #[serde(skip)]
    pub raw: Value,
    #[serde(skip)]
    pub meta: ResponseMeta,
}

/// A request for generated images.
#[derive(Debug, Clone, Default)]
pub struct ImageRequest {
    pub model: String,
    pub prompt: String,
    pub n: Option<u32>,
    pub size: String,
    pub instance: String,
    pub idempotency_key: String,
}

/// One generated image, returned as base64 rather than a URL.
#[derive(Debug, Clone, Deserialize)]
pub struct Image {
    #[serde(default)]
    pub b64_json: String,
    #[serde(default)]
    pub revised_prompt: Option<String>,
}

impl Image {
    /// Decodes the image.
    pub fn bytes(&self) -> Result<Vec<u8>> {
        use base64::Engine as _;
        base64::engine::general_purpose::STANDARD
            .decode(&self.b64_json)
            .map_err(|e| Error::Transport(format!("the image payload was not valid base64: {e}")))
    }
}

/// An image generation response.
#[derive(Debug, Clone, Deserialize)]
pub struct ImageList {
    #[serde(default)]
    pub output_format: String,
    #[serde(default)]
    pub data: Vec<Image>,
    #[serde(skip)]
    pub raw: Value,
    #[serde(skip)]
    pub meta: ResponseMeta,
}

impl Client {
    /// Returns embeddings for the given inputs.
    pub async fn embeddings(&self, request: &EmbeddingRequest) -> Result<EmbeddingList> {
        let mut problems = Vec::new();
        if request.model.trim().is_empty() {
            problems.push("model is required");
        }
        if request.input.is_empty() {
            problems.push("input must not be empty");
        }
        if !problems.is_empty() {
            return Err(Error::InvalidRequest(problems.join("; ")));
        }
        self.check_modality(&request.model, crate::catalog::EMBEDDING_MODALITIES)
            .await?;

        let body = json!({ "model": request.model, "input": request.input });
        let opts = CallOptions {
            model: request.model.clone(),
            instance: request.instance.clone(),
            idempotency_key: request.idempotency_key.clone(),
            streaming: false,
        };
        let (response, meta) = self.send("/v1/embeddings", Some(&body), &opts).await?;
        let raw: Value = response.json().await.map_err(|e| {
            Error::Transport(format!(
                "the gateway returned embeddings this SDK could not parse: {e}"
            ))
        })?;
        let mut list: EmbeddingList = serde_json::from_value(raw.clone()).map_err(|e| {
            Error::Transport(format!(
                "the gateway returned embeddings this SDK could not parse: {e}"
            ))
        })?;
        if let Some(usage) = list.usage.as_mut() {
            usage.absorb_details(raw.get("usage").unwrap_or(&Value::Null));
        }
        list.raw = raw;
        list.meta = meta;
        Ok(list)
    }

    /// Creates images from a prompt. Generation legitimately takes minutes, so the default request
    /// timeout is long.
    pub async fn images(&self, request: &ImageRequest) -> Result<ImageList> {
        let mut problems = Vec::new();
        if request.model.trim().is_empty() {
            problems.push("model is required");
        }
        if request.prompt.trim().is_empty() {
            problems.push("prompt is required");
        }
        if !problems.is_empty() {
            return Err(Error::InvalidRequest(problems.join("; ")));
        }
        self.check_modality(&request.model, crate::catalog::IMAGE_MODALITIES)
            .await?;

        let mut body = json!({ "model": request.model, "prompt": request.prompt });
        let map = body.as_object_mut().unwrap();
        if let Some(n) = request.n {
            map.insert("n".into(), json!(n));
        }
        if !request.size.is_empty() {
            map.insert("size".into(), json!(request.size));
        }
        let opts = CallOptions {
            model: request.model.clone(),
            instance: request.instance.clone(),
            idempotency_key: request.idempotency_key.clone(),
            streaming: false,
        };
        let (response, meta) = self
            .send("/v1/images/generations", Some(&body), &opts)
            .await?;
        let raw: Value = response.json().await.map_err(|e| {
            Error::Transport(format!(
                "the gateway returned images this SDK could not parse: {e}"
            ))
        })?;
        let mut list: ImageList = serde_json::from_value(raw.clone()).map_err(|e| {
            Error::Transport(format!(
                "the gateway returned images this SDK could not parse: {e}"
            ))
        })?;
        list.raw = raw;
        list.meta = meta;
        Ok(list)
    }
}
