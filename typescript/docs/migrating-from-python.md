# Migrating from the Python SDK

Written for someone moving a call site, not for someone learning the platform. The two SDKs replay the
**same 49 contract cases** against the same recorded bytes, so where this document says "the same", that
is enforced rather than intended.

## The shape of the difference

|                | Python                                           | TypeScript                                                          |
| -------------- | ------------------------------------------------ | ------------------------------------------------------------------- |
| Construction   | `Axonium()`                                      | `new Axonium()`                                                     |
| Sync and async | both, mirrored surfaces                          | promises only — every call is a network call                        |
| Field names    | `snake_case` on responses                        | **`camelCase` on responses, `snake_case` on requests**              |
| Streaming      | `with client.chat.completions.stream(...) as s:` | `for await (const chunk of await api.chat.completions.stream(...))` |
| Usage row      | `client.usage.retrieve(id)`                      | `api.usage.get(id)`                                                 |
| Token claims   | `client.token_claims`                            | `api.tokenClaims`                                                   |
| Errors         | `axonium.ForbiddenError`                         | `ForbiddenError`, same names                                        |

**Requests keep `snake_case` and responses become `camelCase`**, which looks inconsistent and is not: a
request body goes on the wire as you wrote it, so `max_tokens` has to be `max_tokens` or the gateway
drops it silently. A response is this SDK's own object, so it follows the language.

## Call by call

```python
# Python
client = Axonium()
answer = client.chat.completions.create(
    model="qwen3-0.6b",
    messages=[{"role": "user", "content": "hola"}],
    max_tokens=100,
)
print(answer.content, answer.usage.prompt_tokens, answer.meta.request_id)
```

```ts
// TypeScript
const api = new Axonium();
const answer = await api.chat.completions.create({
  model: "qwen3-0.6b",
  messages: [{ role: "user", content: "hola" }],
  max_tokens: 100,
});
console.log(answer.content, answer.usage?.promptTokens, answer.meta.requestId);
```

Two differences that bite:

- **The request is one object**, not keyword arguments.
- **`usage` is optional.** It is `undefined` when the backend reported none, which is not the same as
  zero — and `?.` is how you say so.

### Streaming

```python
with client.chat.completions.stream(model=m, messages=msgs) as stream:
    for chunk in stream:
        print(chunk.content, end="")
    print(stream.usage.estimated)
```

```ts
const stream = await api.chat.completions.stream({ model, messages });
for await (const chunk of stream) process.stdout.write(chunk.delta);
console.log(stream.usage?.estimated);
```

`chunk.content` becomes **`chunk.delta`**: it is the text that arrived in that chunk, and `delta` says
so where `content` reads like the whole thing. `chunk.raw` is the decoded chunk if you need more.

There is no context manager, so there is nothing to close. **Breaking out of the loop cancels the
request** — the stream releases its reader and cancels the body on exit, including an early `break`.

### Vision

```python
# Python validates and raises
{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
```

```ts
imageFromBytes(bytes, "image/png");
```

Same wire result. Neither SDK will send an `http(s)://` url, because the gateway refuses it as an SSRF
mitigation — Python raises, and this one refuses before the round trip.

### Tool calls

```python
call.function.name        # and call.name as a shortcut
call.decoded_arguments()
```

```ts
call.function.name; // and call.name as a shortcut
decodedArguments(call); // a function, not a method
```

A free function rather than a method because the response types here are plain data: a `ToolCall` from
`JSON.parse` keeps no prototype, so a method would be a trap for anyone who serialised a response and
read it back.

### Structured output

```python
response_format={"type": "json_schema", "json_schema": {"name": "x", "schema": {...}}}
```

```ts
response_format: jsonSchema("x", { ... })
// or a Zod schema, without this package depending on Zod:
response_format: jsonSchema("x", { toJSONSchema: () => z.toJSONSchema(schema) })
```

`jsonSchema()` accepts anything with a `toJSONSchema()` method, which is the seam that keeps Zod out of
this package's dependencies while letting you use it. **The content comes back as a JSON string**, not a
nested object, in both SDKs.

### Errors

The class names are the same and the hierarchy is the same, including the two shapes worth knowing:

```ts
try {
  await api.chat.completions.create(request);
} catch (err) {
  if (err instanceof RateLimitError) await sleep(err.retryAfter ?? 1000);
  else if (err instanceof APIError) report(err.meta.requestId, err.typeSuffix);
  else if (err instanceof OAuthError) rotateCredential(); // NOT an APIError, deliberately
}
```

`OAuthError` does not extend `APIError` in either SDK: the token endpoint answers RFC 6749 rather than
problem+json, and retrying a credential failure with the same credential can never work.

### Configuration

Same environment variables, same precedence — argument, then environment, then an error naming both:

```
AXONIUM_GATEWAY_BASE_URL   AXONIUM_CLIENT_ID   AXONIUM_CLIENT_SECRET   AXONIUM_SCOPE
```

Three differences:

- **No `load_dotenv`.** Loading a `.env` is the application's job in both, but Node has no convention
  for it either — use `node --env-file=.env`.
- **`verify_modality` has no equivalent**, and deliberately. Python's modality preflight predates
  `RM-66`, which made the gateway refuse every wrong-modality combination itself; all six were measured.
  It now saves a request rather than a billed generation, and a new SDK starting today does not need to
  carry it.
- **`ca_bundle` has no equivalent, and cannot have one.** `fetch` has no option for a custom CA, and
  reaching one means either an `undici` dependency or a `node:` import — each breaking a requirement this
  package was built to. Use `NODE_EXTRA_CA_CERTS=/path/to/ca.pem` on Node and Bun, or `--cert` on Deno.
  It is an environment concern here rather than a constructor argument.

## What Python has and this does not

- **`usage.export`** — in no SDK of this family yet. `GET /v1/usage/export` is in the contract.
- **OpenTelemetry** — Python has it behind an extra; here it is planned for `0.2.0`.
- **`X-Prometheus-Ignored-Parameters` on the response.** `requireParameters: true` makes the gateway
  refuse a dropped field instead of silently dropping it, and that works here; reading the header back
  on a success does not. Deliberately held: none of the five SDKs expose it, and doing it in one first
  would be the divergence the shared corpus exists to prevent.

## What this has and Python does not

- **`ChatStream.reasoning`** exists in both, but was missing here until it was measured — see `AXO-130`.
- **Zero runtime dependencies**, which is why there is no Zod overload and no HTTP client.
- **An injected `fetch`**, so tests replay the corpus against a function rather than a patched global.
