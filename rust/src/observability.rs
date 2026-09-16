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
const NOTICEABLE_WAIT: std::time::Duration = std::time::Duration::from_secs(1);

/// One traced operation. A no-op unless the `tracing` feature is on, so call sites need no `cfg`.
pub(crate) struct Operation {
    #[cfg(feature = "tracing")]
    span: tracing::Span,
}

impl Operation {
    #[cfg(feature = "tracing")]
    pub(crate) fn start(name: &'static str, model: &str) -> Self {
        Self {
            span: tracing::info_span!(
                "axonium",
                otel.name = name,
                gen_ai.system = "prometheus-gateway",
                gen_ai.request.model = model,
                prometheus.request_id = tracing::field::Empty,
                prometheus.trace_id = tracing::field::Empty,
                prometheus.instance_id = tracing::field::Empty,
            ),
        }
    }

    #[cfg(not(feature = "tracing"))]
    pub(crate) fn start(_name: &'static str, _model: &str) -> Self {
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
pub(crate) fn operation_name(path: &str) -> &'static str {
    if path.ends_with("/chat/completions") {
        "chat.completions"
    } else if path.ends_with("/embeddings") {
        "embeddings"
    } else if path.ends_with("/generations") {
        "images.generations"
    } else if path.ends_with("/mine") {
        "models.mine"
    } else if path.ends_with("/models") {
        "models.list"
    } else {
        "request"
    }
}
