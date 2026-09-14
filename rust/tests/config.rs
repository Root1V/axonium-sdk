//! Configuration resolution, and the defaults that make credentials the only required setting.

use axonium::{Client, Config, Error, DEFAULT_AUTH_BASE_URL, DEFAULT_GATEWAY_BASE_URL};

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
    std::env::remove_var("AXONIUM_AUTH_BASE_URL");
    std::env::remove_var("AXONIUM_GATEWAY_BASE_URL");

    let client = Client::new(credentials_only()).expect("credentials alone should be enough");
    assert_eq!(client.config().auth_base_url, DEFAULT_AUTH_BASE_URL);
    assert_eq!(client.config().gateway_base_url, DEFAULT_GATEWAY_BASE_URL);
}

/// Overriding one must not force restating the other: a self-hosted gateway fronted by the
/// official auth-service is a real shape.
/// Pointing at a self-hosted gateway takes the token host with it.
///
/// The gateway issues tokens itself now, so the old behaviour -- keeping the official auth address
/// when only the gateway was overridden -- would silently ask the official platform for a token to
/// use somewhere else. Nothing errors in that shape, which is what makes it worth a test.
#[test]
fn the_token_host_follows_the_gateway() {
    std::env::remove_var("AXONIUM_AUTH_BASE_URL");
    std::env::remove_var("AXONIUM_GATEWAY_BASE_URL");

    let client = Client::new(Config {
        gateway_base_url: "https://mine.example".into(),
        ..credentials_only()
    })
    .expect("building");

    assert_eq!(client.config().gateway_base_url, "https://mine.example");
    assert_eq!(client.config().auth_base_url, "https://mine.example");
}

/// A deployment that still runs a separate auth-service says so, and is not overridden.
#[test]
fn a_separate_auth_service_is_still_addressable() {
    std::env::remove_var("AXONIUM_AUTH_BASE_URL");
    std::env::remove_var("AXONIUM_GATEWAY_BASE_URL");

    let client = Client::new(Config {
        gateway_base_url: "https://mine.example".into(),
        auth_base_url: "https://auth.mine.example".into(),
        ..credentials_only()
    })
    .expect("building");

    assert_eq!(client.config().auth_base_url, "https://auth.mine.example");
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
