<!-- translated-from: 02-calls.md sha256:6c54017156 -->
# Hacer llamadas

> **Ya tengo un cliente. ¿Qué le puedo pedir?**

Seis recursos. Cada uno devuelve un objeto tipado que además conserva el payload crudo, así que un
campo que el SDK no modela sigue siendo accesible.

## Chat completions

```python
completion = client.chat.completions.create(
    model="qwen3-0.6b",
    messages=[{"role": "user", "content": "Summarise this in one line: ..."}],
    max_tokens=200,
)
print(completion.content)          # the first choice's text
print(completion.usage.total_tokens)
```

```go
maxTokens := 200
completion, err := client.Chat.Create(ctx, axonium.ChatRequest{
	Model:     "qwen3-0.6b",
	Messages:  []axonium.Message{axonium.TextMessage("user", "Summarise this in one line: ...")},
	MaxTokens: &maxTokens,
})
fmt.Println(completion.Content())
```

```rust
let completion = client
    .chat(&ChatRequest {
        model: "qwen3-0.6b".into(),
        messages: vec![Message::text("user", "Summarise this in one line: ...")],
        max_tokens: Some(200),
        ..Default::default()
    })
    .await?;
println!("{}", completion.content());
```

```swift
let completion = try await client.chat(
    ChatRequest(
        model: "qwen3-0.6b",
        messages: [.user("Summarise this in one line: ...")],
        maxTokens: 200))
print(completion.content ?? "")        // the first choice's text
print(completion.usage?.totalTokens ?? 0)
```

```typescript
const completion = await client.chat.completions.create({
  model: "qwen3-0.6b",
  messages: [{ role: "user", content: "Summarise this in one line: ..." }],
  max_tokens: 200,
});
console.log(completion.content); // the first choice's text
console.log(completion.usage?.totalTokens);
```

`content` es un accesorio, no un campo. Entra al mensaje de la primera opción, y devuelve vacío en
vez de reventar cuando una respuesta no tiene opciones — algo que pasa en una generación cortada
antes de producir ninguna.

### Campos que el gateway no soporta

El gateway acepta un subconjunto permitido y **nombra de vuelta** lo que ignoró. Hasta `PRM-127` sí
descartaba en silencio, que es el problema que la cabecera vino a terminar: pones `frequency_penalty`,
nadie se queja, y nada lo aplica.

**Los cinco SDK hacen tres cosas distintas con un campo que no reconocen**, y conviene saberlo antes
de apoyarse en uno:

| | un campo no reconocido |
|---|---|
| Python | **no se envía**, con un `UnsupportedFieldWarning` que lo nombra |
| Go, Rust, Swift | se envía, por un canal explícito: `Extra` / `extra` / `extraFields` |
| TypeScript | se envía, en silencio — el tipo de petición acepta cualquier clave |

Así que un parámetro específico del motor como `chat_template_kwargs` llega a llama.cpp desde
TypeScript y no puede desde Python. Esa divergencia es `AXO-154` y está sin decidir.

Esa lista es el *modelo* que el SDK tiene de lo que el gateway acepta, y un modelo puede caducar:
cuando la plataforma empezó a honrar `response_format`, este SDK siguió avisando de que se
descartaría durante cinco días. El gateway reporta su propio veredicto en
`X-Prometheus-Ignored-Parameters`, que es la respuesta autoritativa porque es suya y no una conjetura
sobre él.

**TypeScript lo expone como `meta.ignoredParameters`** — `undefined` cuando la cabecera no está,
porque la cabecera solo aparece cuando hay algo que reportar, y un array vacío afirmaría que el
gateway miró y no encontró nada. Los otros cuatro leen la cabecera y la tiran; el nombre de aquí es
el que copiarán.

Pasa `require_parameters: true` para convertir un descarte silencioso en un `400 unknown-parameter`,
cuando que te den calladamente menos de lo que pediste es peor que fallar.

### Salida estructurada

```python
schema = {"type": "json_schema", "json_schema": {"name": "capital", "schema": {
    "type": "object", "properties": {"capital": {"type": "string"}}, "required": ["capital"]}}}

completion = client.chat.completions.create(
    model="qwen3-0.6b",
    messages=[{"role": "user", "content": "Capital of Peru?"}],
    response_format=schema,
)
answer = json.loads(completion.content)      # a JSON string, not a nested object
```

```go
completion, err := client.Chat.Create(ctx, axonium.ChatRequest{
	Model:          "qwen3-0.6b",
	Messages:       []axonium.Message{axonium.TextMessage("user", "Capital of Peru?")},
	ResponseFormat: schema,
})

var answer map[string]any
err = json.Unmarshal([]byte(completion.Content()), &answer)
```

```rust
let completion = client
    .chat(&ChatRequest {
        model: "qwen3-0.6b".into(),
        messages: vec![Message::text("user", "Capital of Peru?")],
        response_format: Some(schema),
        ..Default::default()
    })
    .await?;

let answer: serde_json::Value = serde_json::from_str(&completion.content())?;
```

```swift
var request = ChatRequest(model: "qwen3-0.6b", messages: [.user("Capital of Peru?")])
request.responseFormat = schema

let completion = try await client.chat(request)
let answer = try JSONSerialization.jsonObject(with: Data((completion.content ?? "").utf8))
```

```typescript
import { jsonSchema } from "axonium";

const completion = await client.chat.completions.create({
  model: "qwen3-0.6b",
  messages: [{ role: "user", content: "Capital of Peru?" }],
  // Accepts a plain JSON Schema, or anything with a toJSONSchema() method -- a Zod schema goes in
  // without this package ever importing Zod, which is what keeps it at zero dependencies.
  response_format: jsonSchema("capital", { type: "object", properties: { capital: { type: "string" } } }),
});
const answer = JSON.parse(completion.content); // a JSON string, not a nested object
```

La gramática es del motor, y el esquema se reenvía verbatim — validarlo aquí sería una segunda copia
de las reglas del motor, separándose de la primera.

### Cuánta confianza tenía el modelo

`logprobs` devuelve la probabilidad del token elegido; `top_logprobs` añade las N alternativas más
probables en cada posición. El objetivo es que un agente decida cuándo **escalar a una persona** en
vez de actuar sobre una suposición.

```python
completion = client.chat.completions.create(
    model="qwen3-0.6b",
    messages=[{"role": "user", "content": "yes or no?"}],
    logprobs=True,
    top_logprobs=3,
)
for token in completion.choices[0].logprobs.content:
    print(token.token, token.probability)     # 0.99946, not -0.00054
```

```go
yes := true
three := 3
completion, err := client.Chat.Create(ctx, axonium.ChatRequest{
	Model:       "qwen3-0.6b",
	Messages:    []axonium.Message{axonium.TextMessage("user", "yes or no?")},
	Logprobs:    &yes,
	TopLogprobs: &three,
})
for _, token := range completion.Choices[0].Logprobs.Content {
	fmt.Println(token.Token, token.Probability())
}
```

```rust
let completion = client
    .chat(&ChatRequest {
        model: "qwen3-0.6b".into(),
        messages: vec![Message::text("user", "yes or no?")],
        logprobs: Some(true),
        top_logprobs: Some(3),
        ..Default::default()
    })
    .await?;
for token in &completion.choices[0].logprobs.as_ref().unwrap().content {
    println!("{} {}", token.token, token.probability());
}
```

```swift
var request = ChatRequest(model: "qwen3-0.6b", messages: [.user("yes or no?")])
request.logprobs = true
request.topLogprobs = 3

let completion = try await client.chat(request)
for token in completion.choices[0].logprobs ?? [] {
    print(token.token ?? "", token.probability ?? 0)
}
```

```typescript
const completion = await client.chat.completions.create({
  model: "qwen3-0.6b",
  messages: [{ role: "user", content: "yes or no?" }],
  logprobs: true,
  top_logprobs: 3,
});
for (const token of completion.logprobs ?? []) {
  console.log(token.token, token.probability); // 0.99946, not -0.00054
}
```

**Son logaritmos naturales.** `-0,00054` es alrededor del 99,95 % y `-7,6` alrededor del 0,05 %.
Leído como probabilidad parece un número cercano a cero que significa «improbable», y el error no
hace ruido — así que los cinco exponen `probability` en vez de obligarte a recordar que hay que
llamar a `exp`. Es **ausente y no cero** cuando el backend no mandó `logprob`: un token del que no
dijo nada es un hecho distinto de uno que dijo imposible.

**`top_logprobs` necesita `logprobs`.** Mandarlo solo, o junto a `logprobs: false`, es un `422` que
el gateway lanza antes de que el motor lo vea — la regla es de llama.cpp y el gateway la aplica para
que el rechazo te llegue en el sobre de siempre. Los cinco SDK lo rechazan en el sitio de la llamada,
porque un viaje de ida y vuelta para que te lo digan no aporta nada.

**Que `logprobs` no venga en la respuesta significa que el motor no tiene la capacidad**, no que el
modelo dudara. La respuesta simplemente no lleva la clave. Manda `require_parameters` para que te lo
digan en vez de deducirlo de una ausencia.

**La respuesta llega como una cadena JSON en el contenido, no como un objeto anidado**, y los SDK no
la parsean por ti. El mismo motivo por el que los `arguments` de una herramienta siguen siendo una
cadena: una generación cortada por `max_tokens` la deja truncada, y un modelo de respuesta que lanza
desde dentro es peor que uno que te entrega lo que llegó. No es hipotético — la primera respuesta
estructurada que medimos escribiendo esto volvió como `{\n  "capital": "Lima"\n` con
`finish_reason: length`.

## Streaming

Un método aparte, no una bandera. Eso mantiene honesto el tipo de retorno, hace explícita la
comprobación del scope `inference:stream`, y da un solo sitio donde decir cuál de los dos fallos de
un stream se reintenta.

```python
with client.chat.completions.stream(
    model="qwen3-0.6b",
    messages=[{"role": "user", "content": "Count to five"}],
) as stream:
    for chunk in stream:
        print(chunk.content, end="", flush=True)
    print(stream.usage)
```

```go
stream, err := client.Chat.Stream(ctx, req)
if err != nil {
	return err
}
defer stream.Close()

for stream.Next() {
	fmt.Print(stream.Current().Content())
}
if err := stream.Err(); err != nil {
	return err
}
```

```rust
let mut stream = client.chat_stream(&request).await?;
while let Some(chunk) = stream.next().await? {
    print!("{}", chunk.content());
}
```

```swift
let stream = try await client.chatStream(
    ChatRequest(model: "qwen3-0.6b", messages: [.user("Count to five")]))

for try await chunk in stream {
    print(chunk.content ?? "", terminator: "")
}
print(await stream.usage() as Any)
```

```typescript
const stream = await client.chat.completions.stream({
  model: "qwen3-0.6b",
  messages: [{ role: "user", content: "Count to five" }],
});

for await (const chunk of stream) {
  process.stdout.write(chunk.content ?? "");
}
console.log(stream.usage);
```

Cuatro cosas que el stream maneja y un lector SSE ingenuo no:

- **El centinela `[DONE]`** termina la iteración; no se entrega como chunk.
- **Un rechazo que llega en vez del stream** es un status HTTP real, no un frame SSE — el gateway lee
  el status del motor antes de que existan las cabeceras `200`/`text/event-stream`. Lanza el mismo
  error tipado que lanzaría la llamada no-streaming, y **se reintenta como cualquier otra petición**.
  Ver [Fallos](03-failure.md).
- **Un error en banda** — un evento decodificado con una clave `error` de nivel superior — lanza
  `StreamInterruptedError` **con el texto acumulado hasta ese momento**, para que una respuesta
  parcial no se pierda con la excepción. La detección es por presencia de la clave, no por comparar
  el texto del mensaje, porque solo hay un mensaje documentado y no hay motivo para creer que sea el
  único. Este **nunca se reintenta**: parte de la respuesta se entregó y parte se facturó.
- **Un stream cortado sin `[DONE]`** termina la iteración con lo que llegó, en vez de colgarse.

Soltar el stream cancela la petición. En Rust eso es `Drop`; en Go es el `context`; en Python es
salir del bloque `with`.

## Embeddings, imágenes, rerank

```python
vectors = client.embeddings.create(model="qwen3-embedding", input=["first", "second"])

image = client.images.generate(model="sd-turbo", prompt="a lighthouse at dusk")
open("out.png", "wb").write(image.data[0].to_bytes())

ranked = client.rerank.create(
    model="qwen3-reranker",
    query="annual membership fee",
    documents=["Rates schedule", "Opening hours", "Card benefits"],
)
print(ranked.ranking)   # indices into the documents you sent, best first
```

```go
vectors, err := client.Embeddings.Create(ctx, axonium.EmbeddingRequest{
	Model: "qwen3-embedding",
	Input: []string{"first", "second"},
})

ranked, err := client.Rerank.Create(ctx, axonium.RerankRequest{
	Model:     "qwen3-reranker",
	Query:     "annual membership fee",
	Documents: []string{"Rates schedule", "Opening hours", "Card benefits"},
})
fmt.Println(ranked.Ranking())
```

```rust
let ranked = client
    .rerank(&RerankRequest {
        model: "qwen3-reranker".into(),
        query: "annual membership fee".into(),
        documents: vec!["Rates schedule".into(), "Opening hours".into()],
        ..Default::default()
    })
    .await?;
println!("{:?}", ranked.ranking());
```

```swift
let vectors = try await client.embeddings(
    EmbeddingRequest(model: "qwen3-embedding", input: ["first", "second"]))

let ranked = try await client.rerank(
    RerankRequest(
        model: "qwen3-reranker",
        query: "annual membership fee",
        documents: ["Rates schedule", "Opening hours", "Card benefits"]))
print(ranked.ranking)
```

```typescript
const vectors = await client.embeddings.create({
  model: "qwen3-embedding",
  input: ["first", "second"],
});

const image = await client.images.generate({ model: "sd-turbo", prompt: "a lighthouse at dusk" });
await writeFile("out.png", image.data[0]!.toBytes());

const ranked = await client.rerank.create({
  model: "qwen3-reranker",
  query: "annual membership fee",
  documents: ["Rates schedule", "Opening hours", "Card benefits"],
});
console.log(ranked.ranking); // indices into the documents you sent, best first
```

**`raw_scores` pide el logit en vez de la probabilidad.** Las probabilidades de un reranker se
saturan cerca de 1,0 — se midió 0,99 para un documento solo vagamente relacionado con su consulta — y
una probabilidad saturada no se puede calibrar mientras que el logit de detrás sí. No todos los
motores lo tienen; donde no, la petición sigue teniendo éxito y el campo vuelve nombrado en
`X-Prometheus-Ignored-Parameters`, así que mandarlo siempre es seguro y que se descarte es
descubrible y no silencioso.

**Rerank puntúa el conjunto entero de documentos en una petición.** Contra un presupuesto de 60 RPM,
puntuar 50 candidatos cuesta una unidad y no cincuenta. El `index` de cada resultado apunta al array
que **tú** mandaste, nunca a los resultados, que es lo que mantiene un resultado reordenado
atribuible a su entrada.

## Predict — las tareas para las que OpenAI no tiene forma

Tres modalidades van aquí y a ningún otro sitio: `classification`, `zero_shot` y `typed_decision`.
Todos los demás endpoints tienen forma de OpenAI porque cada tarea que sirven tiene un endpoint de
OpenAI al que parecerse. Estas no.

```python
result = client.predict.create("sst2-clf", {"inputs": "El servicio ha sido excelente"})
result.value        # [{"label": "POSITIVE", "score": 0.9783}]  -- a LIST, not a dict

result = client.predict.create(
    "von-decide",
    {"inputs": "Me cobraron dos veces la misma factura",
     "parameters": {"candidate_labels": ["cargo duplicado", "cliente satisfecho"]}},
)
result.value["labels"][0]   # "cargo duplicado"
```

```go
result, err := client.Predict.Create(ctx, "sst2-clf",
	map[string]any{"inputs": "El servicio ha sido excelente"}, axonium.PredictOptions{})

var labels []struct {
	Label string  `json:"label"`
	Score float64 `json:"score"`
}
err = result.Into(&labels)
```

```rust
let result = client
    .predict(
        "sst2-clf",
        &serde_json::json!({"inputs": "El servicio ha sido excelente"}),
        &PredictOptions::default(),
    )
    .await?;
let labels: Vec<Label> = result.decode()?;
```

```swift
let result = try await client.predict(
    model: "sst2-clf", body: ["inputs": "El servicio ha sido excelente"])

// A top-level ARRAY from this engine, an object from the next one. Decode what you expect from
// the payload_schema the catalog gave you, not from the modality.
let labels: [[String: JSONValue]] = try result.decode()
```

```typescript
const result = await client.predict.create("sst2-clf", {
  inputs: "El servicio ha sido excelente",
});
result.value; // [{ label: "POSITIVE", score: 0.9783 }] -- an ARRAY, not an object

const decided = await client.predict.create("von-decide", {
  inputs: "Me cobraron dos veces la misma factura",
  parameters: { candidate_labels: ["cargo duplicado", "cliente satisfecho"] },
});
```

**El cuerpo va al motor verbatim y su respuesta vuelve verbatim.** Inventar un cuerpo para estas
sería el gateway decidiendo, en nombre del motor, cómo debería ser la API del motor.

**Así que la respuesta se entrega sin decodificar, y eso no es tipado defensivo.** `sst2-clf` contesta
un **array** de nivel superior; `von-decide` y `laya-decide` contestan objetos. Un cliente que modelara
esto como un diccionario reportaría «esto no es JSON» sobre JSON válido, para el primer motor que la
plataforma sacó en esta ruta.

**Despacha por `payload_schema`, no por `modality`.** `sst2-clf` y `von-decide` son los dos
clasificadores y quieren cuerpos distintos, así que aquí no hay un `classify(text)` — un método tipado
prometería una estabilidad que el endpoint no ofrece. El catálogo dice qué contrato habla cada modelo:

| modelo | `modality` | `payload_schema` |
|---|---|---|
| `sst2-clf` | `classification` | `hf-inference.text-classification.v1` |
| `von-decide` | `zero_shot` | `hf-inference.zero-shot-classification.v1` |
| `laya-decide` | `typed_decision` | `typed-decision.v1` |
| `nli-tei` | `zero_shot` | `tei.predict.v1` |
| `emotions-tei` | `classification` | `tei.predict.v1` |

**Las dos últimas filas son por lo que esa tabla importa.** `von-decide` y `nli-tei` son los dos
`zero_shot` y contestan *cosas distintas que las dos suman 1*: el primero normaliza entre las
etiquetas candidatas que **tú** diste, el segundo entre las clases **del propio modelo** y no tiene
noción de candidatas en absoluto. Despacha por `modality` y lees una como la otra, en silencio.

`tei.predict.v1` lleva además una trampa que conviene conocer antes de lotear:

```text
inputs: "a text"                     → one flat list of {label, score}
inputs: ["premise", "hypothesis"]    → ONE PAIR, not a batch of two texts
inputs: ["a", "b", "c"]              → 422
inputs: [["a"], ["b"]]               → a batch of two single texts → two lists
```

**Un lote es siempre una lista de listas.** El obvio «manda mis N textos como array» es la única forma
que devuelve calladamente una sola respuesta equivocada. Es también por lo que el resultado se entrega
sin decodificar: una entrada devuelve lista plana y un lote devuelve lista de listas, del mismo modelo
y el mismo endpoint.

**Lo que no pasa a través es la política.** El modelo sigue resolviéndose, siguen haciendo falta
`inference:read` más el scope específico `model:<id>`, una réplica muerta se sigue saltando, y la
petición se sigue midiendo y sigue contando contra un tope de gasto. Las tres modalidades comparten
**un** presupuesto de rate-limit, llamado `predict` — así que las peticiones de clasificación y las
decisiones tipadas se comen los mismos 60 RPM.

**Un modelo que sí tiene endpoint de OpenAI se rechaza aquí** con `400 modality-mismatch`, el inverso
de la comprobación de todos los demás manejadores. Sin eso el mismo modelo sería alcanzable por dos
caminos, con dos rutas de facturación, y la que facturara bien sería la que no usaste.

## El catálogo

```python
client.models.list()    # what this token may call
client.models.mine()    # the subset your token is scoped to, cached
```

```go
client.Models.List(ctx)   // what this token may call
client.Models.Mine(ctx)   // the subset your token is scoped to, cached
```

```rust
client.models().await?;        // what this token may call
client.models_mine().await?;   // the subset your token is scoped to, cached
```

```swift
try await client.models()       // what this token may call
try await client.modelsMine()   // the subset your token is scoped to, cached
```

```typescript
await client.models.list(); // what this token may call
await client.models.mine(); // the subset your token is scoped to, cached
```

**El catálogo lista las instancias en marcha intersecadas con tus scopes**, medido el 05/10/2026. Un
modelo registrado pero parado desaparece exactamente igual que uno que no existe, e igual que uno que
tu token no puede llamar — tres hechos distintos detrás de una ausencia, y solo un operador los
distingue. Así que una lista vacía nunca es evidencia de que el despliegue no tenga modelos.

El acceso es denegar-por-defecto y se concede por modelo, y el streaming necesita un scope distinto
del no-streaming: tener `inference:read` no concede `inference:stream`. Cuando llega un `403` y ya se
ha leído `models.mine()`, el SDK dice cuál de los dos te falta en vez de solo que se denegó el acceso.

## Uso de una petición

```python
row = client.usage.retrieve(completion.meta.request_id)
row.usage.total_tokens
row.cost_usd            # None where no price is configured — not 0.0
row.termination_reason
```

```go
row, err := client.Usage.Retrieve(ctx, completion.Meta.RequestID)
row.Usage.TotalTokens
row.CostUSD             // nil where no price is configured — not 0
row.TerminationReason
```

```rust
let row = client.usage(&completion.meta.request_id).await?;
row.usage.total_tokens;
row.cost_usd;           // None where no price is configured -- not 0.0
row.termination_reason;
```

```swift
let row = try await client.usage(requestID: completion.meta.requestID)
row.usage.totalTokens
row.costUSD             // nil where no price is configured -- not 0
row.terminationReason
```

```typescript
const row = await client.usage.get(completion.meta.requestId!);
row.usage.totalTokens;
row.costUsd; // undefined where no price is configured -- not 0
row.terminationReason;
```

Esto no necesita `admin:read`. `cost_usd` es anulable a propósito: «nadie puso precio a esto» y «no
costó nada» son hechos distintos, y una columna que no los distingue reporta `0,00 $` para tráfico
que nunca fue gratis.

Siguiente: [Fallos, reintentos e idempotencia](03-failure.md).
