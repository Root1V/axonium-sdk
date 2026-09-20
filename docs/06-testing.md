# Testing against Axonium

> **How do I build on this without spending anything or depending on a live deployment?**

## Do not mock the SDK

Mocking `client.chat.completions.create` tests that you called a function. It cannot tell you
whether you handled a `429`, whether your tool loop survives a truncated `arguments` string, or
whether your reconciliation copes with a replay that has no usage row.

Mock the **transport** instead. Every SDK here is an HTTP client and nothing more, so a fake gateway
exercises your code against the real parsing, the real error typing and the real retry logic.

```python
import httpx, respx
from axonium import Axonium

@respx.mock
def test_summariser_handles_a_rate_limit():
    respx.post("https://gw.test/oauth2/token").mock(return_value=httpx.Response(
        200, json={"access_token": "t", "token_type": "bearer", "expires_in": 300}))
    respx.post("https://gw.test/v1/chat/completions").mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "0"},
                       json={"type": "https://prometheus.internal/errors/rate-limit-exceeded-requests",
                             "status": 429}),
        httpx.Response(200, json=COMPLETION),
    ])

    with Axonium(gateway_base_url="https://gw.test", client_id="i", client_secret="s") as client:
        assert summarise(client, "...") == "..."
```

```go
srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path == "/oauth2/token" {
		_, _ = w.Write([]byte(`{"access_token":"t","token_type":"bearer","expires_in":300}`))
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_, _ = w.Write(completionJSON)
}))
defer srv.Close()

client, err := axonium.New(axonium.Config{
	GatewayBaseURL: srv.URL, ClientID: "i", ClientSecret: "s",
})
```

```rust
let server = MockServer::start().await;
Mock::given(path("/oauth2/token"))
    .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
        "access_token": "t", "token_type": "bearer", "expires_in": 300
    })))
    .mount(&server)
    .await;
Mock::given(any())
    .respond_with(ResponseTemplate::new(200).set_body_json(completion))
    .mount(&server)
    .await;

let client = Client::new(Config {
    gateway_base_url: server.uri(),
    client_id: "i".into(),
    client_secret: "s".into(),
    ..Default::default()
})?;
```

## Make the waits stop costing wall-clock

A retry test that honours a real `Retry-After` takes as long as the wait. Set the policy to remove
it:

```python
from axonium import RetryPolicy

client = Axonium(..., retry=RetryPolicy(initial_backoff=0.0, max_backoff=0.0, jitter=False))
```

```go
client, err := axonium.New(axonium.Config{
	GatewayBaseURL: srv.URL, ClientID: "i", ClientSecret: "s",
	Retry: &axonium.RetryPolicy{MaxAttempts: 3, InitialBackoff: 0, MaxBackoff: 0},
})
```

```rust
let client = Client::new(Config {
    gateway_base_url: server.uri(),
    client_id: "i".into(),
    client_secret: "s".into(),
    retry: RetryPolicy {
        max_attempts: 3,
        initial_backoff: Duration::ZERO,
        max_backoff: Duration::ZERO,
        jitter: false,
        ..Default::default()
    },
    ..Default::default()
})?;
```

`max_backoff=0` has a second effect worth knowing: any server-supplied `Retry-After` above zero is
then **surfaced as an error rather than slept through**, which is the same rule that keeps a long
wait from blocking a real caller. If your test asserts that a long wait is handed back, this is how
you produce one.

## Use the recorded envelopes

The repository carries the error bodies and SSE streams these SDKs are tested against, in
[`spec/fixtures/`](https://github.com/Root1V/axonium-sdk/tree/main/spec/fixtures), indexed by
[`spec/cases/manifest.json`](https://github.com/Root1V/axonium-sdk/blob/main/spec/cases/manifest.json).
They are literal wire captures — blank-line record separators included — not hand-written
approximations.

Using them means your fake gateway answers the way the real one does, including the parts nobody
remembers: that the rate-limit envelope omits `trace_id`, that a `429` puts its `scope` in the body
rather than the header, that a stream's error event is `{"error": "..."}` at the top level.

Point your fixtures at that directory rather than copying the JSON into your test file, where it
will stop matching and nobody will notice.

## What the SDKs' own tests do

Worth knowing, because it tells you what is already covered and what is not:

- **One corpus, three runners.** Python, Go and Rust replay the same manifest against the same
  bytes. They share no code, so matching behaviour is verified rather than intended.
- **Every test is written twice** in Python, sync and async, from one parametrised fixture. The
  previous generation of this SDK had zero async tests.
- **Mutation testing is the acceptance bar.** A test that passes when the implementation is broken
  did not test anything, and the only way to find those is to break the implementation on purpose.

Run the whole thing exactly as CI does:

```bash
./scripts/verify.sh
```

## Integration tests

Marked and skipped by default. They need `AXONIUM_INTEGRATION=1` plus real credentials, and they
never run in CI — a test suite that depends on a deployment being up reports red for reasons that
have nothing to do with the change under review.

Next: [What it does not do](07-limits.md).
