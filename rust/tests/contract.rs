//! Replays the shared contract corpus.
//!
//! `spec/cases/manifest.json` is the source of truth for every language SDK. Python, Go and Rust
//! replay the same cases against the same recorded wire bytes, so identical behaviour is enforced
//! by construction rather than by parallel hand-written suites that drift apart.
//!
//! A case that passes here does not prove the gateway behaves this way -- each case says in its
//! own `$comment` whether it was recorded from a live deployment or authored, and only the
//! recorded ones are evidence about the platform.

use std::path::{Path, PathBuf};

use axonium::{ChatRequest, Client, Config, ErrorKind, Message};
use serde_json::Value;
use wiremock::matchers::any;
use wiremock::{Mock, MockServer, ResponseTemplate};

fn spec_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("spec")
}

/// The corpus lives in the monorepo, one level above this crate, so it is present when the tests
/// run from a checkout and absent from the published package. Returning `None` there is the honest
/// outcome: the case files simply are not shipped, and panicking would fail `cargo test` for every
/// consumer who vendors this crate.
fn manifest() -> Option<Value> {
    let path = spec_dir().join("cases").join("manifest.json");
    if !path.exists() {
        return None;
    }
    let raw = std::fs::read_to_string(&path).expect("the shared manifest must be readable");
    Some(serde_json::from_str(&raw).expect("the shared manifest must parse"))
}

fn fixture(name: &str) -> Vec<u8> {
    std::fs::read(spec_dir().join("fixtures").join(name))
        .unwrap_or_else(|e| panic!("fixture {name}: {e}"))
}

/// Stands the recorded response up behind a mock, including the token endpoint every client needs.
async fn serve(case: &Value) -> MockServer {
    let server = MockServer::start().await;
    let response = &case["response"];
    let status = response["status"].as_u64().unwrap_or(200) as u16;

    let (body, content_type) = match (response.get("body_file"), response.get("sse_file")) {
        (Some(f), _) => (fixture(f.as_str().unwrap()), "application/json"),
        (_, Some(f)) => (fixture(f.as_str().unwrap()), "text/event-stream"),
        _ => panic!("{}: no fixture", case["id"]),
    };

    let mut template = ResponseTemplate::new(status)
        .set_body_bytes(body)
        .insert_header("content-type", content_type);
    if let Some(headers) = response.get("headers").and_then(Value::as_object) {
        for (name, value) in headers {
            template = template.insert_header(name.as_str(), value.as_str().unwrap_or_default());
        }
    }

    // The token endpoint first, so it wins over the catch-all.
    Mock::given(wiremock::matchers::path("/oauth2/token"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "access_token": "header.eyJzY29wZSI6ImluZmVyZW5jZTpyZWFkIn0.sig",
            "token_type": "Bearer",
            "expires_in": 300
        })))
        .mount(&server)
        .await;
    Mock::given(any())
        .respond_with(template)
        .mount(&server)
        .await;
    server
}

fn client(url: &str) -> Client {
    Client::new(Config {
        auth_base_url: url.into(),
        gateway_base_url: url.into(),
        client_id: "test".into(),
        client_secret: "test".into(),
        ..Default::default()
    })
    .expect("building the client")
}

fn chat_request(request: &Value) -> ChatRequest {
    ChatRequest {
        model: request["model"].as_str().unwrap_or_default().into(),
        messages: request["messages"]
            .as_array()
            .map(|items| {
                items
                    .iter()
                    .map(|m| Message {
                        role: m["role"].as_str().unwrap_or_default().into(),
                        content: m.get("content").cloned(),
                        reasoning_content: None,
                        tool_calls: None,
                    })
                    .collect()
            })
            .unwrap_or_default(),
        max_tokens: request
            .get("max_tokens")
            .and_then(Value::as_u64)
            .map(|v| v as u32),
        instance: request
            .get("instance")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .into(),
        idempotency_key: request
            .get("idempotency_key")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .into(),
        ..Default::default()
    }
}

/// Walks a dotted path, integer segments indexing into lists, matching the manifest's documented
/// convention.
fn resolve<'a>(root: &'a Value, path: &str) -> Option<&'a Value> {
    let mut node = root;
    for segment in path.split('.') {
        node = match segment.parse::<usize>() {
            Ok(index) => node.get(index)?,
            Err(_) => node.get(segment)?,
        };
    }
    Some(node)
}

fn expect_fields(result: &Value, case: &Value) {
    let Some(fields) = case["expect"].get("fields").and_then(Value::as_object) else {
        return;
    };
    for (path, want) in fields {
        let got = resolve(result, path).cloned().unwrap_or(Value::Null);
        assert_eq!(&got, want, "{}: {path}", case["id"]);
    }
}

fn kind_for(suffix: &str) -> ErrorKind {
    match suffix {
        "unknown-model" => ErrorKind::UnknownModel,
        "modality-mismatch" => ErrorKind::ModalityMismatch,
        "validation-error" => ErrorKind::ValidationError,
        "forbidden" => ErrorKind::Forbidden,
        "unknown-instance" => ErrorKind::UnknownInstance,
        "invalid-idempotency-key" => ErrorKind::InvalidIdempotencyKey,
        "idempotency-key-reuse" => ErrorKind::IdempotencyKeyReuse,
        "idempotency-in-progress" => ErrorKind::IdempotencyInProgress,
        "idempotency-response-not-retained" => ErrorKind::IdempotencyResponseNotRetained,
        "not-found" => ErrorKind::NotFound,
        other => panic!("the manifest names an error this SDK does not map: {other}"),
    }
}

#[tokio::test]
async fn contract_corpus() {
    let Some(manifest) = manifest() else {
        println!(
            "skipped: the shared corpus lives in the monorepo's spec/ and is not part of the \
             published crate. Run these from a checkout of Root1V/axonium-sdk."
        );
        return;
    };
    let cases = manifest["cases"].as_array().expect("cases");
    assert!(
        !cases.is_empty(),
        "a runner that asserts nothing passes forever"
    );

    let mut ran = 0;
    for case in cases {
        let id = case["id"].as_str().unwrap();
        let kind = case["expect"]["kind"].as_str().unwrap();
        let operation = case["operation"].as_str().unwrap();
        let server = serve(case).await;
        let client = client(&server.uri());

        match (kind, operation) {
            ("ok", "chat.completions.create") => {
                let completion = client
                    .chat(&chat_request(&case["request"]))
                    .await
                    .unwrap_or_else(|e| panic!("{id}: {e}"));
                let mut view = completion.raw.clone();
                // The manifest resolves a path against the SDK's own accessors where it exposes
                // them, so a case asserting "content" checks what a caller would actually read.
                view["content"] = Value::String(completion.content());
                // Through serde rather than grafted on directly: the manifest resolves paths
                // like tool_calls.0.function.name by walking JSON, and a typed struct is not.
                view["tool_calls"] = serde_json::to_value(completion.tool_calls())
                    .expect("tool calls are serialisable");
                // Overlaid rather than replaced. The view is otherwise the raw payload, so any
                // value the SDK *derives* -- cache_read_tokens is lifted out of the nested
                // prompt_tokens_details -- is invisible to a manifest path and therefore
                // unassertable. Merging keeps both the raw keys the corpus already pins and the
                // derived ones a caller actually reads.
                if let Some(usage) = completion.usage.as_ref() {
                    let derived = serde_json::json!({
                        "prompt_tokens": usage.prompt_tokens,
                        "completion_tokens": usage.completion_tokens,
                        "total_tokens": usage.total_tokens,
                        "cache_read_tokens": usage.cache_read_tokens,
                    });
                    match (view.get_mut("usage"), derived) {
                        (Some(Value::Object(raw)), Value::Object(extra)) => {
                            for (key, value) in extra {
                                if !value.is_null() {
                                    raw.insert(key, value);
                                }
                            }
                        }
                        (_, derived) => view["usage"] = derived,
                    }
                }
                // Every field a caller can read, not just the three the first cases happened
                // to assert: anything missing here is unassertable by the manifest, which is how
                // the cached-token lift went unverified in all three languages (AXO-81).
                view["meta"] = serde_json::json!({
                    "request_id": completion.meta.request_id,
                    "trace_id": completion.meta.trace_id,
                    "instance": completion.meta.instance,
                    "instance_id": completion.meta.instance_id,
                    "idempotent_replay": completion.meta.idempotent_replay,
                    "idempotent_replay_of": completion.meta.idempotent_replay_of,
                    "rate_limit": completion.meta.rate_limit.as_ref().map(|r| serde_json::json!({
                        "limit_requests": r.limit_requests,
                        "remaining_requests": r.remaining_requests,
                        "remaining_tokens": r.remaining_tokens,
                    })),
                });
                expect_fields(&view, case);
            }
            ("ok", "models.list") => expect_fields(&client.models().await.unwrap().raw, case),
            ("ok", "models.mine") => expect_fields(&client.models_mine().await.unwrap().raw, case),
            ("ok", "usage.retrieve") => {
                let request_id = case["request"]["request_id"].as_str().unwrap_or_default();
                let row = client
                    .usage(request_id)
                    .await
                    .unwrap_or_else(|e| panic!("{id}: {e}"));
                let mut view = row.raw.clone();
                // Same overlay as the completion view: cache_read_tokens is lifted out of the
                // nested prompt_tokens_details, so the raw payload alone cannot assert it.
                if let (Some(Value::Object(raw)), Some(usage)) =
                    (view.get_mut("usage"), row.usage.as_ref())
                {
                    if let Some(cached) = usage.cache_read_tokens {
                        raw.insert("cache_read_tokens".into(), cached.into());
                    }
                }
                expect_fields(&view, case);
            }
            ("ok", "embeddings.create") => {
                let request = &case["request"];
                let list = client
                    .embeddings(&axonium::EmbeddingRequest {
                        model: request["model"].as_str().unwrap_or_default().into(),
                        input: request["input"]
                            .as_array()
                            .map(|a| {
                                a.iter()
                                    .filter_map(|v| v.as_str().map(str::to_string))
                                    .collect()
                            })
                            .unwrap_or_default(),
                        ..Default::default()
                    })
                    .await
                    .unwrap_or_else(|e| panic!("{id}: {e}"));
                expect_fields(&list.raw, case);
            }
            ("ok", "images.generate") => {
                let request = &case["request"];
                let list = client
                    .images(&axonium::ImageRequest {
                        model: request["model"].as_str().unwrap_or_default().into(),
                        prompt: request["prompt"].as_str().unwrap_or_default().into(),
                        ..Default::default()
                    })
                    .await
                    .unwrap_or_else(|e| panic!("{id}: {e}"));
                expect_fields(&list.raw, case);
            }
            ("stream", _) | ("stream_error", _) => {
                let mut stream = client
                    .chat_stream(&chat_request(&case["request"]))
                    .await
                    .unwrap();
                let mut chunks = 0;
                let mut failure = None;
                loop {
                    match stream.next().await {
                        Ok(Some(_)) => chunks += 1,
                        Ok(None) => break,
                        Err(e) => {
                            failure = Some(e);
                            break;
                        }
                    }
                }

                if kind == "stream_error" {
                    let Some(axonium::Error::StreamInterrupted {
                        partial_content, ..
                    }) = failure
                    else {
                        panic!("{id}: expected an in-band interruption, got {failure:?}");
                    };
                    if let Some(want) = case["expect"]["partial_content"].as_str() {
                        assert_eq!(partial_content, want, "{id}: partial content");
                    }
                } else {
                    assert!(failure.is_none(), "{id}: the stream failed: {failure:?}");
                    if let Some(want) = case["expect"]["chunks"].as_u64() {
                        assert_eq!(chunks, want, "{id}: chunk count");
                    }
                    if let Some(want) = case["expect"]["content"].as_str() {
                        assert_eq!(stream.content(), want, "{id}: content");
                    }
                    // Absent on every case but the two tool-call ones, where it is compared in
                    // full. Asserting the empty case too is what stops a reassembler from
                    // inventing calls out of a stream that carried none.
                    let want_calls = case["expect"]
                        .get("tool_calls")
                        .cloned()
                        .unwrap_or_else(|| Value::Array(Vec::new()));
                    let got_calls = serde_json::to_value(stream.tool_calls())
                        .expect("tool calls are serialisable");
                    assert_eq!(got_calls, want_calls, "{id}: tool calls");
                    match (case["expect"].get("usage"), stream.usage()) {
                        (Some(Value::Null) | None, usage) => {
                            assert!(usage.is_none(), "{id}: expected no usage, got {usage:?}")
                        }
                        (Some(want), Some(usage)) => {
                            for (key, value) in want.as_object().unwrap() {
                                let got = match key.as_str() {
                                    "prompt_tokens" => serde_json::json!(usage.prompt_tokens),
                                    "completion_tokens" => {
                                        serde_json::json!(usage.completion_tokens)
                                    }
                                    "total_tokens" => serde_json::json!(usage.total_tokens),
                                    "cache_read_tokens" => {
                                        serde_json::json!(usage.cache_read_tokens)
                                    }
                                    "estimated" => serde_json::json!(usage.estimated),
                                    other => panic!("{id}: unknown usage key {other}"),
                                };
                                assert_eq!(&got, value, "{id}: usage.{key}");
                            }
                        }
                        (Some(want), None) => panic!("{id}: expected usage {want}, got none"),
                    }
                }
            }
            ("error", _) => {
                let suffix = case["expect"]["error_type_suffix"].as_str().unwrap();
                let outcome = if operation == "usage.retrieve" {
                    let request_id = case["request"]["request_id"].as_str().unwrap_or_default();
                    client.usage(request_id).await.err()
                } else if operation == "embeddings.create" {
                    let request = &case["request"];
                    client
                        .embeddings(&axonium::EmbeddingRequest {
                            model: request["model"].as_str().unwrap_or_default().into(),
                            input: vec!["x".into()],
                            ..Default::default()
                        })
                        .await
                        .err()
                } else {
                    client.chat(&chat_request(&case["request"])).await.err()
                };
                let Some(axonium::Error::Api(api)) = outcome else {
                    panic!("{id}: expected a gateway error, got {outcome:?}");
                };
                assert_eq!(
                    api.status,
                    case["response"]["status"].as_u64().unwrap() as u16,
                    "{id}: status"
                );
                assert_eq!(api.type_suffix, suffix, "{id}: type suffix");
                assert_eq!(api.kind, kind_for(suffix), "{id}: kind");
                if let Some(want) = case["expect"]["retryable"].as_bool() {
                    assert_eq!(api.retryable(), want, "{id}: retryable");
                }
                if case["expect"]["has_request_id"].as_bool() == Some(true) {
                    assert!(
                        !api.request_id.is_empty(),
                        "{id}: no request_id to correlate with"
                    );
                }
                if case["expect"]["has_trace_id"].as_bool() == Some(true) {
                    assert!(
                        !api.trace_id.is_empty(),
                        "{id}: no trace_id to correlate with"
                    );
                }
            }
            (k, op) => panic!("{id}: unhandled kind {k} / operation {op}"),
        }
        ran += 1;
    }
    assert_eq!(ran, cases.len(), "every case must be executed, not skipped");
    println!("{ran} contract cases replayed");
}
