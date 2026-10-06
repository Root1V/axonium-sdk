//! Configuration resolution, and the defaults that make credentials the only required setting.

use axonium::{Client, Config, Error, DEFAULT_GATEWAY_BASE_URL};

fn credentials_only() -> Config {
    Config {
        client_id: "i".into(),
        client_secret: "s".into(),
        ..Default::default()
    }
}

/// An official SDK points at the official platform: making every consumer repeat the same two URLs
/// is friction for nothing.
#[test]
fn urls_default_to_the_official_platform() {
    // These tests share a process, so the environment is cleared rather than assumed empty.
    std::env::remove_var("AXONIUM_GATEWAY_BASE_URL");

    let client = Client::new(credentials_only()).expect("credentials alone should be enough");
    assert_eq!(client.config().gateway_base_url, DEFAULT_GATEWAY_BASE_URL);
}

/// Overriding one must not force restating the other: a self-hosted gateway fronted by the
/// official auth-service is a real shape.
/// There is one address, not two.
///
/// The platform used to run a separate auth-service that every consumer also had to configure, and
/// forgetting it left a client asking the official platform for a token to use somewhere else --
/// silently, since nothing errored. Overriding the gateway moves the token endpoint with it
/// because they are the same host.
#[test]
fn one_address_moves_both_inference_and_tokens() {
    std::env::remove_var("AXONIUM_GATEWAY_BASE_URL");

    let client = Client::new(Config {
        gateway_base_url: "https://mine.example".into(),
        ..credentials_only()
    })
    .expect("building");

    assert_eq!(client.config().gateway_base_url, "https://mine.example");
}

/// Defaulting removes the "you forgot one" error, not the "that is not a URL" one.
#[test]
fn a_malformed_url_still_names_itself_and_its_variable() {
    let error = Client::new(Config {
        gateway_base_url: "gateway.example".into(),
        ..credentials_only()
    })
    .expect_err("a scheme-less URL must be refused");

    let Error::Configuration(message) = error else {
        panic!("expected a configuration error, got {error:?}");
    };
    assert!(message.contains("gateway_base_url"), "{message}");
    assert!(message.contains("AXONIUM_GATEWAY_BASE_URL"), "{message}");
    assert!(!message.contains("auth_base_url"), "{message}");
}

/// Without credentials and without a provider there is nothing to authenticate with, and that is
/// still worth failing at construction for.
#[test]
fn credentials_are_still_required() {
    std::env::remove_var("AXONIUM_CLIENT_ID");
    std::env::remove_var("AXONIUM_CLIENT_SECRET");

    let error = Client::new(Config::default()).expect_err("no credentials must be refused");
    assert!(matches!(error, Error::Configuration(_)), "{error:?}");
}

/// A secret must not leak through `Debug`, which is the one trait everybody reaches for while
/// debugging -- and therefore the one that carries values into panic messages, tracing spans and
/// whatever collects them.
#[test]
fn the_secret_never_appears_in_debug_output() {
    let config = Config {
        client_id: "visible-id".into(),
        client_secret: "pmt_live_SUPERSECRET".into(),
        ..Default::default()
    };

    let rendered = format!("{config:?}");
    assert!(
        !rendered.contains("SUPERSECRET"),
        "the secret leaked through Debug: {rendered}"
    );
    assert!(rendered.contains("(redacted)"), "{rendered}");
    // The id is not a secret and is what identifies which client this is, so it stays.
    assert!(rendered.contains("visible-id"), "{rendered}");

    let client = Client::new(config).expect("building");
    let rendered = format!("{client:?}");
    assert!(
        !rendered.contains("SUPERSECRET"),
        "the secret leaked through the client: {rendered}"
    );
    assert!(rendered.contains("autonomous"), "{rendered}");
}

/// The contract case `PRM-187` introduced: `top_logprobs` without `logprobs: Some(true)`.
///
/// The rule is the **engine's** -- llama.cpp answers "top_logprobs requires logprobs to be set to
/// true" -- and the gateway enforces it before forwarding, so the refusal arrives as problem+json.
/// Checking it here is the difference between learning it at the call site and learning it after a
/// round trip. No recorded corpus case covers it, so this is the only thing holding the rule here.
mod logprobs {
    use axonium::{ChatRequest, Error, Message, TokenLogprob};

    fn request() -> ChatRequest {
        ChatRequest {
            model: "m".into(),
            messages: vec![Message::text("user", "x")],
            ..Default::default()
        }
    }

    // `validate` is private, so the request goes through the public surface that calls it. A test
    // reaching past that would assert about a function no caller can reach -- the exact shape this
    // repository keeps finding. The call never leaves the process: validation runs before the
    // transport does, so an unroutable base URL is never contacted.
    async fn refused(request: ChatRequest) -> bool {
        let client = axonium::Client::new(axonium::Config {
            gateway_base_url: "https://gw.test".into(),
            client_id: "i".into(),
            client_secret: "s".into(),
            ..Default::default()
        })
        .expect("client");
        matches!(client.chat(&request).await, Err(Error::InvalidRequest(_)))
    }

    #[tokio::test]
    async fn top_logprobs_alone_is_refused_before_the_wire() {
        let mut request = request();
        request.top_logprobs = Some(3);
        assert!(refused(request).await);
    }

    #[tokio::test]
    async fn top_logprobs_with_logprobs_false_is_refused_too() {
        // An SDK checking only for ABSENCE would send this one: `logprobs` is present, and wrong.
        let mut request = request();
        request.logprobs = Some(false);
        request.top_logprobs = Some(3);
        assert!(refused(request).await);
    }

    #[test]
    fn probability_is_exp_of_the_logprob() {
        // -0.00054 is ~99.95%, not ~0, and nothing about reading it the other way is loud.
        let token = TokenLogprob {
            logprob: -0.00054,
            ..Default::default()
        };
        assert!(token.probability() > 0.999 && token.probability() <= 1.0);
    }
}
