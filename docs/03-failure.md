<!-- translated-from: 03-failure.md sha256:56964b62da -->
# Fallos, reintentos e idempotencia

> **¿Cuándo es seguro volver a intentarlo?**

Esta es la página que merece leerse. Todo lo demás aquí es comodidad; esta es la parte cara de
acertar y que cuesta dinero de verdad equivocar.

## La regla

**Solo se reintenta donde la plataforma afirma que no hubo generación.**

Es una frase corta que esconde el problema entero. Una petición de inferencia que falla después de
que el modelo empezara a producir tokens ya está facturada. Reintentarla no reanuda nada — encola una
segunda generación y pagas dos veces. Así que los SDK no reintentan ante «falló»; reintentan ante una
lista concreta de fallos que el gateway documenta como fallo rápido antes de llegar a un modelo:

| Se reintenta | Por qué es seguro |
|---|---|
| `429 rate-limit-exceeded-requests` | Rechazado en la puerta |
| `503 backend-unavailable` | El cortacircuitos estaba abierto; no se despachó nada |
| `503 rate-limiting-unavailable` | El almacén del limitador estaba caído; rechazo fail-closed |
| `503 usage-store-unavailable` | Rechazado antes de despachar |

Todo lo demás lanza. Un `502 upstream-error` **no** se reintenta por defecto: significa que los
intentos del propio gateway ya fallaron, así que un reintento del cliente es un cuarto intento de
algo que falló tres veces. Puedes activarlo (`retry_upstream_errors`), y aun así queda limitado a un
intento extra sin importar `max_attempts`.

**Un timeout de cliente tampoco se reintenta nunca por defecto**, y ese sorprende. El backend
probablemente sigue generando. El mensaje de error lo dice en vez de dejarte deducirlo.

### Un stream falla de dos maneras, y solo una se reintenta

La regla de arriba decide esto también, pero un stream hace que las dos mitades parezcan iguales
cuando no lo son.

**Rechazado antes de que el stream empiece → se reintenta.** El gateway abre la conexión al motor y
lee su status *antes* de que existan las cabeceras `200`/`text/event-stream`, así que un rechazo
vuelve como una respuesta de error normal — el mismo status y cuerpo que devuelve la forma
no-streaming del endpoint. No se generó nada y no se facturó nada, así que reabrir es una primera
generación y no una segunda, y pasa por la tabla de arriba sin cambios, `Retry-After` incluido. Es
además el único reintento que hay: **el gateway no hace reintentos internos en una petición
streamed**, así que un `503 backend-unavailable` te llega tras un intento en vez de tres. El backoff
no cambia; lo que cambia es el tiempo que esperaste antes de verlo.

**Falló después de que el stream empezara → nunca se reintenta.** En cuanto existe un chunk las
cabeceras están comprometidas, así que el fallo llega en banda, como un chunk que lleva `error`.
Parte de la respuesta se entregó y parte se facturó, así que repetir es una generación nueva y no una
reanudación. El SDK lanza con el texto parcial adjunto y te deja decidir, porque solo tú sabes para
qué se usó esa salida parcial.

Una `Idempotency-Key` no cambia ninguna de las dos mitades. En un stream repite uno que el gateway
*terminó* y cuya entrega se cortó por tu conexión — nunca uno que el propio modelo rompió.

> Hasta el 27/09/2026 la primera mitad no se podía expresar en absoluto: un stream rechazado antes de
> empezar llegaba como un `200` cuyo cuerpo era solo `data: [DONE]`, indistinguible de una respuesta
> legítimamente vacía. No había rechazo visible que reintentar. La plataforma ya devuelve el status
> real del motor.

## Esperar

Cuando el gateway manda `Retry-After`, el SDK lo usa. Lo da el servidor y es autoritativo: para un
cortacircuitos abierto es el tiempo real esperado de recuperación, que ninguna heurística local
mejora.

Con una excepción. **Una espera más larga que `max_backoff` se devuelve en vez de dormirse**, porque
bloquear a un llamante durante minutos dentro de una llamada es peor que decírselo. El error lleva
`retry_after`, así que puedes planificar el trabajo tú.

Sin `Retry-After`, backoff exponencial con jitter — jitter para que los llamantes que se recuperan de
una caída no se resincronicen provocando una segunda.

### Una espera no es un cuelgue

El `Retry-After` de un `429` son segundos hasta que se reinicia la ventana, así que va de 0 a 60. Un
SDK que lo respeta parece, desde fuera, una llamada lenta entre llamadas rápidas. Eso ya se ha
reportado como cuelgue tres veces, por tres equipos distintos.

Así que la espera se reporta dos veces. Una en el log, a nivel `INFO` cuando es larga como para que
una persona lo note — el backoff de menos de un segundo se queda en `DEBUG`, porque la preocupación
por ruido son los reintentos pequeños y frecuentes, no el raro largo. Y otra como **dato en la
respuesta**:

```python
completion = client.chat.completions.create(model="qwen3-0.6b", messages=[...])

completion.meta.waited_s    # 36.0
completion.meta.attempts    # 2
```

```go
completion, err := client.Chat.Create(ctx, request)

completion.Meta.WaitedFor   // 36s
completion.Meta.Attempts    // 2
```

```rust
let completion = client.chat(&request).await?;

completion.meta.waited_for; // 36s
completion.meta.attempts;   // 2
```

```swift
let completion = try await client.chat(request)

completion.meta.waitedFor   // 36s
completion.meta.attempts    // 2
```

```typescript
const completion = await client.chat.completions.create(request);

completion.meta.waitedMs; // 36000
completion.meta.attempts; // 2
```

Usa el segundo. Una línea de log es invisible salvo que la aplicación haya configurado un handler
—los SDK instalan un `NullHandler` y no tocan tu logging— y un panel de latencia no sabe leerla de
todas formas.

> **Límite conocido, en cuatro de los cinco.** Una llamada que esperó y después falló *igualmente* no
> reporta nada de esto en Python, Go, Rust ni Swift: la excepción no lleva metadatos de respuesta. Esa
> es la llamada cuya duración más necesita explicación, y está en el roadmap en vez de hecha.
>
> **TypeScript sí lo contesta**, porque sus errores ya llevan `meta` — así que los contadores se
> estampan antes de que el error salga, y `error.meta.waitedMs` dice cuánto durmió una llamada que
> falló:
>
> ```typescript
> catch (error) {
>   if (error instanceof APIError) {
>     log.warn("gave up", { attempts: error.meta.attempts, waitedMs: error.meta.waitedMs });
>   }
> }
> ```

## Idempotencia

Una `Idempotency-Key` cambia lo que es seguro, y es lo único que hace reintentable una petición que
dio timeout:

```python
completion = client.chat.completions.create(
    model="qwen3-0.6b",
    messages=[{"role": "user", "content": "..."}],
    idempotency_key="order-4417-summary",
)
```

```go
completion, err := client.Chat.Create(ctx, axonium.ChatRequest{
	Model:          "qwen3-0.6b",
	Messages:       []axonium.Message{axonium.TextMessage("user", "...")},
	IdempotencyKey: "order-4417-summary",
})
```

```rust
let completion = client
    .chat(&ChatRequest {
        model: "qwen3-0.6b".into(),
        messages: vec![Message::text("user", "...")],
        idempotency_key: "order-4417-summary".into(),
        ..Default::default()
    })
    .await?;
```

```swift
let completion = try await client.chat(
    ChatRequest(model: "qwen3-0.6b", messages: [.user("...")]),
    idempotencyKey: "order-4417-summary")
```

```typescript
const completion = await client.chat.completions.create(
  { model: "qwen3-0.6b", messages: [{ role: "user", content: "..." }] },
  { idempotencyKey: "order-4417-summary" },
);
```

Con clave, una repetición devuelve el resultado **almacenado**: no se alcanza ningún modelo, no se
registra uso, nada cuenta contra el tope de gasto. Así que el reintento cuesta un viaje de ida y
vuelta en vez de una generación, y la objeción del timeout desaparece. Sin clave sigue en pie la
regla antigua, porque nada del peligro ha cambiado.

**Y el caso que hace que merezca la pena mandar clave en toda llamada larga**: un reintento mientras
la primera petición *sigue corriendo* no es un fallo ni una segunda generación. El gateway contesta
`409 idempotency-in-progress`, que es el **único `409` del catálogo marcado como reintentable**, así
que el SDK lo espera por ti y devuelve el resultado almacenado cuando la original termina.

| al reintentar | el gateway contesta | qué hace el SDK |
|---|---|---|
| la primera **terminó** | el resultado almacenado | lo devuelve — ningún modelo alcanzado, ningún uso registrado, nada contra el tope de gasto |
| la primera **sigue corriendo** | `409 idempotency-in-progress` | lo reintenta, hasta que la original acaba |
| misma clave, una huella que **no coincide** | `409 idempotency-key-reuse` | lanza; ver abajo — un cuerpo distinto es la causa habitual, no la única |

Así que con clave un reintento no es una apuesta: o recoge el resultado o lo espera. Dos bordes que
conviene conocer:

Las claves están limitadas a 255 caracteres y el SDK lo comprueba antes de enviar — el gateway
reporta una clave demasiado larga como un *conflicto*, lo que apunta la investigación en la dirección
equivocada.

**Un `409 idempotency-key-reuse` no siempre significa que reusaste la clave.** La huella se toma
sobre el modelo de petición **del gateway, con sus defaults**, no sobre los bytes que mandaste, así
que un cambio aditivo a ese modelo invalida todas las claves guardadas antes. Veritium lo midió el
2026-10-08: `PRM-235` agregó dos campos opcionales con default nulo, y desde ese despliegue un
cliente que reenviaba una petición byte a byte idéntica recibía este error. El remedio que dice el
propio contrato —*reenvía la petición original sin cambios*— es exactamente lo que falla.

Así que **no acuñes una clave nueva por reflejo.** Comprueba si el cuerpo cambió de verdad:

- **Cambió** — una clave nueva es lo correcto, y el error estaba haciendo su trabajo.
- **No cambió** — una clave nueva compra una **segunda generación facturable** por un trabajo que
  la primera petición puede haber terminado ya, que es justo el daño que una clave existe para
  evitar. Espera a que pase la ventana.

Ningún SDK reintenta este error, y ninguno se recupera de él acuñando una clave nueva, a propósito:
en el caso de mal uso real eso facturaría doble en silencio. Se ha pedido a la plataforma que tome
la huella sobre lo que envía el cliente (`VRT-PRM-004`).

`409 idempotency-response-not-retained` significa que la original tuvo éxito pero su respuesta pasó
del tope de retención de **1 MiB**, así que nunca se guardó. Se generó y se facturó; simplemente no
hay nada que repetir. Las generaciones largas llegan a esto.

### Distinguir una repetición de una generación

```python
if completion.meta.idempotent_replay:
    billed = completion.meta.idempotent_replay_of
```

```go
if completion.Meta.IdempotentReplay {
	billed := completion.Meta.IdempotentReplayOf
	_ = billed
}
```

```rust
if completion.meta.idempotent_replay {
    let billed = &completion.meta.idempotent_replay_of;
}
```

```swift
if completion.meta.idempotentReplay {
    let billed = completion.meta.idempotentReplayOf
}
```

```typescript
if (completion.meta.idempotentReplay) {
  const billed = completion.meta.idempotentReplayOf;
}
```

Una repetición lleva su **propio** `request_id`, y ese id no tiene fila de uso — buscarlo devuelve
`404`, correctamente, porque repetir no alcanzó ningún modelo y no se facturó.
`idempotent_replay_of` es el id de la generación que **sí** se facturó, y la única ruta desde la
respuesta que recibiste hasta el cargo que le corresponde.

Si concilias uso a partir de ids de respuesta, necesitas este campo. Sin él, una auditoría que
empieza en el id de una repetición no encuentra nada **y no puede decir por qué**.

### No se sabe si un fallo libera la clave

Todo lo de arriba describe lo que una clave puede **repetir**. Deliberadamente no dice nada sobre qué
le pasa a la clave cuando la primera petición *falla*, porque no lo sabemos, y la diferencia importa
a quien derive sus claves en vez de generarlas al azar.

Un consumidor con claves deterministas — `(run_id, step_id, huella-del-cuerpo)`, para que reanudar
reproduzca en vez de pagar dos veces — reportó que un paso que falló una vez siguió devolviendo el
error almacenado durante toda la ventana de 24 horas, en milisegundos, así que sus reintentos no
volvieron a alcanzar un modelo. No pudimos reproducirlo contra nuestro despliegue con los fallos que
sabemos provocar (`400 unknown-instance`): la clave seguía usable después, y una segunda llamada con
otro cuerpo tuvo éxito en vez de ser rechazada. Su caso era un `5xx`, que no podemos forzar.

Así que la afirmación honesta es: **si una petición fallida retiene su clave es indefinido aquí**, lo
decide el gateway y no este SDK, y está preguntado. Hasta que se conteste, trata una clave derivada
cuya petición falló como posiblemente inusable el resto de la ventana, y ten en cuenta que un error
almacenado llega sin `Idempotent-Replay`, así que es indistinguible de uno nuevo.

## Enfriamientos

El gateway corre su propio cortacircuitos por backend, así que los SDK no añaden un segundo — se
abriría con señales que el servidor ya contó, y con peor información.

Lo que queda sin cubrir es lo que el gateway no puede reportar: que el propio gateway sea
inalcanzable. Para eso hay un pequeño registro de enfriamiento indexado por `(host, modelo)`. Cuando
llega un `503` con `Retry-After`, las llamadas siguientes a ese modelo fallan en local hasta que
expira, en vez de gastar una petición para que te digan lo mismo. El error lo dice explícitamente,
para que un fallo local rápido no se confunda con una respuesta real del gateway.

El enfriamiento es por modelo, no por host: un backend caído no detiene el resto.

## Capturar cosas

```python
from axonium import RateLimitError, SpendCapExceededError, APIError

try:
    completion = client.chat.completions.create(model="qwen3-0.6b", messages=[...])
except SpendCapExceededError:
    ...                      # not retryable, ever; a human decision
except RateLimitError as error:
    schedule_in(error.retry_after)
except APIError as error:
    log.warning("gateway said no", extra={"request_id": error.request_id})
    raise
```

```go
var apiErr *axonium.APIError
if errors.As(err, &apiErr) && apiErr.Retryable() {
	// ...
}
if errors.Is(err, axonium.ErrRateLimit) {
	// ...
}
```

```rust
match client.chat(&request).await {
    Err(Error::Api(api)) if api.kind == ErrorKind::SpendCapExceeded => { /* ... */ }
    Err(Error::Api(api)) if api.retryable() => { /* ... */ }
    other => other?,
}
```

```swift
do {
    let completion = try await client.chat(request)
} catch let AxoniumError.api(error) where error.kind == .spendCapExceeded {
    // not retryable, ever; a human decision
} catch let AxoniumError.api(error) where error.isRetryable {
    // ...
}
```

```typescript
import { RateLimitError, SpendCapExceededError, APIError } from "axonium";

try {
  const completion = await client.chat.completions.create({ model: "qwen3-0.6b", messages });
} catch (error) {
  if (error instanceof SpendCapExceededError) throw error; // not retryable, ever
  if (error instanceof RateLimitError) scheduleIn(error.retryAfter);
  else if (error instanceof APIError) log.warn("gateway said no", { requestId: error.requestId });
  else throw error;
}
```

Cada error lleva `request_id`, y también `trace_id` en un despliegue actual. El sobre de rate-limit
llegó a omitir `trace_id`; desde la guía `2026-09-19b` ese sobre es un superconjunto estricto del
estándar, así que los dos ids están. Los SDK siguen modelando `trace_id` como opcional, porque un
despliegue anterior a ese arreglo lo omite. Esos dos ids son lo que necesita un equipo de plataforma;
un reporte de error sin ellos es la descripción de una sensación.
