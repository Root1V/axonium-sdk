/**
 * The smallest useful thing: ask a question, read the answer.
 *
 *   node 1-chat.ts
 */
import { Axonium } from "axonium";

const api = new Axonium(); // reads AXONIUM_GATEWAY_BASE_URL, AXONIUM_CLIENT_ID, AXONIUM_CLIENT_SECRET

// Pick a text model from what this token may actually call. `models.mine()` is cached for the client's
// lifetime, and it is what belongs behind a "test connection" button: it proves the gateway answers,
// the credential works, and there is something you may send. `GET /health` proves a process replied.
const catalogue = await api.models.mine();
const model = catalogue.data.find((m) => m.modality === "text");
if (!model) throw new Error(`No text model granted. This token holds: ${catalogue.ids.join(", ")}`);

const answer = await api.chat.completions.create({
  model: model.id,
  messages: [
    { role: "system", content: "Responde en una sola frase." },
    { role: "user", content: "¿Por qué el cielo es azul?" },
  ],
  max_tokens: 300,
});

console.log(answer.content ?? "(sin contenido)");

// A reasoning model puts its thinking here and can spend the whole budget on it, finishing with
// `content` empty. Showing only `content` then displays nothing, which looks like a broken SDK.
if (!answer.content && answer.reasoning) {
  console.log(`\n(el modelo razonó sin concluir; finish_reason=${answer.finishReason})`);
}

console.log(
  `\n${answer.usage?.promptTokens} tokens de entrada, ${answer.usage?.completionTokens} de salida` +
    (answer.usage?.cacheReadTokens ? ` (${answer.usage.cacheReadTokens} desde caché)` : ""),
);
// Worth logging on every call, not only on failures: correlating a slow-but-successful request matters
// as much as a failed one, and this is the id the platform team asks for.
console.log(`request id: ${answer.meta.requestId}`);
