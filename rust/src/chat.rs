//! Chat completions, streaming and not.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::client::{CallOptions, Client};
use crate::error::{Error, Result};
use crate::stream::ChatStream;
use crate::types::{ResponseMeta, Usage};

const ENDPOINT: &str = "/v1/chat/completions";

/// One turn of a conversation.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Message {
    pub role: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub content: Option<Value>,
    /// A reasoning model's chain of thought, kept apart from `content` because it is not part of
    /// the answer. Never inferred from `content` and never merged into it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reasoning_content: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tool_calls: Option<Vec<Value>>,
}

impl Message {
    pub fn text(role: &str, content: &str) -> Self {
        Self {
            role: role.into(),
            content: Some(Value::String(content.into())),
            reasoning_content: None,
            tool_calls: None,
        }
    }
}

/// A chat completion request.
///
/// The gateway's request schema is an allowlist and silently discards what it does not accept, so
/// this models only what it reads. `extra` carries anything else deliberately, so an unrecognised
/// field is a decision rather than a typo that vanishes.
#[derive(Debug, Clone, Default)]
pub struct ChatRequest {
    pub model: String,
    pub messages: Vec<Message>,
    pub max_tokens: Option<u32>,
    pub temperature: Option<f64>,
    pub top_p: Option<f64>,
    pub stop: Vec<String>,
    pub tools: Vec<Value>,
    pub tool_choice: Option<Value>,
    pub extra: serde_json::Map<String, Value>,
    /// Pins the request to one instance, by label (`#2`) or full id. Sent as a header, never in
    /// `model`: a grant covers a model, billing attributes to a model, and the catalog lists
    /// models. A pin opts out of load balancing **and** of failover, so it is for reproducing a
    /// problem, not for normal traffic. Kept across retries.
    pub instance: String,
    /// Makes a retry safe: a repeat with the same key and body returns the stored result without
    /// reaching a model, recording usage, or counting against the spend cap. It is also the only
    /// thing that lets a client-side timeout be retried.
    ///
    /// On a stream it replays one the gateway **finished** and whose delivery your connection
    /// dropped, never one the model itself broke -- that needs resuming rather than replaying.
    pub idempotency_key: String,
}

impl ChatRequest {
    fn validate(&self) -> Result<()> {
        let mut problems = Vec::new();
        if self.model.trim().is_empty() {
            problems.push("model is required".to_string());
        }
        if self.messages.is_empty() {
            problems.push("messages must not be empty".to_string());
        }
        for (i, m) in self.messages.iter().enumerate() {
            if !matches!(m.role.as_str(), "system" | "user" | "assistant" | "tool") {
                problems.push(format!(
                    "messages[{i}].role {:?} is not one of system, user, assistant, tool",
                    m.role
                ));
            }
            if let Some(reason) = remote_image(m.content.as_ref()) {
                problems.push(format!("messages[{i}]: {reason}"));
            }
        }
        if let Some(t) = self.temperature {
            if !(0.0..=2.0).contains(&t) {
                problems.push("temperature must be within [0, 2]".to_string());
            }
        }
        if let Some(p) = self.top_p {
            if p <= 0.0 || p > 1.0 {
                problems.push("top_p must be within (0, 1]".to_string());
            }
        }
        if self.max_tokens == Some(0) {
            problems.push("max_tokens must be greater than zero".to_string());
        }
        if problems.is_empty() {
            Ok(())
        } else {
            Err(Error::InvalidRequest(problems.join("; ")))
        }
    }

    fn payload(&self, stream: bool) -> Value {
        let mut body = json!({ "model": self.model, "messages": self.messages });
        let map = body.as_object_mut().unwrap();
        if stream {
            map.insert("stream".into(), json!(true));
        }
        if let Some(v) = self.max_tokens {
            map.insert("max_tokens".into(), json!(v));
        }
        if let Some(v) = self.temperature {
            map.insert("temperature".into(), json!(v));
        }
        if let Some(v) = self.top_p {
            map.insert("top_p".into(), json!(v));
        }
        if !self.stop.is_empty() {
            map.insert("stop".into(), json!(self.stop));
        }
        if !self.tools.is_empty() {
            map.insert("tools".into(), json!(self.tools));
        }
        if let Some(v) = &self.tool_choice {
            map.insert("tool_choice".into(), v.clone());
        }
        for (k, v) in &self.extra {
            map.insert(k.clone(), v.clone());
        }
        body
    }

    fn options(&self, streaming: bool) -> CallOptions {
        CallOptions {
            model: self.model.clone(),
            instance: self.instance.clone(),
            idempotency_key: self.idempotency_key.clone(),
            streaming,
        }
    }
}

/// Refuses a remote image URL before it is sent. The gateway will not fetch one -- accepting a
/// caller-supplied URL server-side is an SSRF vector -- so images must be inlined as data URIs.
fn remote_image(content: Option<&Value>) -> Option<String> {
    for part in content?.as_array()? {
        let url = part
            .get("image_url")?
            .get("url")
            .and_then(Value::as_str)
            .unwrap_or_default();
        if url.starts_with("http://") || url.starts_with("https://") {
            return Some(
                "image_url.url must be a data: URI, not a remote URL; the gateway does not fetch \
                 remote images, because doing so server-side would be an SSRF vector"
                    .into(),
            );
        }
    }
    None
}

/// One completion candidate.
#[derive(Debug, Clone, Deserialize)]
pub struct Choice {
    #[serde(default)]
    pub index: u32,
    #[serde(default)]
    pub message: Option<Message>,
    #[serde(default)]
    pub delta: Option<Message>,
    #[serde(default)]
    pub finish_reason: Option<String>,
}

/// A non-streaming chat completion.
#[derive(Debug, Clone, Deserialize)]
pub struct ChatCompletion {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub choices: Vec<Choice>,
    #[serde(default)]
    pub usage: Option<Usage>,
    /// The decoded body as received, so a backend-specific field this crate does not model is
    /// still reachable rather than dropped.
    #[serde(skip)]
    pub raw: Value,
    #[serde(skip)]
    pub meta: ResponseMeta,
}

impl ChatCompletion {
    /// The first choice's text, empty when the model returned tool calls instead of prose or is
    /// still reasoning.
    pub fn content(&self) -> String {
        self.choices
            .first()
            .and_then(|c| c.message.as_ref())
            .and_then(|m| m.content.as_ref())
            .map(flatten)
            .unwrap_or_default()
    }

    /// The first choice's chain of thought, separate from the answer.
    pub fn reasoning(&self) -> String {
        self.choices
            .first()
            .and_then(|c| c.message.as_ref())
            .and_then(|m| m.reasoning_content.clone())
            .unwrap_or_default()
    }

    pub fn finish_reason(&self) -> String {
        self.choices
            .first()
            .and_then(|c| c.finish_reason.clone())
            .unwrap_or_default()
    }

    /// Tool calls, passed through untouched: the gateway does not interpret them.
    pub fn tool_calls(&self) -> Vec<Value> {
        self.choices
            .first()
            .and_then(|c| c.message.as_ref())
            .and_then(|m| m.tool_calls.clone())
            .unwrap_or_default()
    }
}

/// Flattens content, which is a string for plain messages and a list of parts for multimodal ones.
pub(crate) fn flatten(content: &Value) -> String {
    match content {
        Value::String(s) => s.clone(),
        Value::Array(parts) => parts
            .iter()
            .filter_map(|p| p.get("text").and_then(Value::as_str))
            .collect(),
        _ => String::new(),
    }
}

impl Client {
    /// Sends a non-streaming chat completion.
    pub async fn chat(&self, request: &ChatRequest) -> Result<ChatCompletion> {
        request.validate()?;
        self.check_modality(&request.model, crate::catalog::CHAT_MODALITIES)
            .await?;

        let (response, meta) = self
            .send(
                ENDPOINT,
                Some(&request.payload(false)),
                &request.options(false),
            )
            .await?;

        let raw: Value = response.json().await.map_err(|e| {
            Error::Transport(format!(
                "the gateway returned a body this SDK could not parse: {e}"
            ))
        })?;
        let mut completion: ChatCompletion = serde_json::from_value(raw.clone()).map_err(|e| {
            Error::Transport(format!(
                "the gateway returned a chat completion this SDK could not parse: {e}"
            ))
        })?;
        if let Some(usage) = completion.usage.as_mut() {
            usage.absorb_details(raw.get("usage").unwrap_or(&Value::Null));
        }
        completion.raw = raw;
        completion.meta = meta;
        Ok(completion)
    }

    /// Opens a streaming chat completion.
    ///
    /// Streaming is a separate method rather than a flag: it needs its own scope, it is never
    /// retried automatically, and its result is a different type. Folding it into `chat` would
    /// hide all three.
    ///
    /// Dropping the returned stream cancels the request, which propagates through the gateway to
    /// Prometheus and stops the generation -- so an abandoned stream stops costing money.
    pub async fn chat_stream(&self, request: &ChatRequest) -> Result<ChatStream> {
        request.validate()?;
        self.check_modality(&request.model, crate::catalog::CHAT_MODALITIES)
            .await?;

        let (response, meta) = self
            .send(
                ENDPOINT,
                Some(&request.payload(true)),
                &request.options(true),
            )
            .await?;
        Ok(ChatStream::new(response, meta))
    }
}
