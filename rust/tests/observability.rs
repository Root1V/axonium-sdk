//! What the `tracing` feature emits, and — more importantly — what it never does.
//!
//! These only run with `--features tracing`; without it the module compiles to nothing, which is
//! the point.
#![cfg(feature = "tracing")]

use std::sync::{Arc, Mutex};

use axonium::{ChatRequest, Client, Config, Message};
use tracing_subscriber::fmt::MakeWriter;
use wiremock::matchers::any;
use wiremock::{Mock, MockServer, ResponseTemplate};

/// Collects everything the subscriber writes, so a test can assert on the whole emission rather
/// than on one field at a time.
#[derive(Clone, Default)]
struct Collected(Arc<Mutex<Vec<u8>>>);

impl Collected {
    fn text(&self) -> String {
        String::from_utf8_lossy(&self.0.lock().unwrap()).to_string()
    }
}

impl std::io::Write for Collected {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        self.0.lock().unwrap().extend_from_slice(buf);
        Ok(buf.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

impl<'a> MakeWriter<'a> for Collected {
    type Writer = Self;
    fn make_writer(&'a self) -> Self::Writer {
        self.clone()
    }
}

async fn call_with_subscriber(collected: &Collected) {
    let server = MockServer::start().await;
    Mock::given(wiremock::matchers::path("/oauth2/token"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "access_token": "header.eyJzY29wZSI6ImluZmVyZW5jZTpyZWFkIn0.sig",
            "token_type": "Bearer",
            "expires_in": 300
        })))
        .mount(&server)
        .await;
    Mock::given(any())
        .respond_with(
            ResponseTemplate::new(200)
                .insert_header("x-request-id", "req-42")
                .insert_header("x-trace-id", "trace-42")
                .insert_header("x-prometheus-instance-id", "qwen3-0-6b-iq4-nl-local-1")
                .set_body_json(serde_json::json!({
                    "id": "c1",
                    "model": "qwen3-0.6b",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "the answer"}}]
                })),
        )
        .mount(&server)
        .await;

    let subscriber = tracing_subscriber::fmt()
        .with_writer(collected.clone())
        // Without this the field names arrive wrapped in escape sequences and no assertion on a
        // literal substring can match.
        .with_ansi(false)
        .with_max_level(tracing::Level::DEBUG)
        .finish();

    let client = Client::new(Config {
        gateway_base_url: server.uri(),
        client_id: "id-should-not-appear".into(),
        client_secret: "secret-should-not-appear".into(),
        ..Default::default()
    })
    .expect("building");

    let request = ChatRequest {
        model: "qwen3-0.6b".into(),
        messages: vec![Message::text("user", "a very secret prompt about payroll")],
        ..Default::default()
    };

    tracing::subscriber::with_default(subscriber, || {
        futures_executor_block_on(client.chat(&request)).expect("the call should succeed");
    });
}

/// The test runtime is already async, so the call is driven on the current thread inside the
/// subscriber guard rather than spawned past it.
fn futures_executor_block_on<F: std::future::Future>(future: F) -> F::Output {
    tokio::task::block_in_place(|| tokio::runtime::Handle::current().block_on(future))
}

/// The promise that makes the whole field set safe to emit at any level: nothing here is content.
/// A library that can be configured to log prompts is how prompts reach a collector nobody
/// audited, so there is no such configuration.
#[tokio::test(flavor = "multi_thread")]
async fn records_metadata_and_never_content() {
    let collected = Collected::default();
    call_with_subscriber(&collected).await;
    let emitted = collected.text();

    for expected in [
        "request_id=\"req-42\"",
        "trace_id=\"trace-42\"",
        "instance_id=\"qwen3-0-6b-iq4-nl-local-1\"",
        "status=200",
        "attempt=1",
        "duration_ms",
        "gen_ai.request.model",
    ] {
        assert!(
            emitted.contains(expected),
            "should carry {expected}\n{emitted}"
        );
    }

    for forbidden in [
        "secret prompt",
        "payroll",
        "secret-should-not-appear",
        "id-should-not-appear",
        "the answer",
        "Bearer",
    ] {
        assert!(
            !emitted.contains(forbidden),
            "must never carry {forbidden}\n{emitted}"
        );
    }
}

/// A wait a caller would notice has to say so, or it arrives as a latency bug.
///
/// The platform warned us about this shape directly: they were sent a report of "requests hanging
/// 30-60 seconds with no clean load threshold". They were not hangs. Their `Retry-After` on a `429`
/// is seconds until the window resets, so it runs 0-60, and an SDK that respects it -- as it should
/// -- looks from outside like one slow call among fast ones.
async fn call_retrying_after(collected: &Collected, retry_after: &str, level: tracing::Level) {
    let server = MockServer::start().await;
    Mock::given(wiremock::matchers::path("/oauth2/token"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "access_token": "header.eyJzY29wZSI6ImluZmVyZW5jZTpyZWFkIn0.sig",
            "token_type": "Bearer",
            "expires_in": 300
        })))
        .mount(&server)
        .await;
    Mock::given(any())
        .respond_with(
            ResponseTemplate::new(429)
                .insert_header("retry-after", retry_after)
                .set_body_json(serde_json::json!({
                    "type": "https://prometheus.internal/errors/rate-limit-exceeded-requests",
                    "status": 429
                })),
        )
        .mount(&server)
        .await;

    let subscriber = tracing_subscriber::fmt()
        .with_writer(collected.clone())
        .with_ansi(false)
        .with_max_level(level)
        .finish();

    let client = Client::new(Config {
        gateway_base_url: server.uri(),
        client_id: "i".into(),
        client_secret: "s".into(),
        ..Default::default()
    })
    .expect("building");

    tracing::subscriber::with_default(subscriber, || {
        // The same pattern the test above uses: driven on the current thread inside the subscriber
        // guard rather than spawned past it, so the events land in this collector.
        let _ = futures_executor_block_on(client.models());
    });
}

#[tokio::test(flavor = "multi_thread")]
async fn a_long_retry_wait_is_reported_at_info() {
    let collected = Collected::default();
    call_retrying_after(&collected, "2", tracing::Level::INFO).await;

    let text = collected.text();
    assert!(
        text.contains("waiting before a retry"),
        "a two-second wait left nothing at INFO to explain it:\n{text}"
    );
    assert!(
        text.contains("delay_s=2"),
        "the wait was not reported with its length:\n{text}"
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn a_short_backoff_stays_at_debug() {
    // The noise worry is frequent small retries, not the rare long one. A sub-second backoff that
    // shouted at INFO would train people to filter the level that matters.
    let collected = Collected::default();
    call_retrying_after(&collected, "0", tracing::Level::INFO).await;

    let text = collected.text();
    assert!(
        !text.contains("waiting before a retry"),
        "a zero-second wait should not reach INFO:\n{text}"
    );
}
