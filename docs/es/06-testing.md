<!-- translated-from: 06-testing.md sha256:3f1d48e248 -->
# Probar contra Axonium

> **¿Cómo construyo encima de esto sin gastar nada ni depender de un despliegue vivo?**

## No simules el SDK

Simular `client.chat.completions.create` prueba que llamaste a una función. No te dice si manejaste
un `429`, si tu bucle de herramientas sobrevive a un `arguments` truncado, ni si tu conciliación
aguanta una repetición que no tiene fila de uso.

Simula el **transporte**. Cada SDK de aquí es un cliente HTTP y nada más, así que un gateway falso
ejercita tu código contra el parseo real, el tipado de errores real y la lógica de reintentos real.

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

```swift
// URLProtocol, through the configuration's own sessionConfiguration hook -- which exists for this.
final class FakeGateway: URLProtocol { /* canInit, startLoading, ... */ }

let session = URLSessionConfiguration.ephemeral
session.protocolClasses = [FakeGateway.self]

let client = try AxoniumClient(
    configuration: AxoniumConfiguration(
        gatewayBaseURL: "https://gw.test", clientID: "i", clientSecret: "s",
        sessionConfiguration: session))
```

```typescript
// No mocking library and no patched global: `fetch` is injected, so the fake IS the dependency.
// That is why this package's own suite has a test asserting no HTTP-mocking library is installed.
const client = new Axonium({
  gatewayBaseURL: "https://gw.test",
  clientId: "i",
  clientSecret: "s",
  fetch: async (url, init) => {
    if (String(url).endsWith("/oauth2/token")) {
      return Response.json({ access_token: "t", token_type: "bearer", expires_in: 300 });
    }
    return Response.json(COMPLETION);
  },
});
```

## Haz que las esperas dejen de costar reloj

Un test de reintentos que honra un `Retry-After` real tarda lo que la espera. Pon la política a cero:

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

```swift
let client = try AxoniumClient(
    configuration: AxoniumConfiguration(
        gatewayBaseURL: "https://gw.test", clientID: "i", clientSecret: "s",
        retry: RetryPolicy(maxAttempts: 3, initialBackoff: 0, maxBackoff: 0, jitter: false)))
```

```typescript
const client = new Axonium({
  gatewayBaseURL: "https://gw.test",
  clientId: "i",
  clientSecret: "s",
  retry: { maxAttempts: 3, initialBackoff: 0, maxBackoff: 0, jitter: false },
  fetch: fakeGateway,
});
```

`max_backoff=0` tiene un segundo efecto que conviene conocer: cualquier `Retry-After` del servidor
por encima de cero pasa entonces a **devolverse como error en vez de dormirse**, que es la misma
regla que evita que una espera larga bloquee a un llamante real. Si tu test afirma que una espera
larga se devuelve, así es como produces una.

## Usa los sobres grabados

El repositorio lleva los cuerpos de error y los streams SSE contra los que se prueban estos SDK, en
[`spec/fixtures/`](https://github.com/Root1V/axonium-sdk/tree/main/spec/fixtures), indexados por
[`spec/cases/manifest.json`](https://github.com/Root1V/axonium-sdk/blob/main/spec/cases/manifest.json).
Son capturas literales de cable — separadores de registro en línea en blanco incluidos — no
aproximaciones escritas a mano.

Usarlos significa que tu gateway falso contesta como el real, incluidas las partes que nadie
recuerda: que el sobre de rate-limit omite `trace_id`, que un `429` pone su `scope` en el cuerpo y no
en la cabecera, que el evento de error de un stream es `{"error": "..."}` en el nivel superior.

Apunta tus fixtures a ese directorio en vez de copiar el JSON a tu fichero de test, donde dejará de
coincidir y nadie se dará cuenta.

## Qué hacen los propios tests de los SDK

Merece conocerse, porque dice qué está cubierto ya y qué no:

- **Un corpus, cinco runners.** Python, Go, Rust, TypeScript y Swift reproducen el mismo manifest
  contra los mismos bytes. No comparten código, así que el comportamiento que coincide está
  verificado y no pretendido. Swift vendoriza el corpus como submódulo; los otros cuatro lo leen del
  repositorio.
- **El corpus crece cuando la mutación encuentra un hueco**, no por calendario. El caso más reciente
  existe porque mutar un runner para comparar la cadena literal `"stream interrupted"` dejó verdes
  todos los demás casos — el corpus no sabía distinguir *detectar la clave* de *comparar el texto*,
  que es justo la distinción que la plataforma había pedido explícitamente.
- **Cada test está escrito dos veces** en Python, sync y async, desde una sola fixture
  parametrizada. La generación anterior de este SDK tenía cero tests async.
- **La prueba de mutación es el listón de aceptación.** Un test que pasa cuando la implementación
  está rota no probó nada, y la única forma de encontrar esos es romper la implementación a
  propósito.

Ejecuta todo exactamente como lo ejecuta CI:

```bash
./scripts/verify.sh
```

Es un espejo deliberado de los ficheros de workflow. Una comprobación local que es *casi* la de CI
reporta verde y esconde la diferencia — algo que este repositorio ha pagado dos veces, una con un
paso de lint que necesitaba un build que solo existía en una máquina, y otra con una suite verde en
la versión de Node que tenía el autor y roja en la que el paquete promete.

## Tests de integración

Marcados y saltados por defecto. Necesitan `AXONIUM_INTEGRATION=1` más credenciales reales, y nunca
corren en CI — una suite que depende de que un despliegue esté en pie reporta rojo por motivos que no
tienen nada que ver con el cambio bajo revisión.

Siguiente: [Lo que no hace](07-limits.md).
