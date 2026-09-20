//! Chat completions, streaming and not.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::client::{CallOptions, Client};
use crate::error::{Error, Result};
use crate::stream::ChatStream;
use crate::types::{ResponseMeta, Usage};

const ENDPOINT: &str = "/v1/chat/completions";

/// The function a tool call names, and the arguments it was called with.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct FunctionCall {
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub name: String,

    /// The arguments **as the model produced them**: a JSON string, not a decoded object.
    ///
    /// Kept raw on purpose. Streaming delivers this in fragments that are only valid once
    /// concatenated, and a generation cut short by `max_tokens` leaves a string that was never
    /// going to parse -- decoding here would turn that into an error surfaced from inside a
    /// response type, for a caller who only wanted to see what the model had managed to say. Use
    /// [`ToolCall::parse_arguments`] when you want the object.
    #[serde(default)]
    pub arguments: String,
}

/// A tool call, in the one shape both streaming and non-streaming produce.
///
/// Streamed calls arrive split across fragments that are individually invalid JSON; the SDK
/// reassembles them into exactly this, so the same caller code handles both.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct ToolCall {
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub id: String,
    /// `"function"` for everything the gateway forwards today. Modelled rather than assumed,
    /// because the field exists on the wire precisely so it can grow.
    #[serde(rename = "type", default, skip_serializing_if = "String::is_empty")]
    pub kind: String,
    #[serde(default)]
    pub function: FunctionCall,
}

impl ToolCall {
    /// The function name, without reaching through [`ToolCall::function`].
    pub fn name(&self) -> &str {
        &self.function.name
    }

    /// Decodes [`FunctionCall::arguments`] into an object.
    ///
    /// Fails with [`Error::ToolCallArguments`] when the string is not a JSON object. The usual
    /// cause is a generation that ran out of tokens mid-call, so check `finish_reason` before
    /// calling this on a response you have not verified completed.
    pub fn parse_arguments(&self) -> Result<serde_json::Map<String, Value>> {
        match serde_json::from_str::<Value>(&self.function.arguments) {
            Ok(Value::Object(map)) => Ok(map),
            Ok(other) => Err(Error::ToolCallArguments {
                tool_call_id: self.id.clone(),
                arguments: self.function.arguments.clone(),
                reason: format!("it decoded to {}, not an object", kind_of(&other)),
            }),
            Err(error) => Err(Error::ToolCallArguments {
                tool_call_id: self.id.clone(),
                arguments: self.function.arguments.clone(),
                reason: error.to_string(),
            }),
        }
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

/// One turn of a conversation.
///
/// `Default` so that a turn can be written by naming only the fields it uses -- the shape every
/// other request type in this SDK is built with. A defaulted `role` is empty and not a valid
/// message on its own; it is a starting point, not a message.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Message {
    pub role: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub content: Option<Value>,
    /// A reasoning model's chain of thought, kept apart from `content` because it is not part of
    /// the answer. Never inferred from `content` and never merged into it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reasoning_content: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tool_calls: Option<Vec<ToolCall>>,
    /// The id of the call this message answers, on a `role: "tool"` turn.
    ///
    /// Without it a tool result cannot be matched to the call that asked for it, and a tool-use
    /// loop cannot be closed at all: this SDK could receive a tool call and never send its result
    /// back. Python and Go have carried it since the beginning; Rust did not, and writing the
    /// documented loop in all three languages is what noticed.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub tool_call_id: String,
    /// The tool's name on a `role: "tool"` turn, where a backend expects it alongside the id.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub name: String,
}

impl Message {
    pub fn text(role: &str, content: &str) -> Self {
        Self {
            role: role.into(),
            content: Some(Value::String(content.into())),
            reasoning_content: None,
            tool_calls: None,
            tool_call_id: String::new(),
            name: String::new(),
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

    /// Tool calls from the first choice.
    ///
    /// The gateway does not interpret them, and neither does this SDK beyond giving them a shape:
    /// `arguments` is still the model's own string, reachable raw.
    pub fn tool_calls(&self) -> Vec<ToolCall> {
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

#[cfg(test)]
mod tool_results {
    use super::{Message, ToolCall};

    /// A tool-use loop has to be expressible: receive a call, run it, send the result back.
    ///
    /// It was not. `Message` carried no `tool_call_id`, so this SDK could read a tool call and had
    /// no way to answer it -- the loop AXO-55 described as needing "no conversion in either
    /// direction" could not be closed in Rust at all. Nothing failed, because nobody had written
    /// the second half.
    #[test]
    fn a_tool_result_can_be_sent_back() {
        let call = ToolCall {
            id: "call_1".into(),
            ..Default::default()
        };

        let result = Message {
            role: "tool".into(),
            tool_call_id: call.id.clone(),
            content: Some("42".into()),
            ..Default::default()
        };

        let wire = serde_json::to_value(&result).expect("serialises");
        assert_eq!(wire["role"], "tool");
        assert_eq!(wire["tool_call_id"], "call_1");
    }

    /// The two new fields are absent from an ordinary turn rather than sent empty: a backend that
    /// validates `tool_call_id` should not see one on a user message.
    #[test]
    fn an_ordinary_turn_carries_neither_field() {
        let wire = serde_json::to_value(Message::text("user", "hi")).expect("serialises");
        assert!(wire.get("tool_call_id").is_none(), "{wire}");
        assert!(wire.get("name").is_none(), "{wire}");
    }

    #[test]
    fn a_tool_turn_survives_a_whole_conversation() {
        let conversation = vec![
            Message::text("user", "weather?"),
            Message {
                role: "tool".into(),
                tool_call_id: "call_1".into(),
                content: Some("sunny".into()),
                ..Default::default()
            },
        ];

        let wire = serde_json::to_value(&conversation).expect("serialises");
        assert_eq!(wire[1]["tool_call_id"], "call_1");
        assert!(wire[0].get("tool_call_id").is_none());
    }
}
