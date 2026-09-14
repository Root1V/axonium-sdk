//! Per-request usage lookup.
//!
//! Answers the one question a caller cannot otherwise answer about their own bill: *this request
//! was charged — why did it stop?* Both aggregate usage endpoints require `admin:read`, which a
//! normal client neither has nor should have; this one reads exactly the caller's own row.

use serde::Deserialize;
use serde_json::Value;

use crate::client::Client;
use crate::error::{Error, Result};
use crate::types::{ResponseMeta, Usage};

/// Percent-encodes one path segment, per RFC 3986's unreserved set.
///
/// The id comes from a caller who may have stored or mistyped it, and an unescaped separator would
/// silently address a different route. Hand-rolled rather than pulling in a dependency for eleven
/// lines, which is also why it is tested rather than trusted.
fn escape_path_segment(value: &str) -> String {
    value
        .bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'.' | b'_' | b'~' => {
                (b as char).to_string()
            }
            other => format!("%{other:02X}"),
        })
        .collect()
}

/// The billing record for one request.
///
/// Every field is optional. The gateway guarantees the row exists for a billed request, not which
/// columns a given deployment populates — `cost_usd` is `None` where no price is configured, and
/// new columns are appended over time.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct RequestUsage {
    /// The request id this row describes, which should match what was asked for.
    #[serde(default)]
    pub request_id: String,
    #[serde(default)]
    pub model: String,
    /// `"chat"`, `"embeddings"` or `"images"` — what kind of request was billed.
    #[serde(default)]
    pub request_kind: String,

    /// Mirrors an inference response field for field. `cache_read_tokens` is a subset of
    /// `prompt_tokens`, the same convention as everywhere else, so a row and the response it
    /// describes can be compared without arithmetic.
    #[serde(default)]
    pub usage: Option<Usage>,

    #[serde(default)]
    pub image_count: Option<u64>,

    /// Whether the caller received less than the whole answer. Derived from `termination_reason`
    /// by the gateway and never assigned separately, so the two cannot disagree.
    #[serde(default)]
    pub interrupted: Option<bool>,

    /// `"complete"`, `"upstream_error"` or `"client_disconnected"` today.
    ///
    /// Deliberately a `String` rather than an enum. The platform proposed a fourth value this week
    /// and withdrew it; the next one may not be withdrawn, and an exhaustive match would turn a
    /// new value into a parse failure for a caller who only wanted the token counts.
    #[serde(default)]
    pub termination_reason: String,

    /// `None` where the deployment has no price configured, which is not the same as free.
    #[serde(default)]
    pub cost_usd: Option<f64>,

    /// Which replica served it — the value to quote when asking the platform team about this row.
    #[serde(default)]
    pub instance_id: String,

    #[serde(default)]
    pub created_at: String,

    /// The decoded body as received, so a column this SDK does not model stays reachable.
    #[serde(skip)]
    pub raw: Value,
    #[serde(skip)]
    pub meta: ResponseMeta,
}

impl Client {
    /// The usage row for `request_id`, which comes from any response's `meta.request_id`.
    ///
    /// A missing row is [`Error::Api`] with [`crate::ErrorKind::NotFound`]. That covers three
    /// cases the gateway deliberately does not distinguish — the id is not yours, the id never
    /// existed, and the id belongs to a **replay**. A replay reaches no model and is not billed,
    /// so it has no row of its own; use `meta.idempotent_replay_of` to get the id of the
    /// generation that *was* charged, and look that up instead.
    pub async fn usage(&self, request_id: &str) -> Result<RequestUsage> {
        let path = format!("/v1/usage/{}", escape_path_segment(request_id));

        let (response, meta) = self
            .send(&path, None, &crate::client::CallOptions::default())
            .await?;
        let raw: Value = response.json().await.map_err(|e| {
            Error::Transport(format!(
                "the gateway returned a usage row this SDK could not parse: {e}"
            ))
        })?;
        let mut row: RequestUsage = serde_json::from_value(raw.clone()).map_err(|e| {
            Error::Transport(format!(
                "the gateway returned a usage row this SDK could not parse: {e}"
            ))
        })?;
        // The cached count arrives nested under prompt_tokens_details and is lifted by an
        // explicit call rather than by serde, because a field deserializer cannot see its
        // siblings. Forgetting it here reported "nobody measured the cache" on the one path where
        // somebody did -- which is exactly what the contract case caught.
        if let (Some(usage), Some(raw_usage)) = (row.usage.as_mut(), raw.get("usage")) {
            usage.absorb_details(raw_usage);
        }
        row.raw = raw;
        row.meta = meta;
        Ok(row)
    }
}

#[cfg(test)]
mod tests {
    //! The id going *out*. The recorded contract cases all carry well-formed UUIDs, so none of
    //! them can tell an escaped path from an unescaped one -- and this escaper is hand-rolled
    //! rather than a library call, which is the reason to test it rather than trust it.
    //!
    //! These tests import the real function. A first draft defined a copy of it here and every
    //! mutation passed, which is the same failure the whole corpus discipline exists to catch:
    //! the test checked the step before the one its name promised.

    use super::escape_path_segment as escape;

    #[test]
    fn a_well_formed_id_passes_through_untouched() {
        let id = "a0f3ec1b-25e5-4025-abba-73bec9c8b390";
        assert_eq!(escape(id), id);
    }

    #[test]
    fn a_separator_cannot_address_another_route() {
        // An id a caller stored, mistyped or built by concatenation can contain a slash. Left
        // unescaped it would silently address a different path -- the aggregate usage route, which
        // needs admin:read and would come back as a confusing 403 rather than a 404.
        let escaped = escape("../usage?limit=1000");
        assert!(
            !escaped.contains('/'),
            "a path separator survived: {escaped}"
        );
        assert!(
            !escaped.contains('?'),
            "a query separator survived: {escaped}"
        );
    }

    #[test]
    fn non_ascii_is_percent_encoded_per_byte() {
        // UTF-8 is encoded byte by byte, which is what RFC 3986 requires; encoding the char would
        // produce something no server decodes back.
        assert_eq!(escape("ñ"), "%C3%B1");
    }
}
