<!-- translated-from: 01-concepts.md sha256:f7c063b31d -->
# Conceptos

> **¿Qué son todas estas piezas y para qué sirve cada una?**

## El gateway, y por qué solo hay una dirección

Prometheus es una plataforma con varios componentes: un registro de modelos, las instancias sobre
las que planifica, un servicio de autenticación, un limitador de tasa con cortacircuitos por
backend, y un almacén de uso. **Tu cliente no habla con ninguno de ellos.**

Habla con el **gateway**, que sirve la inferencia *y* emite los tokens en la misma dirección. Eso es
una decisión de la plataforma y no una conveniencia del SDK: antes hubo dos direcciones, y la
segunda desapareció. Por eso hay un host que configurar y no seis, y por eso un despliegue nuevo se
describe con una sola URL.

## El cliente

El pool de conexiones y el token cacheado viven en él, así que construir uno por petición tira los
dos — y vuelve a pedir un token que ya tenías.

Los cinco clientes son seguros para uso concurrente.

**Un cliente async pertenece al bucle de eventos en el que se construyó, así que «uno por proceso»
solo vale para un proceso con un solo bucle.** Esta página lo decía sin ese matiz, y un servicio con
una API y un pool de workers suele tener más de uno. Medido en Python 3.13, reusando un
`AsyncAxonium` entre dos llamadas a `asyncio.run()`:

```
loop 1: ok
loop 2: RuntimeError: Event loop is closed
```

El pool retiene los recursos del primer bucle, y el error sale de dentro de asyncio, así que no
nombra la causa. Construye el cliente dentro del bucle que lo va a usar — un `lifespan` de FastAPI,
el arranque de un worker —, consérvalo mientras ese bucle viva y ciérralo al apagar
(`await client.aclose()`, o `async with`). El cliente sync no tiene esa restricción.

## Tokens

OAuth2 `client_credentials`, contra el propio gateway. El SDK lo gestiona entero y conviene saber
cómo, porque las tres decisiones de abajo son las que evitan un `401` a mitad de una petición:

- Se **refresca por adelantado**: al 80 % de la vida del token, o cuando quedan menos de 30 segundos,
  lo que ocurra antes.
- La caducidad se calcula desde una lectura de reloj **monótono tomada antes de enviar**, así que una
  respuesta lenta del token hace que el SDK refresque pronto y no tarde.
- `expires_in` siempre se lee de la respuesta. El SDK nunca supone una duración.
- El `scope` **concedido** se lee de vuelta de la respuesta, nunca se asume que sea el pedido.

**Lo que no hace:** no refresca un token que nunca pidió, y no esconde un `401`. Un reintento
reactivo, y después el error es tuyo.

## Metadatos de la respuesta

Cada respuesta lleva un `meta` junto al contenido — en los éxitos, no solo en los fallos, porque
correlacionar una llamada lenta que funcionó importa tanto como una que no.

```python
completion = client.chat.completions.create(model="qwen3-0.6b", messages=[...])

completion.meta.request_id     # take this to the platform team
completion.meta.instance_id    # which instance served it
completion.meta.rate_limit     # the budget as of this response
completion.meta.waited_s       # seconds this SDK spent deliberately asleep
completion.meta.attempts       # how many HTTP attempts produced this
```

```go
completion.Meta.RequestID
completion.Meta.InstanceID
completion.Meta.RateLimit
completion.Meta.WaitedFor
completion.Meta.Attempts
```

```rust
completion.meta.request_id;
completion.meta.instance_id;
completion.meta.rate_limit;
completion.meta.waited_for;
completion.meta.attempts;
```

```swift
completion.meta.requestID     // take this to the platform team
completion.meta.instanceID    // which instance served it
completion.meta.rateLimit     // the budget as of this response
completion.meta.waitedFor     // seconds this SDK spent deliberately asleep
completion.meta.attempts      // how many HTTP attempts produced this
```

```typescript
completion.meta.requestId; // take this to the platform team
completion.meta.instanceId; // which instance served it
completion.meta.rateLimit; // the budget as of this response
completion.meta.waitedMs; // milliseconds this SDK spent deliberately asleep
completion.meta.attempts; // how many HTTP attempts produced this
```

`waited_s` existe porque un `Retry-After` respetado de 0 a 60 segundos parece desde fuera una
llamada lenta entre llamadas rápidas. Tres equipos distintos reportaron exactamente eso como un
cuelgue. El SDK lo registra en el log, pero una línea de log es invisible por defecto y una métrica
de latencia no sabe leerla — así que el número viaja en la respuesta. Está **excluido** a propósito
de cualquier duración que el SDK reporte: réstalo de tu propio reloj para obtener lo que de verdad
tardó la plataforma.

## La instantánea de límites de tasa

`meta.rate_limit` lleva los seis contadores `X-RateLimit-*` y, lo importante, un `scope` que dice
**qué presupuesto** describen. Los endpoints tienen presupuestos separados, así que un
`remaining_requests` leído tras una llamada de chat no dice nada sobre tu presupuesto de embeddings.

El conjunto de nombres de scope **se lee de la cabecera y no se enumera aquí**. Esta página llegó a
nombrar tres; los cinco SDK guardaban cada uno su propia lista en un comentario, y ya habían
divergido — uno decía `chat` donde la cabecera dice `chat_completions`, que es un nombre por el que
indexarías un mapa y no acertarías nunca.

La ventana sí merece diseñarse en contra, y no es deslizante: el presupuesto es un **cubo fijo de 60
segundos alineado al reloj**, y la asignación entera vuelve en el segundo 0 de cada minuto. Así que
una ráfaga a caballo de un límite pasa donde la misma unos segundos antes se rechaza. Marca el ritmo
contra el contador restante, nunca contra una tasa que supusiste.

```python
client.last_rate_limit          # what the most recent call reported
client.rate_limits["embeddings"]  # the most recent reading for that budget
```

```go
client.LastRateLimit()               // what the most recent call reported
client.RateLimits()["embeddings"]    // the most recent reading for that budget
```

```rust
client.last_rate_limit();                  // what the most recent call reported
client.rate_limits().get("embeddings");    // the most recent reading for that budget
```

```swift
// No client-held map yet: read the budget off the response you just got.
completion.meta.rateLimit?.scope              // which budget these numbers describe
completion.meta.rateLimit?.remainingRequests  // what is left of it
```

```typescript
client.lastRateLimit; // what the most recent call reported
completion.meta.rateLimit; // the budget as of this response, and the one to key yourself
```

Lee `client.rate_limits`, no `last_rate_limit`, cuando la pregunta es «cuánto queda del presupuesto
X». La distinción no es pedantería: antes de que existiera `scope`, una tubería de sugerencias que
tocaba tres endpoints seguidos dejaba `last_rate_limit` describiendo el que contestó último, sin que
nada en los números lo dijera.

**El mapa por scope existe solo en Python, Go y Rust.** TypeScript tiene `lastRateLimit` y Swift no
tiene ninguno de los dos — en ambos, `meta.rate_limit` de cada respuesta lleva los mismos números con
el `scope` adjunto, así que indexarlos tú son tres líneas y es lo que hace el mapa. Dicho aquí en vez
de dejar una pestaña que calladamente enseña otra cosa.

**Lo que no hace:** los contadores de tokens son la contabilidad *a posteriori* del gateway, no una
reserva. Son una señal fuerte, no una garantía de que no vayas a ver un `429`.

## Errores

Una clase por fila del catálogo de errores del gateway, todas descendiendo de una raíz común,
partidas por una línea que importa:

```
AxoniumError
├── ConfigurationError      you gave the SDK something unusable
├── TransportError          the request never produced a response
├── APIError                the gateway answered, and said no
│   ├── RateLimitError, SpendCapExceededError, UnknownModelError, …
└── OAuthError              the token endpoint said no, in RFC 6749 shape
```

`OAuthError` **no** hereda de `APIError`, a propósito. Son sobres distintos con semánticas distintas,
y juntarlos es como una página HTML de error 502 de un proxy acabó tipada como «tus credenciales
están mal».

Un tipo de error no reconocido cae en una clase elegida por el status HTTP en vez de lanzar — el
catálogo crece, y un SDK que se rompe con un error nuevo es peor que uno que lo tipa flojo.
