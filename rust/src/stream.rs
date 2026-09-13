//! Server-sent event parsing for streaming completions.
//!
//! Two things about this stream are easy to get wrong, and both are handled here rather than at
//! the call sites.
//!
//! **Failures arrive in band.** By the time a backend fails mid-generation the `200` and
//! `text/event-stream` headers are already committed, so a broken stream cannot be signalled with
//! a status code. The gateway emits an error chunk instead. A client that only checks the status
//! sees a truncated response as a successful one.
//!
//! **Token counts may never arrive as `usage`.** llama.cpp-family backends send none at all; the
//! counts have to be recovered from the final chunk's `timings`, and a derived figure is flagged
//! so a caller never mistakes it for a reported one.

use bytes::Bytes;
use futures_util::StreamExt;
use serde_json::Value;

use crate::chat::{flatten, ChatCompletion};
use crate::error::{Error, Result};
use crate::types::{ResponseMeta, Usage};

const DATA_PREFIX: &str = "data:";

/// The terminal sentinel. The gateway appends exactly one, and a client must stop at it -- for a
/// while the gateway sent two and everything that recorded the request lived past the first, so a
/// correct client went unbilled.
const DONE: &str = "[DONE]";

/// One streamed delta.
#[derive(Debug, Clone)]
pub struct Chunk {
    pub raw: Value,
}

impl Chunk {
    fn delta(&self) -> Option<&Value> {
        self.raw.get("choices")?.get(0)?.get("delta")
    }

    /// This chunk's text delta, empty while the model is reasoning.
    pub fn content(&self) -> String {
        self.delta()
            .and_then(|d| d.get("content"))
            .map(flatten)
            .unwrap_or_default()
    }

    /// This chunk's chain-of-thought delta. A chunk carries one or the other, so a progress
    /// display can show which phase the model is in.
    pub fn reasoning(&self) -> String {
        self.delta()
            .and_then(|d| d.get("reasoning_content"))
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string()
    }

    /// Tool call fragments, if any. The first carries `index`, `id`, `type` and `function.name`;
    /// later ones carry only `index` and an `arguments` fragment. **`index` is the correlation
    /// key**, since `id` never repeats -- and the fragments are individually invalid JSON, so they
    /// are concatenated and parsed once at the end.
    pub fn tool_calls(&self) -> Vec<Value> {
        self.delta()
            .and_then(|d| d.get("tool_calls"))
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
    }

    pub fn finish_reason(&self) -> String {
        self.raw
            .get("choices")
            .and_then(|c| c.get(0))
            .and_then(|c| c.get("finish_reason"))
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string()
    }
}

/// An open streaming completion.
///
/// Dropping it cancels the request, which the gateway observes as a disconnect and passes to
/// Prometheus, which stops generating and frees the backend slot. That is what makes an abandoned
/// stream stop costing money, so dropping early is a real action rather than a leak.
pub struct ChatStream {
    body: Option<Box<dyn futures_util::Stream<Item = reqwest::Result<Bytes>> + Send + Unpin>>,
    buffer: String,
    meta: ResponseMeta,
    content: String,
    reasoning: String,
    usage: Option<Usage>,
    timings: Option<Value>,
    done: bool,
}

impl ChatStream {
    pub(crate) fn new(response: reqwest::Response, meta: ResponseMeta) -> Self {
        Self {
            body: Some(Box::new(response.bytes_stream())),
            buffer: String::new(),
            meta,
            content: String::new(),
            reasoning: String::new(),
            usage: None,
            timings: None,
            done: false,
        }
    }

    /// The next chunk, or `None` at end of stream.
    pub async fn next(&mut self) -> Result<Option<Chunk>> {
        loop {
            if let Some(line) = self.take_line() {
                match decode(&line) {
                    Some(Event::Done) => {
                        self.done = true;
                        return Ok(None);
                    }
                    Some(Event::Payload(payload)) => return self.absorb(payload).map(Some),
                    None => continue,
                }
            }

            let Some(body) = self.body.as_mut() else {
                return self.finish();
            };
            match body.next().await {
                Some(Ok(bytes)) => self.buffer.push_str(&String::from_utf8_lossy(&bytes)),
                Some(Err(e)) => {
                    self.body = None;
                    return Err(Error::Transport(e.to_string()));
                }
                None => {
                    self.body = None;
                    // Anything left without a trailing newline is still a record.
                    if !self.buffer.trim().is_empty() {
                        let rest = std::mem::take(&mut self.buffer);
                        if let Some(Event::Payload(p)) = decode(&rest) {
                            return self.absorb(p).map(Some);
                        }
                        if let Some(Event::Done) = decode(&rest) {
                            self.done = true;
                            return Ok(None);
                        }
                    }
                    return self.finish();
                }
            }
        }
    }

    fn take_line(&mut self) -> Option<String> {
        let idx = self.buffer.find('\n')?;
        let line = self.buffer[..idx].to_string();
        self.buffer.drain(..=idx);
        Some(line)
    }

    fn absorb(&mut self, payload: Value) -> Result<Chunk> {
        // Detected by the presence of the key, not by matching its message: the gateway documents
        // one failure string today, but that is implementation rather than contract.
        if let Some(error) = payload.get("error") {
            return Err(Error::StreamInterrupted {
                message: error
                    .as_str()
                    .map(str::to_string)
                    .unwrap_or_else(|| error.to_string()),
                partial_content: self.content.clone(),
                request_id: self.meta.request_id.clone(),
                trace_id: self.meta.trace_id.clone(),
            });
        }

        let chunk = Chunk { raw: payload };
        self.content.push_str(&chunk.content());
        self.reasoning.push_str(&chunk.reasoning());
        if let Some(usage) = chunk.raw.get("usage") {
            if let Ok(mut parsed) = serde_json::from_value::<Usage>(usage.clone()) {
                parsed.absorb_details(usage);
                self.usage = Some(parsed);
            }
        }
        if let Some(timings) = chunk.raw.get("timings") {
            self.timings = Some(timings.clone());
        }
        Ok(chunk)
    }

    /// The gateway always sends the sentinel, so its absence means the connection died
    /// mid-generation. Reporting success would hand the caller a truncated answer they believe is
    /// complete.
    fn finish(&mut self) -> Result<Option<Chunk>> {
        if self.done {
            return Ok(None);
        }
        self.done = true;
        Err(Error::StreamInterrupted {
            message: "the connection closed before the stream was terminated".into(),
            partial_content: self.content.clone(),
            request_id: self.meta.request_id.clone(),
            trace_id: self.meta.trace_id.clone(),
        })
    }

    /// Everything received so far, including on a stream that failed partway.
    pub fn content(&self) -> &str {
        &self.content
    }

    /// The chain of thought received so far, assembled separately from the answer. A reasoning
    /// model streams it before any answer token, so `content` stays empty until it stops thinking
    /// -- and with a small `max_tokens` it can stay empty for the whole stream.
    pub fn reasoning(&self) -> &str {
        &self.reasoning
    }

    pub fn meta(&self) -> &ResponseMeta {
        &self.meta
    }

    /// Token accounting, or `None` if it cannot be determined. A reported `usage` wins; otherwise
    /// the counts are reconstructed from the final chunk's timings and flagged `estimated`.
    pub fn usage(&self) -> Option<Usage> {
        if let Some(usage) = &self.usage {
            return Some(usage.clone());
        }
        let timings = self.timings.as_ref()?;
        let number = |key: &str| timings.get(key).and_then(Value::as_u64);
        let cached = number("cache_n");
        // input includes the cached prefix; cache_read says how many of those were cached.
        let prompt = number("prompt_n").unwrap_or(0) + cached.unwrap_or(0);
        let completion = number("predicted_n").unwrap_or(0);
        if prompt == 0 && completion == 0 {
            return None;
        }
        Some(Usage {
            prompt_tokens: Some(prompt),
            completion_tokens: Some(completion),
            total_tokens: Some(prompt + completion),
            cache_read_tokens: cached,
            estimated: true,
        })
    }
}

enum Event {
    Payload(Value),
    Done,
}

/// Decodes one wire line, or `None` for one carrying no event. Blank lines separate records, and
/// anything that is not a `data:` field is not part of this protocol.
fn decode(line: &str) -> Option<Event> {
    let stripped = line.trim();
    let data = stripped.strip_prefix(DATA_PREFIX)?.trim();
    if data == DONE {
        return Some(Event::Done);
    }
    if data.is_empty() {
        return None;
    }
    // A single unparseable chunk is not worth destroying an otherwise good stream over.
    serde_json::from_str(data).ok().map(Event::Payload)
}

/// Kept for symmetry with the other SDKs, where a completed stream can be turned into a
/// completion-shaped value.
impl ChatStream {
    pub fn into_completion(self) -> ChatCompletion {
        ChatCompletion {
            id: String::new(),
            model: String::new(),
            choices: Vec::new(),
            usage: self.usage(),
            raw: Value::Null,
            meta: self.meta,
        }
    }
}
