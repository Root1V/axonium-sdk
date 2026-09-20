//! A retried call must be able to explain its own duration without anyone reading a log.
//!
//! This SDK emits through `tracing` and installs no subscriber, so the INFO event announcing a wait
//! is invisible unless the application set one up. Three teams reported a respected `Retry-After`
//! as a hang because of exactly that. `ResponseMeta` is the copy of the answer that needs no
//! configuration and cannot be missed.

use axonium::{Client, Config};
use std::time::Duration;
use wiremock::matchers::{any, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

async fn server_failing(times: u64) -> MockServer {
    let server = MockServer::start().await;
    Mock::given(path("/oauth2/token"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "access_token": "header.eyJzY29wZSI6ImluZmVyZW5jZTpyZWFkIn0.sig",
            "token_type": "Bearer",
            "expires_in": 300
        })))
        .mount(&server)
        .await;
    if times > 0 {
        // No `retry-after`: the policy's own backoff decides, which is what the assertions below
        // interrogate it about.
        Mock::given(any())
            .respond_with(ResponseTemplate::new(429).set_body_json(serde_json::json!({
                "type": "https://prometheus.internal/errors/rate-limit-exceeded-requests",
                "status": 429
            })))
            .up_to_n_times(times)
            .mount(&server)
            .await;
    }
    Mock::given(any())
        .respond_with(
            ResponseTemplate::new(200)
                .set_body_json(serde_json::json!({"object": "list", "data": []})),
        )
        .mount(&server)
        .await;
    server
}

fn client_for(server: &MockServer, retry: axonium::RetryPolicy) -> Client {
    Client::new(Config {
        gateway_base_url: server.uri(),
        client_id: "i".into(),
        client_secret: "s".into(),
        retry,
        ..Default::default()
    })
    .expect("building")
}

#[tokio::test(flavor = "multi_thread")]
async fn meta_reports_the_total_waited_and_how_many_attempts() {
    let server = server_failing(2).await;
    // Two different delays, deliberately: an implementation that overwrote instead of accumulating
    // would still agree with a single retry, or with two equal ones.
    let retry = axonium::RetryPolicy {
        max_attempts: 3,
        initial_backoff: Duration::from_millis(60),
        max_backoff: Duration::from_secs(1),
        jitter: false,
        ..Default::default()
    };
    // `backoff` is crate-private, so this cannot ask the policy what it would return. The oracle
    // is the wall clock instead: the mock server is local, so nearly all of a retried call's
    // elapsed time is the sleeping, and `waited_for` has to account for it. That is a measurement,
    // not this test reproducing the backoff arithmetic and then agreeing with itself.
    //
    // With 60ms doubling once, the sum is ~180ms and the last delay alone is ~120ms -- 60ms apart,
    // well outside the tolerance below. So an implementation that overwrote instead of
    // accumulating fails here rather than passing by coincidence.
    let client = client_for(&server, retry);
    let started = std::time::Instant::now();
    let result = client
        .models_mine()
        .await
        .expect("the retries should succeed");
    let elapsed = started.elapsed();

    assert_eq!(result.meta.attempts, 3);
    assert!(
        result.meta.waited_for > Duration::ZERO,
        "two retries slept, so this cannot be zero"
    );
    let gap = elapsed.saturating_sub(result.meta.waited_for);
    assert!(
        gap < Duration::from_millis(40),
        "the call took {elapsed:?} but only accounts for {:?} of it. The unexplained {gap:?} is \
         the size of a delay that went unrecorded -- the sum of the waits is what belongs here, \
         not the last one",
        result.meta.waited_for
    );
    assert!(
        result.meta.waited_for <= elapsed,
        "cannot have slept {:?} inside a call that took {elapsed:?}",
        result.meta.waited_for
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn meta_on_a_first_time_success_waited_for_nothing() {
    let server = server_failing(0).await;
    let client = client_for(&server, axonium::RetryPolicy::default());

    let result = client.models_mine().await.expect("listing");

    assert_eq!(result.meta.waited_for, Duration::ZERO);
    // Zero would be a count nothing can be true of: something served this response.
    assert_eq!(result.meta.attempts, 1);
}

// Not tested here: what meta says after a call that waited and then failed anyway -- the call whose
// duration most needs explaining. `Error` carries no `ResponseMeta` in this SDK, exactly as the
// Python one raises without it, so there is nothing a caller could read. Recorded as AXO-91.
