//! What an `Idempotency-Key` changes, and the one branch where it changes anything.
//!
//! **This file exists because the behaviour existed and nothing exercised it.** `client.rs` has
//! retried a timed-out request under a key since the key was added — the whole reason to pass one —
//! and measured 2026-10-07 there was no Rust test that it did. Python and Go both had one. The
//! branch is three tokens long (`opts.idempotency_key.is_empty() ||`), which is exactly the size of
//! change that gets reverted by someone simplifying a condition they have no failing test for.
//!
//! The asymmetry is the point of every test here: a timeout **without** a key must not be retried,
//! because the backend is probably still generating and the repeat would be a second billable
//! generation rather than a resumption.

use axonium::{ChatRequest, Client, Config, Message, RetryPolicy, Timeouts};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, Request, Respond, ResponseTemplate};

/// Counts the attempts that reach the server and stalls every one of them past the client's
/// timeout. The count is the assertion: a policy that failed to reach the wire would be
/// indistinguishable from one that chose not to retry if only the error type were checked.
struct StallAndCount {
    seen: Arc<AtomicUsize>,
}

impl Respond for StallAndCount {
    fn respond(&self, _: &Request) -> ResponseTemplate {
        self.seen.fetch_add(1, Ordering::SeqCst);
        ResponseTemplate::new(200)
            .set_delay(Duration::from_secs(30))
            .set_body_json(serde_json::json!({}))
    }
}

async fn stalling_server(seen: Arc<AtomicUsize>) -> MockServer {
    let server = MockServer::start().await;
    Mock::given(path("/oauth2/token"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "access_token": "header.eyJzY29wZSI6ImluZmVyZW5jZTpyZWFkIn0.sig",
            "token_type": "Bearer",
            "expires_in": 300
        })))
        .mount(&server)
        .await;
    Mock::given(method("POST"))
        .and(path("/v1/chat/completions"))
        .respond_with(StallAndCount { seen })
        .mount(&server)
        .await;
    server
}

fn client(server: &MockServer) -> Client {
    Client::new(Config {
        gateway_base_url: server.uri(),
        client_id: "i".into(),
        client_secret: "s".into(),
        // Short enough that the stalled response times out promptly; the waits are removed so the
        // test measures which failures are retried, not how long the SDK will sleep.
        timeouts: Timeouts {
            request: Duration::from_millis(300),
            ..Default::default()
        },
        retry: RetryPolicy {
            max_attempts: 3,
            initial_backoff: Duration::ZERO,
            max_backoff: Duration::ZERO,
            ..Default::default()
        },
        ..Default::default()
    })
    .expect("building")
}

fn request() -> ChatRequest {
    ChatRequest {
        model: "m".into(),
        messages: vec![Message::text("user", "hi")],
        ..Default::default()
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn a_timeout_without_a_key_is_not_retried() {
    let seen = Arc::new(AtomicUsize::new(0));
    let server = stalling_server(Arc::clone(&seen)).await;

    let result = client(&server).chat(&request()).await;

    assert!(result.is_err(), "a stalled response should time out");
    // One attempt, and the number is the whole assertion. A retry here would queue a second
    // billable generation on top of a first that is probably still running.
    assert_eq!(
        seen.load(Ordering::SeqCst),
        1,
        "a timeout with no idempotency key must reach the gateway exactly once"
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn a_timeout_under_a_key_is_retried_to_the_policy_limit() {
    let seen = Arc::new(AtomicUsize::new(0));
    let server = stalling_server(Arc::clone(&seen)).await;

    let mut req = request();
    req.idempotency_key = "k-1".into();
    let result = client(&server).chat(&req).await;

    assert!(
        result.is_err(),
        "every attempt stalls, so the call still fails"
    );
    // Three, against the one above, under the same server and the same timeout. The pair is what
    // makes this a test of the key rather than of the retry policy: the only difference between
    // the two calls is the key.
    assert_eq!(
        seen.load(Ordering::SeqCst),
        3,
        "a key makes the repeat free, so a timeout is retried to max_attempts"
    );
}
