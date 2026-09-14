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
//!
//! **Tool calls arrive in pieces that are not individually valid JSON.** A call is split across as
//! many deltas as it takes -- `{`, `"`, `city` -- and only the first carries the identity (`id`,
//! `type`, `function.name`). `index` is the correlation key, because `id` never repeats. They are
//! reassembled here so a caller never has to, and the result is the same shape a non-streaming
//! completion returns.

use std::collections::BTreeMap;

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

    /// This chunk's raw tool-call fragments, which are *not* usable on their own.
    ///
    /// A fragment carries a slice of an `arguments` string that is invalid JSON by itself, and
    /// only the first one for a given `index` carries the identity. Use [`ChatStream::tool_calls`]
    /// for the assembled calls; this is here for a caller who wants to watch them arrive.
    pub fn tool_call_fragments(&self) -> Vec<Value> {
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

/// One tool call being assembled from its fragments.
#[derive(Debug, Default)]
struct PartialToolCall {
    id: Option<String>,
    kind: Option<String>,
    name: Option<String>,
    arguments: String,
}

impl PartialToolCall {
    /// Folds one wire fragment in.
    ///
    /// Identity is recorded the first time it is seen rather than overwritten: `id` is documented
    /// never to repeat, so a second, different one would be a correlation bug, and adopting it
    /// silently would repoint a call that is already accumulating arguments.
    fn absorb(&mut self, fragment: &Value) {
        let text = |value: Option<&Value>| value.and_then(Value::as_str).map(str::to_string);

        self.id = self.id.take().or_else(|| text(fragment.get("id")));
        self.kind = self.kind.take().or_else(|| text(fragment.get("type")));

        let Some(function) = fragment.get("function") else {
            return;
        };
        self.name = self.name.take().or_else(|| text(function.get("name")));
        if let Some(piece) = function.get("arguments").and_then(Value::as_str) {
            self.arguments.push_str(piece);
        }
    }

    /// Renders the call in the shape a non-streaming completion returns.
    ///
    /// `arguments` stays a JSON *string*, exactly as non-streaming delivers it, rather than being
    /// decoded here. That is what lets one piece of caller code handle both, and it means a stream
    /// cut short by `max_tokens` still hands back the fragment that did arrive instead of failing
    /// or dropping the call.
    fn assemble(&self) -> Value {
        serde_json::json!({
            "id": self.id,
            "type": self.kind,
            "function": {"name": self.name, "arguments": self.arguments},
        })
    }
}

/// Folds a chunk's fragments into the calls under construction, keyed by their wire `index`.
///
/// Split out from the stream so the correlation rules can be exercised directly: the recorded
/// contract cases pin what the gateway actually emits, and these are the rules that only show up
/// when it emits something it never has yet.
fn absorb_tool_calls(into: &mut BTreeMap<i64, PartialToolCall>, fragments: &[Value]) {
    for fragment in fragments {
        // Every backend seen so far sends `index`, and it is the only way to tell two concurrent
        // calls apart. Falling back to slot 0 keeps the single-call case working instead of
        // dropping the call outright, which is the only case a stream without indices can
        // represent unambiguously anyway.
        let index = fragment.get("index").and_then(Value::as_i64).unwrap_or(0);
        into.entry(index).or_default().absorb(fragment);
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
    // Ordered by the wire `index` rather than by arrival, so a backend that interleaves two
    // calls still yields them in the order the model asked for. A BTreeMap gives that for
    // free; a HashMap would hand back whichever order it felt like.
    tool_calls: BTreeMap<i64, PartialToolCall>,
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
            tool_calls: BTreeMap::new(),
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
        absorb_tool_calls(&mut self.tool_calls, &chunk.tool_call_fragments());
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

    /// The tool calls the model asked for, in the same shape non-streaming returns.
    ///
    /// Reassembled from fragments that are individually invalid JSON, so this is what a caller
    /// should read rather than the per-chunk [`Chunk::tool_call_fragments`]. `arguments` is a JSON
    /// string here exactly as it is non-streaming, so the same `serde_json::from_str` works for
    /// both.
    ///
    /// Populated as the stream runs, and complete once it ends. A stream that stopped on a
    /// `finish_reason` of `length` leaves a truncated `arguments` that will not parse -- check the
    /// finish reason before decoding.
    pub fn tool_calls(&self) -> Vec<Value> {
        self.tool_calls
            .values()
            .map(PartialToolCall::assemble)
            .collect()
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

#[cfg(test)]
mod tool_call_tests {
    //! Reassembly rules the recordings cannot exercise.
    //!
    //! The two recorded cases in the contract manifest cover what the gateway actually emits, and
    //! they are what pins the behaviour. These use constructed input -- said plainly, because a
    //! hand-written stream proves only that the code does what it was written to do -- to cover
    //! two defensive rules the wire has never yet violated: fragments arriving out of index order,
    //! and a second, contradictory identity for a call already in flight.

    use super::*;

    fn assemble(groups: &[Vec<Value>]) -> Vec<Value> {
        let mut calls = BTreeMap::new();
        for group in groups {
            absorb_tool_calls(&mut calls, group);
        }
        calls.values().map(PartialToolCall::assemble).collect()
    }

    fn head(index: i64, id: &str, name: &str, arguments: &str) -> Value {
        serde_json::json!({
            "index": index, "id": id, "type": "function",
            "function": {"name": name, "arguments": arguments},
        })
    }

    fn more(index: i64, arguments: &str) -> Value {
        serde_json::json!({"index": index, "function": {"arguments": arguments}})
    }

    #[test]
    fn calls_come_back_in_index_order_not_arrival_order() {
        // The gateway groups by index today, so this ordering has never been observed. Relying on
        // arrival order would work right up until a backend interleaves, and then it would hand
        // the caller two calls with their arguments swapped rather than failing loudly.
        let calls = assemble(&[
            vec![head(1, "b", "second", "{}")],
            vec![head(0, "a", "first", "{}")],
        ]);
        let ids: Vec<_> = calls.iter().map(|c| c["id"].as_str().unwrap()).collect();
        assert_eq!(ids, ["a", "b"]);
    }

    #[test]
    fn interleaved_arguments_stay_with_their_own_call() {
        let calls = assemble(&[
            vec![head(0, "a", "f", "{\"x\":")],
            vec![head(1, "b", "f", "{\"y\":")],
            vec![more(0, "1}")],
            vec![more(1, "2}")],
        ]);
        let args: Vec<_> = calls
            .iter()
            .map(|c| c["function"]["arguments"].as_str().unwrap())
            .collect();
        assert_eq!(args, ["{\"x\":1}", "{\"y\":2}"]);
    }

    #[test]
    fn a_contradictory_second_id_does_not_repoint_the_call() {
        // id is documented never to repeat, so a second one for the same index is a platform bug.
        // Adopting it would move arguments already accumulated onto a different call, which is
        // worse than ignoring it: the caller would execute the right arguments against the wrong
        // id.
        let calls = assemble(&[
            vec![head(0, "original", "f", "{")],
            vec![serde_json::json!({
                "index": 0, "id": "contradiction", "function": {"arguments": "}"}
            })],
        ]);
        assert_eq!(
            calls,
            vec![serde_json::json!({
                "id": "original", "type": "function",
                "function": {"name": "f", "arguments": "{}"},
            })]
        );
    }

    #[test]
    fn a_stream_with_no_tool_calls_reports_none() {
        assert!(assemble(&[vec![]]).is_empty());
    }
}
