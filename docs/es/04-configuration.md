<!-- translated-from: 04-configuration.md sha256:500a8b2d09 -->
# Configuración y transporte

> **¿Cómo apunto esto a mi despliegue?**

## Una sola dirección

Hay **una** dirección, y normalmente ninguna que indicar. El gateway emite sus propios tokens y
sirve la inferencia, así que `gateway_base_url` es el único ajuste de host que existe.

No siempre fue así, y el motivo del cambio merece guardarse: una dirección de autenticación y otra de
gateway significaban que apuntar el SDK a tu propio despliegue requería cambiar **dos** cosas.
Olvidar la segunda dejaba al cliente pidiéndole un token a la plataforma oficial y usándolo en otro
sitio. **Nada fallaba.** El token simplemente lo emitía quien no debía. Un ajuste cuyo mal uso es
silencioso es peor que un ajuste que falta, así que la segunda dirección se eliminó del todo en vez
de darle un buen valor por defecto — un campo opcional sigue enseñando a los consumidores que existe
una segunda dirección.

Si te queda un `AXONIUM_AUTH_BASE_URL` en un entorno, se ignora.

## Precedencia

Gana el primero:

1. Un argumento por llamada
2. Un argumento del constructor
3. Una variable de entorno
4. Si no: `ConfigurationError`, nombrando el ajuste **y** su variable de entorno

| Ajuste | Variable de entorno | Por defecto |
|---|---|---|
| `gateway_base_url` | `AXONIUM_GATEWAY_BASE_URL` | `http://127.0.0.1:8020` |
| `client_id` | `AXONIUM_CLIENT_ID` | — obligatorio |
| `client_secret` | `AXONIUM_CLIENT_SECRET` | — obligatorio |
| `scope` | `AXONIUM_SCOPE` | sin poner: decide el gateway |
| `ca_bundle` | `AXONIUM_CA_BUNDLE` | almacén de confianza del sistema |

**Las credenciales son lo único obligatorio.** La dirección por defecto es de bucle local, que es lo
que hace seguro tenerla: equivocarse llega a tu propia máquina —normalmente una conexión rechazada, y
suficientemente claro— y nunca puede mandar calladamente una credencial a algún sitio real.

Pedir *ningún* `scope` no es lo mismo que pedirlos todos. Déjalo sin poner salvo que quieras un token
**más estrecho** del que tu cliente tiene derecho; el scope concedido siempre se lee de vuelta de la
respuesta en vez de asumirse.

## El SDK no lee `.env`

Cargar un fichero `.env` es decisión de la aplicación, no de una biblioteca. Una biblioteca que lee
uno cambia su comportamiento según un fichero que el llamante no mencionó, y en un proceso de
servidor eso es una sorpresa que nadie pidió.

```bash
uv run --env-file .env python my_script.py
```

Python resuelve el entorno **en el momento de construir**, no al importar — lo que elimina una clase
de bug que tenía la generación anterior de este SDK, donde un campo con valor por defecto de
`os.getenv` en el cuerpo de una clase se evaluaba una sola vez, al importar, antes de que nada
estuviera configurado.

## Timeouts

```python
from axonium import Axonium, Timeouts

client = Axonium(
    client_id="...",
    client_secret="...",
    timeouts=Timeouts(connect=10, read=600, write=600, pool=10, stream_read=180),
)
```

```go
client, err := axonium.New(axonium.Config{
	ClientID:     "...",
	ClientSecret: "...",
	Timeouts: axonium.Timeouts{
		Connect: 10 * time.Second,
		Request: 600 * time.Second,
		Stream:  180 * time.Second,
		Auth:    15 * time.Second,
	},
})
```

```rust
let client = Client::new(Config {
    client_id: "...".into(),
    client_secret: "...".into(),
    timeouts: Timeouts {
        connect: Duration::from_secs(10),
        request: Duration::from_secs(600),
        stream: Duration::from_secs(180),
        auth: Duration::from_secs(15),
    },
    ..Default::default()
})?;
```

```swift
let client = try AxoniumClient(
    configuration: AxoniumConfiguration(
        gatewayBaseURL: "...",
        clientID: "...",
        clientSecret: "...",
        timeouts: Timeouts(connect: 10, request: 600, streamRead: 180, auth: 30)))
```

```typescript
const client = new Axonium({
  clientId: "...",
  clientSecret: "...",
  // Milliseconds, because that is what every timer in this runtime takes and converting at the
  // boundary is one more place to be wrong by a factor of a thousand.
  timeouts: { connect: 10_000, request: 600_000, stream: 180_000 },
});
```

Los cinco no reparten el presupuesto igual, y la documentación no va a fingir que sí. Python expone
las cuatro fases de httpx (`connect`, `read`, `write`, `pool`); Go, Rust y Swift acotan la llamada
entera con `request`; TypeScript cuenta en milisegundos porque es lo que toman los temporizadores de
su runtime. Lo que importa es igual en todas partes: el presupuesto de lectura es generoso porque
generar es lento, y el de streaming es aparte y más corto.

Los timeouts de lectura son generosos porque generar es lento. El de streaming es aparte y más
corto: acota el hueco **entre chunks**, no la duración del stream entero, así que un stream atascado
se detecta sin poner techo a uno largo.

Cualquier llamada puede sobrescribirlo:

```python
client.chat.completions.create(model="qwen3-0.6b", messages=[...], timeout=30)
```

```go
ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
defer cancel()

completion, err := client.Chat.Create(ctx, request)
```

```rust
let completion = tokio::time::timeout(
    Duration::from_secs(30),
    client.chat(&request),
).await??;
```

```swift
// No per-call timeout: cancel the enclosing Task, which tears down the request.
let task = Task { try await client.chat(request) }
Task { try await Task.sleep(for: .seconds(30)); task.cancel() }
let completion = try await task.value
```

```typescript
// Either a per-call timeout, or your own AbortSignal -- whichever fires first wins.
await client.chat.completions.create(request, { timeout: 30_000 });
await client.chat.completions.create(request, { signal: AbortSignal.timeout(30_000) });
```

**Python y TypeScript aceptan un `timeout` por llamada; Go, Rust y Swift deliberadamente no.** Una
fecha límite sobre una llamada concreta es ya lo que son `context.Context`, `tokio::time::timeout` y
la cancelación de tareas, y un segundo mecanismo al lado es un sitio más para que los dos discrepen.
TypeScript no tiene nada equivalente integrado, así que lleva el suyo — y además acepta tu
`AbortSignal`, ganando el que dispare primero.

**Un timeout de cliente no se reintenta**, y el error dice por qué — ver [Fallos](03-failure.md).

### TypeScript sobre Node tiene un techo por debajo de todo esto, a 300 segundos

El `fetch` propio de Node se rinde ante una respuesta que no ha mandado **ninguna cabecera** pasados
300 s. Eso es el `headersTimeout` de undici, ignora `timeouts.request`, y este SDK no puede subirlo
sin tomar una dependencia de undici — lo que acabaría con la regla de cero dependencias que vigilan
tres tests.

Solo muerde a las llamadas **no-streaming**, y las muerde a todas, porque una generación no-streaming
manda sus cabeceras cuando termina. Así que cualquier generación de más de cinco minutos falla a los
cinco minutos, por alto que pongas el timeout.

Dos salidas, por orden de preferencia:

1. **Usa `chat.completions.stream()`.** En un stream las cabeceras llegan de inmediato, así que el
   límite nunca aplica. Para cualquier cosa que pueda pasar de cinco minutos, esta es la respuesta
   correcta y no un apaño.
2. **Inyecta tu propio `fetch`** con un `headersTimeout` mayor:

<!-- one-language: typescript -->
```typescript
import { fetch as undiciFetch, Agent } from "undici";

const client = new Axonium({
  fetch: (url, init) =>
    undiciFetch(url, { ...init, dispatcher: new Agent({ headersTimeout: 900_000 }) }),
});
```

Hasta la `0.2.2` esto salía como `TransportError: Could not reach the gateway` — falso, el gateway
contestó y seguía generando — **y los fallos de transporte se reintentan**, así que una llamada se
convertía en tres generaciones facturables. Ahora es un `TimeoutError`, que nunca se reintenta, y el
mensaje dice todo lo anterior. Lo reportó un consumidor; ver [Fallos](03-failure.md) para por qué un
timeout es lo único que este SDK no reintenta por su cuenta.

## Política de reintentos

```python
from axonium import RetryPolicy

client = Axonium(
    client_id="...",
    client_secret="...",
    retry=RetryPolicy(max_attempts=3, initial_backoff=0.5, max_backoff=60.0, jitter=True),
)
```

```go
client, err := axonium.New(axonium.Config{
	ClientID:     "...",
	ClientSecret: "...",
	Retry: &axonium.RetryPolicy{
		MaxAttempts:    3,
		InitialBackoff: 500 * time.Millisecond,
		MaxBackoff:     60 * time.Second,
		Jitter:         true,
	},
})
```

```rust
let client = Client::new(Config {
    client_id: "...".into(),
    client_secret: "...".into(),
    retry: RetryPolicy {
        max_attempts: 3,
        initial_backoff: Duration::from_millis(500),
        max_backoff: Duration::from_secs(60),
        jitter: true,
        ..Default::default()
    },
    ..Default::default()
})?;
```

```swift
let client = try AxoniumClient(
    configuration: AxoniumConfiguration(
        gatewayBaseURL: "...",
        clientID: "...",
        clientSecret: "...",
        retry: RetryPolicy(maxAttempts: 3, initialBackoff: 0.5, maxBackoff: 60, jitter: true)))
```

```typescript
const client = new Axonium({
  clientId: "...",
  clientSecret: "...",
  retry: { maxAttempts: 3, initialBackoff: 500, maxBackoff: 60_000, jitter: true },
});

// Or turn it off entirely, which is a policy and not an absence of one.
import { NO_RETRY } from "axonium";
const strict = new Axonium({ clientId: "...", clientSecret: "...", retry: NO_RETRY });
```

`max_backoff` hace doble función: es el techo del backoff **y** el `Retry-After` más largo del
servidor que el SDK va a aguantar sentado. Ponerlo a cero convierte cada espera en un error inmediato
que lleva `retry_after` — que es lo que quieres en un manejador de peticiones que no puede
bloquearse.

## TLS

Apunta `ca_bundle` a la cadena de confianza de tu despliegue. No hay bandera para desactivar la
verificación, y no la va a haber: el modo de fallo de esa bandera es que se activa durante un
incidente y nadie la desactiva.

Swift toma `additionalTrustAnchors` como **datos** y no como una ruta, porque una app empaqueta un
certificado como recurso o lo saca de un perfil MDM, y una ruta dentro de un contenedor en sandbox no
es algo que un llamante pueda nombrar de forma útil.

**TypeScript no tiene equivalente y no puede tenerlo** sin romper su promesa de cero dependencias: el
`fetch` nativo no expone la confianza TLS. En Node, pon `NODE_EXTRA_CA_CERTS` en el proceso. Eso es
un hueco real y no una preferencia de diseño, y se dice aquí en vez de dejar que se descubra.

## Reutilización de conexiones

Un cliente por proceso. El pool de conexiones y el token cacheado viven en él, así que construir uno
por petición tira los dos — y vuelve a pedir un token que ya tenías.

Los cinco clientes son seguros para uso concurrente.

Siguiente: [Operaciones compuestas](05-composed.md).
