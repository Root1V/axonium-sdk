//! A failed token request can arrive in either of two shapes, and they mean opposite things.
//!
//! Not in the shared contract corpus because the token endpoint is not in it at all -- each runner
//! mocks it per case rather than exercising it. Recorded as its own gap rather than papered over.

async fn token_failing(status: u16, body: &str) -> (wiremock::MockServer, axonium::Client) {
    let server = wiremock::MockServer::start().await;
    wiremock::Mock::given(wiremock::matchers::path("/oauth2/token"))
        .respond_with(
            wiremock::ResponseTemplate::new(status)
                .set_body_raw(body.to_string(), "application/json"),
        )
        .mount(&server)
        .await;

    let client = axonium::Client::new(axonium::Config {
        client_id: "i".into(),
        client_secret: "s".into(),
        gateway_base_url: server.uri(),
        ..Default::default()
    })
    .expect("building");
    (server, client)
}

#[tokio::test]
async fn a_4xx_from_the_token_endpoint_is_an_oauth2_error() {
    let (_server, client) = token_failing(
        401,
        r#"{"error":"invalid_client","error_description":"Invalid."}"#,
    )
    .await;

    let error = client.models().await.unwrap_err();

    match error {
        axonium::Error::OAuth { code, .. } => assert_eq!(code, "invalid_client"),
        other => panic!("got {other:?}"),
    }
}

#[tokio::test]
async fn a_503_from_the_token_endpoint_is_the_gateway_failing_and_is_retryable() {
    // The distinction that matters: reading this as OAuth2 would give it no kind and no
    // retryability, so a momentary blip would look exactly like bad credentials and the caller
    // would abandon a request that was about to succeed.
    let (_server, client) = token_failing(
        503,
        r#"{"type":"https://prometheus.internal/errors/upstream-unavailable","status":503,"detail":"x"}"#,
    )
    .await;

    let error = client.models().await.unwrap_err();

    match error {
        axonium::Error::Api(api) => {
            assert_eq!(api.kind, axonium::ErrorKind::TokenEndpointUnavailable);
            assert!(api.retryable(), "a gateway failure should be retryable");
        }
        other => panic!("a gateway failure typed as {other:?}"),
    }
}

#[tokio::test]
async fn a_deployment_without_a_token_endpoint_is_not_retryable() {
    // Same status as the one above and the opposite answer, which is why the suffix has to drive
    // the decision rather than the status.
    let (_server, client) = token_failing(
        503,
        r#"{"type":"https://prometheus.internal/errors/not-configured","status":503,"detail":"x"}"#,
    )
    .await;

    match client.models().await.unwrap_err() {
        axonium::Error::Api(api) => assert!(!api.retryable()),
        other => panic!("got {other:?}"),
    }
}
