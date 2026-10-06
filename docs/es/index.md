<!-- translated-from: index.md sha256:7b91499081 -->
# Primeros pasos

**Axonium** es un conjunto de SDK cliente para la plataforma de inferencia **Prometheus**, en
Python, Go, Rust, Swift y TypeScript.

**Prometheus** es una plataforma de inferencia autoalojada: un catálogo de modelos pequeños de
lenguaje, embeddings, reranking e imagen, servidos en instancias gestionadas detrás de una sola API
autenticada, con control de acceso por modelo, límites de tasa, topes de gasto y contabilidad de
uso por petición.

Tu cliente habla con exactamente uno de sus componentes — el **gateway**, que sirve la inferencia y
emite los tokens en una sola dirección. El resto de la plataforma queda detrás: el registro de
modelos y las instancias sobre las que planifica, el servicio de autenticación, el limitador de tasa
y el cortacircuitos por backend, y el almacén de uso. Estos SDK nunca se dirigen a ninguno de ellos
directamente, y por eso hay un host que configurar y no seis.

Cinco SDK, un contrato. Son implementaciones separadas que no comparten código — lo que los mantiene
idénticos es un corpus de bytes de cable grabados que los cinco reproducen. El comportamiento que
coincide está **verificado**, no pretendido.

**Este sitio también está [en inglés](../index.html).** Cada ejemplo se muestra en los cinco
lenguajes, y si falta una pestaña es un fallo del sitio y hay un test que lo caza.

## Instalación

```bash
pip install axonium
```

```bash
go get github.com/Root1V/axonium-sdk/go
```

```bash
cargo add axonium
```

```bash
# Package.swift: .package(url: "https://github.com/Root1V/axonium-sdk-swift", from: "0.3.0")
swift package add-dependency https://github.com/Root1V/axonium-sdk-swift --from 0.3.0
```

```bash
npm install axonium
```

## Lo más pequeño que funciona

Las credenciales son el único ajuste que tienes que dar. El gateway emite sus propios tokens, así
que hay una sola dirección, y normalmente ninguna que indicar:

```python
from axonium import Axonium

with Axonium(client_id="...", client_secret="...") as client:
    completion = client.chat.completions.create(
        model="qwen3-0.6b",
        messages=[{"role": "user", "content": "Say hello"}],
    )
    print(completion.content)
```

```go
client, err := axonium.New(axonium.Config{ClientID: "...", ClientSecret: "..."})
if err != nil {
	return err
}
defer client.Close()

completion, err := client.Chat.Create(ctx, axonium.ChatRequest{
	Model:    "qwen3-0.6b",
	Messages: []axonium.Message{axonium.TextMessage("user", "Say hello")},
})
if err != nil {
	return err
}
fmt.Println(completion.Content())
```

```rust
let client = Client::new(Config {
    client_id: "...".into(),
    client_secret: "...".into(),
    ..Default::default()
})?;

let completion = client
    .chat(&ChatRequest {
        model: "qwen3-0.6b".into(),
        messages: vec![Message::text("user", "Say hello")],
        ..Default::default()
    })
    .await?;
println!("{}", completion.content());
```

```swift
let client = try AxoniumClient(
    configuration: AxoniumConfiguration(
        gatewayBaseURL: "...", clientID: "...", clientSecret: "..."))

let completion = try await client.chat(
    ChatRequest(model: "qwen3-0.6b", messages: [.user("Say hello")]))
print(completion.content ?? "")
```

```typescript
import { Axonium } from "axonium";

const client = new Axonium({ clientId: "...", clientSecret: "..." });

const completion = await client.chat.completions.create({
  model: "qwen3-0.6b",
  messages: [{ role: "user", content: "Say hello" }],
});
console.log(completion.content);
```

Las credenciales pueden venir del entorno — `AXONIUM_CLIENT_ID` y `AXONIUM_CLIENT_SECRET`. El SDK
nunca lee un fichero `.env` por su cuenta; cargarlo es trabajo de la aplicación, y
[Configuración](04-configuration.md) explica por qué.

## Lo que te da esto y no te da una llamada HTTP a secas

Un `POST` al gateway no es difícil. Lo que cuesta acertar es todo lo que lo rodea, y eso es lo que
son estos SDK:

- **Tokens** pedidos, cacheados y refrescados antes de caducar, con un único reintento ante un `401`.
- **Errores tipados**, una clase por cada fila del catálogo de errores del gateway, para que
  `except SpendCapExceededError` sea algo que puedas escribir.
- **Reintentos que no pueden cobrar dos veces.** Solo se reintenta donde la plataforma afirma que no
  hubo generación. Ver [Fallos](03-failure.md) — esa es la parte que merece la pena leer.
- **Streaming** con detección de errores en banda, cancelación de verdad, y llamadas a herramientas
  reensambladas en la misma forma que devuelve la llamada no-streaming.
- **Visibilidad de los límites de tasa** en cada respuesta, por presupuesto.

## Dónde seguir

- [Conceptos](01-concepts.md) — las piezas y para qué sirve cada una.
- [Hacer llamadas](02-calls.md) — el cliente y las seis cosas que puede pedir.
- [Fallos, reintentos e idempotencia](03-failure.md) — la parte cara.
- [Configuración y transporte](04-configuration.md) — apuntar esto a tu despliegue.
- [Operaciones compuestas](05-composed.md) — cuando una llamada no basta.
- [Probar contra Axonium](06-testing.md) — construir encima sin gastar nada.
- [Lo que no hace](07-limits.md) — los límites, dichos en voz alta.
- [Referencia de la API de Python](08-reference.md) — cada símbolo exportado.

La documentación de referencia de los otros cuatro la generan sus propios ecosistemas:
[pkg.go.dev](https://pkg.go.dev/github.com/Root1V/axonium-sdk/go) para Go,
[docs.rs](https://docs.rs/axonium/latest/axonium/) para Rust, DocC en el
[repositorio de Swift](https://github.com/Root1V/axonium-sdk-swift), y los ficheros `.d.ts` más
[`docs/api.md`](https://github.com/Root1V/axonium-sdk/blob/main/typescript/docs/api.md) para
TypeScript, que además incluye una
[guía de migración desde Python](https://github.com/Root1V/axonium-sdk/blob/main/typescript/docs/migrating-from-python.md).

---

*Los bloques de código son **idénticos** a los de la versión en inglés, a propósito: lo que se pega
en un editor no se traduce, y mantenerlos iguales es lo que evita que las dos versiones del sitio se
separen sin que nadie lo note.*

*Esta página y el resto del sitio se generan desde `docs/es/*.md`. Un test falla si el HTML deja de
coincidir con su Markdown, y otro falla si la traducción se queda atrás respecto al original en
inglés.*
