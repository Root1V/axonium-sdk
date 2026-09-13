//! The model catalog, and the optional modality preflight.

use serde::Deserialize;
use serde_json::Value;

use crate::client::{CallOptions, Client};
use crate::error::{Error, Result};
use crate::types::ResponseMeta;

/// Modalities each endpoint can serve. `text` and `vision` are BOTH served on the chat endpoint,
/// so the mapping to a consumer's own vocabulary is not one to one.
pub(crate) const CHAT_MODALITIES: &[&str] = &["text", "vision"];
pub(crate) const EMBEDDING_MODALITIES: &[&str] = &["embedding"];
pub(crate) const IMAGE_MODALITIES: &[&str] = &["image"];

/// Every modality this build can place. Anything outside it is newer than this build and is not
/// ours to reject -- the platform added two in a single week, and a guard rail that starts
/// refusing valid requests each time would be worse than no guard rail.
const KNOWN_MODALITIES: &[&str] = &["text", "vision", "embedding", "image"];

/// One entry of the catalog.
#[derive(Debug, Clone, Deserialize)]
pub struct Model {
    pub id: String,
    #[serde(default)]
    pub modality: String,
    /// `None` for image models, which have no context window at all. A zero would read as a window
    /// of zero and make a `prompt_tokens < context_length` check reject every image request.
    ///
    /// For a model served by several instances this is the **smallest** of them, so a request that
    /// fits the advertised number fits whichever instance serves it.
    #[serde(default)]
    pub context_length: Option<u64>,
    /// How many instances currently serve this model. Informational: an instance is never
    /// addressed through `model`.
    #[serde(default)]
    pub served_by: Option<u32>,
    #[serde(default)]
    pub family: String,
    #[serde(default)]
    pub quantization: String,
    #[serde(default)]
    pub owned_by: String,
}

/// A catalog response.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct ModelList {
    #[serde(default)]
    pub data: Vec<Model>,
    #[serde(skip)]
    pub raw: Value,
    #[serde(skip)]
    pub meta: ResponseMeta,
}

impl ModelList {
    pub fn ids(&self) -> Vec<&str> {
        self.data.iter().map(|m| m.id.as_str()).collect()
    }

    pub fn find(&self, id: &str) -> Option<&Model> {
        self.data.iter().find(|m| m.id == id)
    }
}

impl Client {
    /// The full public catalog. Needs no authentication, so it works before any credential is
    /// configured -- useful for checking connectivity.
    pub async fn models(&self) -> Result<ModelList> {
        self.catalog_request("/v1/models").await
    }

    /// The models this token is actually allowed to call.
    ///
    /// Access is deny-by-default and granted per model, so the public catalog does not answer
    /// "what can I call": this does.
    pub async fn models_mine(&self) -> Result<ModelList> {
        self.catalog_request("/v1/models/mine").await
    }

    async fn catalog_request(&self, path: &str) -> Result<ModelList> {
        let (response, meta) = self.send(path, None, &CallOptions::default()).await?;
        let raw: Value = response.json().await.map_err(|e| {
            Error::Transport(format!(
                "the gateway returned a catalog this SDK could not parse: {e}"
            ))
        })?;
        let mut list: ModelList = serde_json::from_value(raw.clone()).map_err(|e| {
            Error::Transport(format!(
                "the gateway returned a catalog this SDK could not parse: {e}"
            ))
        })?;
        list.raw = raw;
        list.meta = meta;
        Ok(list)
    }

    /// Verifies a model's modality against the endpoint before sending.
    ///
    /// The gateway used to accept chat on an embedding model and answer `200` with degenerate,
    /// billable output; it now rejects it, so this is a typo-catcher and a saved round trip rather
    /// than a correctness guard. Off unless `verify_modality` is set, because it costs one catalog
    /// request and the SDK otherwise makes no request a caller did not ask for.
    ///
    /// If the catalog cannot be loaded the check is skipped: a guard rail must not become a new
    /// way for inference to fail.
    pub(crate) async fn check_modality(&self, model: &str, accepted: &[&str]) -> Result<()> {
        if !self.config.verify_modality {
            return Ok(());
        }
        let Ok(catalog) = self
            .catalog
            .get_or_try_init(|| async { self.models().await })
            .await
        else {
            return Ok(());
        };

        let Some(found) = catalog.find(model) else {
            return Err(Error::InvalidRequest(format!(
                "model {model:?} is not in the gateway's catalog; known models: {}",
                catalog.ids().join(", ")
            )));
        };
        if found.modality.is_empty() || !KNOWN_MODALITIES.contains(&found.modality.as_str()) {
            return Ok(());
        }
        if accepted.contains(&found.modality.as_str()) {
            return Ok(());
        }
        Err(Error::InvalidRequest(format!(
            "model {model:?} has modality {:?}, which this endpoint does not accept (it takes {})",
            found.modality,
            accepted.join(" or ")
        )))
    }
}
