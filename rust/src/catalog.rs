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
pub(crate) const RERANK_MODALITIES: &[&str] = &["rerank"];

/// The three pass-through modalities (spec §3.10). They share one endpoint and one rate-limit
/// budget, and the check is useful in one direction only: the gateway refuses a chat model on
/// `/predict` itself with `400 modality-mismatch`, but forwards a classification model to the chat
/// endpoint without complaint.
pub(crate) const PREDICT_MODALITIES: &[&str] = &["classification", "zero_shot", "typed_decision"];

/// Every endpoint's accepted set, and the only place the list of them lives.
const ENDPOINT_MODALITIES: &[&[&str]] = &[
    CHAT_MODALITIES,
    EMBEDDING_MODALITIES,
    IMAGE_MODALITIES,
    RERANK_MODALITIES,
    PREDICT_MODALITIES,
];

/// Every modality this build can place. Anything outside it is newer than this build and is not
/// ours to reject -- the platform added two in a single week, and a guard rail that starts
/// refusing valid requests each time would be worse than no guard rail.
///
/// **Derived from [`ENDPOINT_MODALITIES`] rather than written out, because it was written out and
/// drifted.** `Rerank::create` has always passed `&["rerank"]`, `"rerank"` was never in the
/// hand-kept list, and [`Client::check_modality`] returns early on a modality it does not know --
/// so the call refused nothing for months, including the case its own doc comment promised to
/// catch. The two lists were one truth in two places and only one was updated when rerank shipped.
fn is_known_modality(modality: &str) -> bool {
    ENDPOINT_MODALITIES
        .iter()
        .any(|set| set.contains(&modality))
}

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
    /// A versioned identifier for the request body's contract, e.g. `"prometheus.chat.v1"` or
    /// `"hf-inference.text-classification.v1"`.
    ///
    /// **This, not [`Self::modality`], is what identifies the shape to send.** It matters most on
    /// the pass-through route, where the body belongs to the engine and two engines serving the
    /// same modality can want different ones: `sst2-clf` and `von-decide` are both classifiers and
    /// their payloads differ. Dispatch on this.
    #[serde(default)]
    pub payload_schema: String,
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
    /// The models this token may call -- not every deployed model, and not since PRM-167.
    ///
    /// This endpoint was the platform's one public route and returned the whole catalog. It now
    /// requires a token and answers exactly what [`Client::models_mine`] answers; the two are
    /// aliases.
    ///
    /// An empty `data` means this token holds no `model:<id>` grants, **not** that the platform
    /// has no models. Those are different facts and only an operator can tell them apart.
    ///
    /// This doc used to say it needed no authentication. That was true of the platform and never
    /// true of this code, which has always sent the token -- and the accident is what kept it
    /// working when the platform closed the endpoint without announcing it.
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
            // Absence is no longer evidence. This used to refuse, because the catalog was the
            // platform's full public list and a missing model was a typo. Since PRM-167 it holds
            // only the models this token has a grant for, so absence has two causes this SDK
            // cannot tell apart: not registered (the gateway says 400 unknown-model) or not
            // granted (403 forbidden). Refusing here would tell a caller to check a name that is
            // spelled correctly, and would pre-empt the 403 whose job is to name the scope.
            return Ok(());
        };
        if found.modality.is_empty() || !is_known_modality(&found.modality) {
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::Path;

    /// Named here so a set added to one list and not the other is caught rather than inferred.
    const DECLARED: &[(&str, &[&str])] = &[
        ("CHAT_MODALITIES", CHAT_MODALITIES),
        ("EMBEDDING_MODALITIES", EMBEDDING_MODALITIES),
        ("IMAGE_MODALITIES", IMAGE_MODALITIES),
        ("RERANK_MODALITIES", RERANK_MODALITIES),
        ("PREDICT_MODALITIES", PREDICT_MODALITIES),
    ];

    /// A modality only one endpoint accepts is still known, or that endpoint has no check at all.
    ///
    /// This is the assertion that was missing. `Client::rerank` passed a literal `&["rerank"]`, and
    /// because `KNOWN_MODALITIES` was a separate hand-written list that had never heard of
    /// `"rerank"`, [`Client::check_modality`] returned before it could compare anything -- the call
    /// refused nothing, including the pairing its own doc comment promised to catch.
    #[test]
    fn every_accepted_modality_is_known() {
        for (name, set) in DECLARED {
            for modality in *set {
                assert!(
                    is_known_modality(modality),
                    "{name} accepts {modality:?}, which is_known_modality rejects: \
                     check_modality returns early on it, so that endpoint refuses nothing"
                );
            }
        }
    }

    /// Every `check_modality` call must pass one of the declared endpoint sets.
    ///
    /// A literal at the call site is how a modality gets accepted by an endpoint without ever
    /// becoming known, which is exactly how the rerank check shipped dead. Literals are refused.
    #[test]
    fn every_modality_check_uses_a_declared_set() {
        let src = Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
        let mut found = 0;

        for entry in fs::read_dir(&src).expect("src is readable") {
            let path = entry.expect("entry").path();
            if path.extension().and_then(|e| e.to_str()) != Some("rs") {
                continue;
            }
            let source = fs::read_to_string(&path).expect("source is readable");
            // Truncated at the test module, because this very test mentions check_modality and
            // would otherwise match itself -- which it did, on the first run.
            let source = source
                .split_once("#[cfg(test)]")
                .map_or(source.as_str(), |(before, _)| before);
            for line in source.lines() {
                let Some(rest) = line.split_once("check_modality(").map(|(_, r)| r) else {
                    continue;
                };
                // The definition itself, and the doc comments that name it, are not calls.
                if line.trim_start().starts_with("///")
                    || line.trim_start().starts_with("//")
                    || line.contains("pub(crate) async fn check_modality")
                {
                    continue;
                }
                found += 1;
                let argument = rest
                    .split(',')
                    .nth(1)
                    .unwrap_or("")
                    .trim()
                    .trim_end_matches(')')
                    .trim_start_matches("crate::catalog::");
                assert!(
                    DECLARED.iter().any(|(name, _)| *name == argument),
                    "{} passes {argument:?} to check_modality; it must pass one of the declared \
                     endpoint sets, because the known set is derived from those and a literal here \
                     is accepted by an endpoint without ever becoming known",
                    path.file_name().unwrap().to_string_lossy()
                );
            }
        }
        assert!(
            found > 0,
            "found no check_modality call sites; the pattern stopped matching, not the code"
        );
    }
}
