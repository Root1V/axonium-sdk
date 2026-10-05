//! The pass-through route: the tasks OpenAI has no shape for.
//!
//! `POST /v1/models/{model}/predict` serves three modalities -- `classification`, `zero_shot` and
//! `typed_decision`. Every other route on this gateway is OpenAI-shaped because every task it
//! serves has an OpenAI endpoint to be shaped like. These do not, and inventing a body for them
//! would be the gateway deciding, on the engine's behalf, what the engine's API should look like.
//!
//! So **the body is forwarded to the engine verbatim and its answer comes back verbatim**, and the
//! shape is not stable even across engines serving the same modality. That is the cost of
//! pass-through and it is paid by the caller; [`Model::payload_schema`](crate::Model::payload_schema)
//! in the catalog is what identifies the shape.
//!
//! Two engines can serve one modality and disagree, and `tei.predict.v1` is the first case where
//! that is not hypothetical. On `zero_shot`, `hf-inference.zero-shot-classification.v1` answers
//! `{sequence, labels, scores}` normalised across the candidate labels YOU supplied, while
//! `tei.predict.v1` answers scores across the MODEL'S OWN classes and has no notion of candidate labels
//! at all. Both sum to 1, over different things. A caller dispatching on `modality` reads one as the
//! other; dispatching on `payload_schema` is what prevents it, and this is the case that argument was
//! waiting for.
//!
//! And `tei.predict.v1` has a trap worth reading before building a batch helper. Measured against a
//! live server by the platform team:
//!
//! ```text
//! inputs: "a text"                     -> one flat list of {label, score}
//! inputs: ["premise", "hypothesis"]    -> ONE PAIR, not a batch of two texts
//! inputs: ["a", "b", "c"]              -> 422
//! inputs: [["a"], ["b"]]               -> a batch of two single texts -> two lists
//! inputs: [["p1","h1"], ["p2","h2"]]   -> a batch of two pairs -> two lists
//! ```
//!
//! A batch is ALWAYS a list of lists. A flat array of two strings is read as a single pair and answers
//! once, in silence; three or more is a 422. So the obvious "send my N texts as an array" is the one
//! form that quietly returns a single wrong answer, and wrapping each text in its own list is the form
//! that batches. The per-request cap is set per instance (64 on the reference deployment).
//!
//! This is also why the result holds its value undecoded: a single input returns a flat list and a
//! batch returns a list of lists, from the same model and the same endpoint, so a type that assumed
//! either one would be wrong half the time.
//!
//! What does *not* pass through is the policy: the model still resolves, `inference:read` plus the
//! specific `model:<id>` scope is still required, a dead replica is still skipped, and the request
//! is still metered and still counts against a spend cap.

use serde::de::DeserializeOwned;
use serde_json::Value;

use crate::client::{CallOptions, Client};
use crate::error::{Error, Result};
use crate::types::ResponseMeta;

/// Whatever the engine returned, undecoded, and the correlation metadata beside it.
#[derive(Debug, Clone)]
pub struct PredictResult {
    /// The decoded body. A [`Value`] rather than a `Map`, because one of the three live shapes is
    /// not an object -- measured against a deployment:
    ///
    /// ```text
    /// sst2-clf     [{"label":"POSITIVE","score":0.978}]   <- a top-level array
    /// von-decide   {"sequence":…,"labels":[…],"scores":[…]}
    /// laya-decide  {"model":…,"answers":{…},"usage":{…},"routing":{…}}
    /// ```
    ///
    /// A `Map` would have failed to deserialise the first engine the platform shipped on this
    /// route.
    pub value: Value,

    /// Request and trace IDs, and the rate-limit budget, as of this response. The budget here is
    /// `predict`, shared by all three pass-through modalities rather than one each.
    pub meta: ResponseMeta,
}

impl PredictResult {
    /// Deserialises [`Self::value`] into a caller's own type, which is the normal way to read a
    /// result.
    ///
    /// ```no_run
    /// # use axonium::PredictResult;
    /// #[derive(serde::Deserialize)]
    /// struct Label {
    ///     label: String,
    ///     score: f64,
    /// }
    /// # fn f(result: PredictResult) -> axonium::Result<()> {
    /// let labels: Vec<Label> = result.decode()?;
    /// # Ok(())
    /// # }
    /// ```
    pub fn decode<T: DeserializeOwned>(&self) -> Result<T> {
        serde_json::from_value(self.value.clone()).map_err(|e| {
            Error::InvalidRequest(format!(
                "the engine's answer does not fit the requested type: {e}"
            ))
        })
    }
}

/// Per-call knobs the other endpoints carry on their request structs.
///
/// They cannot live inside the body here, because the body belongs to the engine: a field this
/// crate added would be forwarded to the engine as part of its payload.
#[derive(Debug, Clone, Default)]
pub struct PredictOptions {
    /// Makes a retry safe: a repeat with the same key and the same body returns the stored result
    /// without reaching a model, recording usage, or counting against the spend cap.
    pub idempotency_key: String,

    /// Pins the request to one replica. Diagnostic; leave empty to let the gateway route.
    pub instance: String,
}

impl Client {
    /// Sends `body` to `model` unchanged and returns its answer unchanged.
    ///
    /// Deliberately **not** a `classify(text)` typed per modality. That would promise a stability
    /// this endpoint does not offer -- `sst2-clf` and `von-decide` are both classifiers and want
    /// different payloads. Dispatch on
    /// [`Model::payload_schema`](crate::Model::payload_schema), not on
    /// [`Model::modality`](crate::Model::modality).
    ///
    /// ```no_run
    /// # use axonium::{Client, PredictOptions};
    /// # async fn f(client: &Client) -> axonium::Result<()> {
    /// let result = client
    ///     .predict(
    ///         "sst2-clf",
    ///         &serde_json::json!({"inputs": "El servicio ha sido excelente"}),
    ///         &PredictOptions::default(),
    ///     )
    ///     .await?;
    /// # Ok(())
    /// # }
    /// ```
    ///
    /// A model that *has* an OpenAI endpoint is refused here with `400 modality-mismatch` -- the
    /// inverse of every other handler's check. Without it the same model would be reachable two
    /// ways, with two billing paths and two rate-limit budgets, and the one that billed correctly
    /// would be whichever the caller did not use.
    pub async fn predict(
        &self,
        model: &str,
        body: &Value,
        options: &PredictOptions,
    ) -> Result<PredictResult> {
        if model.trim().is_empty() {
            return Err(Error::InvalidRequest("model is required".into()));
        }
        // Every engine seen on this route takes an object. An array or a scalar is not refused
        // because it is impossible -- it is refused because nothing in the contract describes one,
        // so sending it would be guessing on the caller's behalf, and the engine's own 4xx arrives
        // wrapped as predict-backend-rejected with a status that is the engine's.
        //
        // Note the asymmetry with the response, which genuinely can be an array: the request shape
        // is something the contract could describe and does, the response shape is the engine's.
        if !body.is_object() {
            return Err(Error::InvalidRequest(format!(
                "the predict body must be a JSON object; got {}. The engine's own contract is \
                 named by payload_schema in the catalog",
                kind_of(body)
            )));
        }
        self.check_modality(model, crate::catalog::PREDICT_MODALITIES)
            .await?;

        // The model ID is a path segment here rather than a body field, which is new on this route.
        // Escaping it matters: a slash in an ID would otherwise address a different endpoint, and
        // the gateway would answer about a route instead of about a model.
        let path = format!("/v1/models/{}/predict", encode_path_segment(model));

        let call = CallOptions {
            model: model.to_string(),
            instance: options.instance.clone(),
            idempotency_key: options.idempotency_key.clone(),
            ..Default::default()
        };

        let (response, meta) = self.send(&path, Some(body), &call).await?;
        let value: Value = response.json().await.map_err(|e| {
            Error::Transport(format!(
                "the predict route returned a body that is not JSON: {e}"
            ))
        })?;
        Ok(PredictResult { value, meta })
    }
}

fn kind_of(value: &Value) -> &'static str {
    match value {
        Value::Null => "null",
        Value::Bool(_) => "a boolean",
        Value::Number(_) => "a number",
        Value::String(_) => "a string",
        Value::Array(_) => "an array",
        Value::Object(_) => "an object",
    }
}

/// Percent-encodes everything a path segment may not contain, so an ID can never change the route.
fn encode_path_segment(segment: &str) -> String {
    let mut out = String::with_capacity(segment.len());
    for byte in segment.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(byte as char)
            }
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}
