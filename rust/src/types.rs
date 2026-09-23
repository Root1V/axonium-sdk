//! Types shared across every response.
//!
//! Inference bodies are passed through from heterogeneous backends verbatim, so these keep the
//! decoded body rather than discarding what they do not model: a field this SDK does not know
//! about stays reachable instead of vanishing.

use serde::Deserialize;
use serde_json::Value;

/// Token accounting.
///
/// Every counter is an `Option` because `None` and `Some(0)` mean different things: nothing
/// measured it, versus it was measured and was zero. Embedding responses carry no completion count
/// because there is no generation phase, and llama.cpp-family backends report no usage at all when
/// streaming. Collapsing those onto zero would let a caller conclude a phase was free when in
/// truth it was never counted.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct Usage {
    pub prompt_tokens: Option<u64>,
    pub completion_tokens: Option<u64>,
    pub total_tokens: Option<u64>,

    /// How many of `prompt_tokens` were served from cache. A **subset** of the input count, not a
    /// separate bucket: the tri-party convention settled on inclusive because providers report it
    /// that way, so an adapter copies instead of subtracting -- and a forgotten subtraction
    /// double-counts the cache without producing any error.
    #[serde(default, deserialize_with = "cached_tokens")]
    pub cache_read_tokens: Option<u64>,

    /// True when the counts were derived from a backend `timings` object rather than reported.
    /// A derived figure must never be mistaken for a measured one: it is the difference between
    /// billing on a fact and billing on an inference.
    #[serde(skip)]
    pub estimated: bool,
}

/// Lifts the cached count out of the OpenAI-shaped `prompt_tokens_details`, where non-streaming
/// responses report it. Leaving it nested would mean telling a caller nobody measured the cache on
/// the one path where somebody did.
fn cached_tokens<'de, D: serde::Deserializer<'de>>(d: D) -> Result<Option<u64>, D::Error> {
    Ok(Option::<u64>::deserialize(d).unwrap_or(None))
}

impl Usage {
    pub(crate) fn absorb_details(&mut self, raw: &Value) {
        if self.cache_read_tokens.is_none() {
            self.cache_read_tokens = raw
                .get("prompt_tokens_details")
                .and_then(|d| d.get("cached_tokens"))
                .and_then(Value::as_u64);
        }
    }
}

/// The rate-limit budget as of one response.
///
/// The token figures are the gateway's post-hoc accounting rather than a pre-flight reservation,
/// so a burst of large requests can still exceed the budget between updates. A strong signal, not
/// a guarantee against ever seeing a 429.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct RateLimit {
    /// Which budget these numbers describe -- `"embeddings"`, `"rerank"`, `"chat_completions"`, or
    /// `"default"` for everything sharing the general bucket.
    ///
    /// Without it these counters cannot be attributed: the endpoints hold separate budgets, so a
    /// `remaining_requests` read after a chat call says nothing about the embeddings budget, and
    /// nothing in the numbers themselves reveals which one answered.
    ///
    /// `None` on a deployment predating per-endpoint budgets, or one predating guide
    /// `2026-09-19b`, which is when the header reached the 429 as well.
    pub scope: Option<String>,

    pub limit_requests: Option<u64>,
    pub remaining_requests: Option<u64>,
    pub reset_requests: Option<u64>,
    pub limit_tokens: Option<u64>,
    pub remaining_tokens: Option<u64>,
    pub reset_tokens: Option<u64>,
}

impl RateLimit {
    /// Whether the response carried no rate-limit headers at all.
    ///
    /// `scope` is excluded deliberately: it labels a budget rather than being one, so a response
    /// carrying only a scope has still reported no numbers. Written out rather than compared
    /// against `Self::default()`, which would quietly start counting the label as a budget.
    fn is_empty(&self) -> bool {
        self.limit_requests.is_none()
            && self.remaining_requests.is_none()
            && self.reset_requests.is_none()
            && self.limit_tokens.is_none()
            && self.remaining_tokens.is_none()
            && self.reset_tokens.is_none()
    }

    /// Fills in [`scope`](Self::scope) from a problem+json body when the header did not carry it.
    ///
    /// Measured against a deployment 2026-09-19: `X-RateLimit-Scope` was present on successful
    /// responses and **absent on the 429**, where the body carried `"scope"` instead. The platform
    /// closed that gap in guide `2026-09-19b` -- the header is now on both -- and this fallback
    /// stays anyway: a deployment predating the fix still omits it, and the rule costs nothing.
    ///
    /// The header wins when both are present, matching the rule already used for `Retry-After`.
    /// Today they cannot disagree; the rule is what protects the next envelope carrying only one.
    #[must_use]
    pub fn with_scope_from(mut self, body: Option<&serde_json::Value>) -> Self {
        if self.scope.is_none() {
            if let Some(scope) = body.and_then(|b| b.get("scope")).and_then(|v| v.as_str()) {
                self.scope = Some(scope.to_string());
            }
        }
        self
    }
}

/// Correlation and budget information, carried on successes as well as failures: correlating a
/// slow but successful call matters as much as correlating a failed one.
#[derive(Debug, Clone, Default)]
pub struct ResponseMeta {
    pub request_id: String,
    pub trace_id: String,
    /// Short label of the instance that served this response (`#1`, `#2`), unique within the
    /// model. Stable for an instance's life, but a number can return after the highest-numbered
    /// instance is deleted -- so log it for readability and key on `instance_id`.
    pub instance: String,
    /// Full id of the instance that served this response. This is what to report when asking the
    /// platform team about a slow or odd response.
    pub instance_id: String,
    /// True when this response was replayed from an `Idempotency-Key` rather than generated. A
    /// replay reached no model and recorded no usage, so its `usage` describes the original.
    pub idempotent_replay: bool,
    /// On a replay, the request id of the generation that was actually billed.
    ///
    /// A replay carries its own request id, and that id has no usage row of its own -- looking it
    /// up returns `404`, correctly, because replaying does not reach a model and is not billed.
    /// This is the id that *does* resolve, so it is the only way from the response a caller
    /// received to the charge it corresponds to. Empty on anything that is not a replay.
    pub idempotent_replay_of: String,
    pub rate_limit: Option<RateLimit>,

    /// How long this SDK spent deliberately asleep before the response arrived -- in practice a
    /// `Retry-After` it was asked to honour, which the gateway sets anywhere from 0 to 60s.
    ///
    /// Here because a wait that exists only as a log line is invisible by default: this SDK emits
    /// through `tracing` and does not install a subscriber, so a caller who never set one up sees
    /// a 36-second call and nothing explaining it. Three separate teams reported exactly that as a
    /// hang. A latency metric cannot read a log line, but it can read this.
    ///
    /// Deliberately excluded from any duration this SDK reports: sleeping is not service time.
    /// Subtract it from a wall-clock reading to get what the platform actually spent. Zero when
    /// nothing was retried.
    pub waited_for: std::time::Duration,
    /// How many HTTP attempts produced this response, counting the one that succeeded. `1` when it
    /// worked first time, so `attempts > 1` is the test for "this was retried".
    pub attempts: u32,
}

impl ResponseMeta {
    pub(crate) fn from_headers(headers: &reqwest::header::HeaderMap) -> Self {
        let text = |name: &str| {
            headers
                .get(name)
                .and_then(|v| v.to_str().ok())
                .unwrap_or_default()
                .to_string()
        };
        let number = |name: &str| {
            headers
                .get(name)
                .and_then(|v| v.to_str().ok())
                .and_then(|v| v.parse().ok())
        };

        let rate_limit = RateLimit {
            scope: headers
                .get("x-ratelimit-scope")
                .and_then(|v| v.to_str().ok())
                .map(str::to_string),
            limit_requests: number("x-ratelimit-limit-requests"),
            remaining_requests: number("x-ratelimit-remaining-requests"),
            reset_requests: number("x-ratelimit-reset-requests"),
            limit_tokens: number("x-ratelimit-limit-tokens"),
            remaining_tokens: number("x-ratelimit-remaining-tokens"),
            reset_tokens: number("x-ratelimit-reset-tokens"),
        };

        Self {
            request_id: text("x-request-id"),
            trace_id: text("x-trace-id"),
            instance: text("x-prometheus-instance"),
            instance_id: text("x-prometheus-instance-id"),
            idempotent_replay: text("idempotent-replay").eq_ignore_ascii_case("true"),
            idempotent_replay_of: text("x-idempotent-replay-of"),
            rate_limit: (!rate_limit.is_empty()).then_some(rate_limit),
            // Headers cannot know either: the wait is client-side state. The retry loop overwrites
            // both before the caller sees this. One attempt is the honest default -- zero would be
            // a count nothing can be true of.
            waited_for: std::time::Duration::ZERO,
            attempts: 1,
        }
    }
}

#[cfg(test)]
mod rate_limit_scope {
    use super::RateLimit;

    fn headers(pairs: &[(&str, &str)]) -> reqwest::header::HeaderMap {
        let mut map = reqwest::header::HeaderMap::new();
        for (name, value) in pairs {
            map.insert(
                reqwest::header::HeaderName::from_bytes(name.as_bytes()).unwrap(),
                value.parse().unwrap(),
            );
        }
        map
    }

    fn snapshot(pairs: &[(&str, &str)]) -> RateLimit {
        crate::types::ResponseMeta::from_headers(&headers(pairs))
            .rate_limit
            .expect("the headers carried a budget")
    }

    #[test]
    fn a_scope_alone_is_not_a_budget() {
        // is_empty decides whether a snapshot is worth reporting. A scope labels a budget rather
        // than being one, so a response carrying only a scope has still reported no numbers.
        assert!(crate::types::ResponseMeta::from_headers(&headers(&[(
            "x-ratelimit-scope",
            "rerank"
        )]))
        .rate_limit
        .is_none());
    }

    #[test]
    fn the_body_supplies_the_scope_when_the_header_does_not() {
        // Measured 2026-09-19, before PRM-130 put the header on the 429 too. Kept: a deployment
        // predating that fix still answers this way.
        let headerless = snapshot(&[("x-ratelimit-remaining-requests", "0")]);
        assert_eq!(headerless.scope, None);

        let filled = headerless.with_scope_from(Some(&serde_json::json!({"scope": "embeddings"})));
        assert_eq!(filled.scope.as_deref(), Some("embeddings"));
    }

    /// A defensive rule, not an observed response: nothing seen so far carries both.
    ///
    /// Pinned anyway because it is the same precedence already documented for `Retry-After`, and an
    /// unstated tie-break is one somebody re-decides differently later. Deliberately not a contract
    /// case -- the corpus holds recorded bytes, and inventing a response nobody has seen is how a
    /// fixture ends up describing a gateway that does not exist.
    #[test]
    fn the_header_wins_over_a_body_that_disagrees() {
        let from_header = snapshot(&[
            ("x-ratelimit-scope", "rerank"),
            ("x-ratelimit-remaining-requests", "0"),
        ]);

        let unchanged =
            from_header.with_scope_from(Some(&serde_json::json!({"scope": "embeddings"})));
        assert_eq!(unchanged.scope.as_deref(), Some("rerank"));
    }

    #[test]
    fn a_body_without_a_scope_leaves_it_unset() {
        let headerless = snapshot(&[("x-ratelimit-remaining-requests", "0")]);

        assert_eq!(
            headerless
                .clone()
                .with_scope_from(Some(&serde_json::json!({"detail": "none here"})))
                .scope,
            None
        );
        assert_eq!(headerless.with_scope_from(None).scope, None);
    }
}
