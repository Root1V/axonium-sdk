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
    // initial_backoff == max_backoff, so both delays are exactly WAIT: the policy caps every
    // backoff at max_backoff, which is documented behaviour rather than this test reproducing the
    // doubling formula. Two retries then have to total exactly 2 * WAIT -- an implementation that
    // overwrote instead of accumulating would report WAIT, and one that recorded nothing would
    // report zero. All three are distinguishable by equality, with no tolerance to tune.
    const WAIT: Duration = Duration::from_millis(60);
    let retry = axonium::RetryPolicy {
        max_attempts: 3,
        initial_backoff: WAIT,
        max_backoff: WAIT,
        jitter: false,
        ..Default::default()
    };
    let client = client_for(&server, retry);
    let started = std::time::Instant::now();
    let result = client
        .models_mine()
        .await
        .expect("the retries should succeed");
    let elapsed = started.elapsed();

    assert_eq!(result.meta.attempts, 3);
    assert_eq!(
        result.meta.waited_for,
        WAIT * 2,
        "must be the sum of both waits, not the last one and not nothing"
    );
    // The wall clock is a sanity check, not the oracle. An earlier version of this test made it
    // the oracle -- asserting that elapsed and waited_for were within 40ms -- and it failed on a
    // loaded machine where the mock server and three round trips cost 128ms. It was right about
    // the code and wrong about the machine, which is the worst way for a test to fail.
    assert!(
        elapsed >= result.meta.waited_for,
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
