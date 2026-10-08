//! Spans and events, behind the `tracing` feature.
//!
//! The platform owns tracing. This emits only the complementary half: enough for a caller's own
//! traces and logs to line up with the platform's, and nothing that duplicates what it records.
//!
//! **Prompts, completions and credentials are never emitted, and there is no option to turn that
//! on.** Correlating a request with the platform's traces needs the request and trace IDs, not the
//! content — and a library that can be configured to log prompts is how that content ends up in a
//! collector nobody audited. Whatever you need to log about the content, you have at the call site.
//!
//! Attribute names follow the GenAI semantic conventions, so spans are readable by tooling that
//! already understands LLM traffic. Trace context is **not** propagated outbound: the gateway
//! starts its own trace and discards a client-supplied one, verified against the deployment, so
//! sending any would be decoration. Correlation runs inbound through the IDs it returns.

use std::time::Duration;

use crate::types::ResponseMeta;

/// The wait at or above which a retry is reported at INFO rather than DEBUG: long enough that
/// a caller will notice it as a stall and want it explained.
///
/// Gated with the feature that uses it: without `tracing` this module compiles to nothing, and
/// an ungated constant is dead code that `clippy -D warnings` rejects -- which is exactly what
/// CI does, and what a local `--all-features` run hides.
#[cfg(feature = "tracing")]
const NOTICEABLE_WAIT: std::time::Duration = std::time::Duration::from_secs(1);

/// One traced operation. A no-op unless the `tracing` feature is on, so call sites need no `cfg`.
pub(crate) struct Operation {
    #[cfg(feature = "tracing")]
    span: tracing::Span,
}

impl Operation {
    #[cfg(feature = "tracing")]
    pub(crate) fn start(path: &str, model: &str, host: &str) -> Self {
        // `tracing` span names are static at macro time, so the convention's
        // `{gen_ai.operation.name} {gen_ai.request.model}` goes in `otel.name`, which is what
        // tracing-opentelemetry reads as the exported span name.
        let Some(operation) = operation_name(path) else {
            // A catalog listing reaches no model. Until this branch existed it got
            // `gen_ai.system` and an empty model, which put a scope lookup in GenAI aggregations
            // as an inference call that somehow used no tokens.
            return Self {
                span: tracing::info_span!(
                    "axonium",
                    otel.name = %format!("axonium {path}"),
                    url.path = path,
                    server.address = host,
                    prometheus.request_id = tracing::field::Empty,
                    prometheus.trace_id = tracing::field::Empty,
                    prometheus.instance_id = tracing::field::Empty,
                ),
            };
        };

        let name = if model.is_empty() {
            operation.to_string()
        } else {
            format!("{operation} {model}")
        };

        Self {
            span: tracing::info_span!(
                "axonium",
                otel.name = %name,
                gen_ai.provider.name = PROVIDER_NAME,
                gen_ai.operation.name = operation,
                gen_ai.request.model = model,
                url.path = path,
                server.address = host,
                prometheus.request_id = tracing::field::Empty,
                prometheus.trace_id = tracing::field::Empty,
                prometheus.instance_id = tracing::field::Empty,
            ),
        }
    }

    #[cfg(not(feature = "tracing"))]
    pub(crate) fn start(_path: &str, _model: &str, _host: &str) -> Self {
        Self {}
    }

    /// Attaches the gateway's correlation IDs, which is the whole reason the span exists: it is
    /// what lets a caller's trace be matched against the platform's.
    pub(crate) fn record_response(&self, _meta: &ResponseMeta) {
        #[cfg(feature = "tracing")]
        {
            if !_meta.request_id.is_empty() {
                self.span
                    .record("prometheus.request_id", _meta.request_id.as_str());
            }
            if !_meta.trace_id.is_empty() {
                self.span
                    .record("prometheus.trace_id", _meta.trace_id.as_str());
            }
            if !_meta.instance_id.is_empty() {
                self.span
                    .record("prometheus.instance_id", _meta.instance_id.as_str());
            }
        }
    }

    /// Reports that the SDK is about to sleep before retrying, and for how long.
    ///
    /// A caller who sees a call take 45 seconds and finds nothing in their logs files a latency
    /// bug. The `429` was the rate limit working and the wait is the whole explanation, so a wait
    /// a person would notice is reported at INFO. Sub-second backoff stays at DEBUG, where it
    /// belongs: the noise worry is frequent small retries, not the rare long one.
    ///
    /// Recorded as a wait rather than folded into `duration_ms`, which is measured per attempt and
    /// deliberately excludes it: time spent sleeping is not latency.
    pub(crate) fn record_retry_wait(
        &self,
        _model: &str,
        _status: u16,
        _attempt: u32,
        _suffix: &str,
        _delay: std::time::Duration,
    ) {
        #[cfg(feature = "tracing")]
        {
            let _enter = self.span.enter();
            let delay_s = _delay.as_secs_f64();
            if _delay >= NOTICEABLE_WAIT {
                tracing::info!(
                    model = _model,
                    status = _status,
                    attempt = _attempt,
                    r#type = _suffix,
                    delay_s,
                    "axonium waiting before a retry"
                );
            } else {
                tracing::debug!(
                    model = _model,
                    status = _status,
                    attempt = _attempt,
                    r#type = _suffix,
                    delay_s,
                    "axonium waiting before a retry"
                );
            }
        }
    }

    /// One record per completed attempt. Every field is metadata about the exchange rather than its
    /// content, which is what makes the whole set safe to emit at any level.
    pub(crate) fn record_attempt(&self, _at: &AttemptRecord<'_>) {
        #[cfg(feature = "tracing")]
        {
            let _enter = self.span.enter();
            let AttemptRecord {
                method: _method,
                path: _path,
                model: _model,
                status: _status,
                attempt: _attempt,
                elapsed,
                meta: _meta,
                error: _error,
            } = *_at;
            let duration_ms = elapsed.as_secs_f64() * 1000.0;
            match _error {
                Some(error) => tracing::debug!(
                    method = _method,
                    path = _path,
                    model = _model,
                    status = _status,
                    attempt = _attempt,
                    duration_ms,
                    request_id = _meta.request_id.as_str(),
                    trace_id = _meta.trace_id.as_str(),
                    instance_id = _meta.instance_id.as_str(),
                    error,
                    "axonium request failed"
                ),
                None => tracing::debug!(
                    method = _method,
                    path = _path,
                    model = _model,
                    status = _status,
                    attempt = _attempt,
                    duration_ms,
                    request_id = _meta.request_id.as_str(),
                    trace_id = _meta.trace_id.as_str(),
                    instance_id = _meta.instance_id.as_str(),
                    "axonium request"
                ),
            }
        }
    }
}

/// One attempt's metadata, grouped because nine positional arguments is a signature nobody calls
/// correctly twice.
#[derive(Clone, Copy)]
// Without the feature nothing reads these, which is exactly the point: the whole module compiles
// to nothing rather than costing a consumer who traces elsewhere.
#[cfg_attr(not(feature = "tracing"), allow(dead_code))]
pub(crate) struct AttemptRecord<'a> {
    pub method: &'a str,
    pub path: &'a str,
    pub model: &'a str,
    pub status: u16,
    pub attempt: u32,
    pub elapsed: Duration,
    pub meta: &'a ResponseMeta,
    pub error: Option<&'a str>,
}

/// Names the operation rather than the URL, so spans group by what was done.
/// `gen_ai.provider.name`, which replaced the deprecated `gen_ai.system`.
///
/// The convention's enumeration names model providers — `openai`, `anthropic`, `groq` and so on —
/// and this platform is not one of them, so this is a **custom value**, which the convention
/// permits when no well-known one applies. It deliberately does not name the inference engine:
/// Argus maps this attribute to `llama.cpp` / `vllm` / `ollama`, which is right for the gateway's
/// own spans because the gateway knows which backend served the request, and impossible from a
/// client because no response header carries it. `server.address` goes on the span instead, which
/// is what the convention names as the way to identify the actual system behind an
/// OpenAI-compatible endpoint.
///
/// Gated with the feature that uses it. Without `tracing` this module emits nothing, so the
/// constant is dead code -- which `clippy -D warnings` rejects and a `--all-features` run hides.
/// That trap is documented at the top of `scripts/verify.sh` and it caught this on the first run.
#[cfg(feature = "tracing")]
pub(crate) const PROVIDER_NAME: &str = "prometheus-gateway";

/// A route's `gen_ai.operation.name`, or `None` where no GenAI operation happened.
///
/// The shared table is `spec/otel-genai.json` and `tests/observability.rs` asserts this against it.
/// Three SDKs emit spans; a table copied by hand into three languages is how `AXO-139` produced
/// five divergent rate-limit scope lists that nothing could catch.
///
/// Gated for the same reason as `PROVIDER_NAME`: it feeds span attributes and nothing else.
#[cfg(feature = "tracing")]
pub(crate) fn operation_name(path: &str) -> Option<&'static str> {
    if path.ends_with("/chat/completions") {
        Some("chat")
    } else if path.ends_with("/embeddings") {
        Some("embeddings")
    // Not `retrieval`: that well-known value is RAG retrieval and carries `gen_ai.data_source.id`
    // to say which corpus was read. A reranker reads no corpus; it scores documents the caller
    // already holds.
    } else if path.ends_with("/rerank") {
        Some("rerank")
    // The enumeration has no image value, and `generate_content` means multimodal generation in
    // Gemini-shaped APIs.
    } else if path.ends_with("/generations") {
        Some("image_generation")
    } else if path.ends_with("/predict") {
        Some("predict")
    } else {
        None
    }
}

// The map only exists under `tracing`, so its test does too. `scripts/verify.sh` runs
// `cargo test` AND `cargo test --all-features`, so the guard still runs on every verification.
#[cfg(all(test, feature = "tracing"))]
mod tests {
    use super::{operation_name, PROVIDER_NAME};

    /// The shared table is `spec/otel-genai.json`, and this asserts the map above against it.
    /// Python and Go have the mirror of this test.
    ///
    /// `AXO-139` is why this is a test and not a comment asking for care: five SDKs kept five
    /// hand-copied lists of rate-limit scopes, they diverged, the published TypeScript documented
    /// a value the header never sends, and **nothing could catch it** because the list was prose.
    ///
    /// A unit test rather than one in `tests/`, because `operation_name` is `pub(crate)` and
    /// widening it so a test could see it would be the test changing the surface it checks.
    #[test]
    fn operation_names_match_the_shared_table() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("spec")
            .join("otel-genai.json");

        // The corpus lives in the monorepo, one level above this crate, so it is present from a
        // checkout and absent from the published package. Skipping there is honest; failing would
        // make `cargo test` on a published crate red for a file it was never shipped.
        let Ok(raw) = std::fs::read_to_string(&path) else {
            return;
        };
        let table: serde_json::Value = serde_json::from_str(&raw).expect("the table parses");

        assert_eq!(
            table["provider_name"].as_str(),
            Some(PROVIDER_NAME),
            "gen_ai.provider.name disagrees with the shared table"
        );

        let operations = table["operations"]
            .as_array()
            .expect("operations is a list");
        assert!(
            !operations.is_empty(),
            "the table lists no operations, so this test would pass by comparing nothing"
        );
        for entry in operations {
            let route = entry["path"].as_str().expect("path");
            assert_eq!(
                operation_name(route),
                entry["operation"].as_str(),
                "{route}: gen_ai.operation.name disagrees with the shared table"
            );
        }

        // The excluded routes are the half a count would miss: a map answering every path would
        // satisfy the loop above and still put a catalog listing in GenAI aggregations as an
        // inference call that used no tokens.
        for entry in table["no_genai_attributes"]
            .as_array()
            .expect("no_genai_attributes is a list")
        {
            let route = entry["path"].as_str().expect("path");
            assert_eq!(
                operation_name(route),
                None,
                "{route} carries no GenAI operation"
            );
        }
    }
}
