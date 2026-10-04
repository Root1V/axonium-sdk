/**
 * A full tool-calling round trip: the model asks, you answer, the model concludes.
 *
 *   node 4-tool-calling.ts
 */
import { Axonium, decodedArguments, type Message, type Tool } from "axonium";

const api = new Axonium();

const catalogue = await api.models.mine();
const model = catalogue.data.find((m) => m.modality === "text");
if (!model) throw new Error(`No text model granted. This token holds: ${catalogue.ids.join(", ")}`);

const tools: Tool[] = [
  {
    type: "function",
    function: {
      name: "get_weather",
      description: "El tiempo actual de una ciudad",
      parameters: {
        type: "object",
        properties: { city: { type: "string", description: "Nombre de la ciudad" } },
        required: ["city"],
        additionalProperties: false,
      },
    },
  },
];

/** Whatever your tool actually does. */
function getWeather(city: string): string {
  return JSON.stringify({ city, temperature_c: 19, conditions: "nublado" });
}

const messages: Message[] = [{ role: "user", content: "¿Qué tiempo hace en Lima?" }];

const first = await api.chat.completions.create({
  model: model.id,
  messages,
  tools,
  tool_choice: "auto",
  max_tokens: 300,
});

if (!first.toolCalls) {
  console.log("El modelo contestó en prosa en vez de llamar:", first.content);
  process.exit(0);
}

// The assistant's turn goes back verbatim, tool calls included, or the model loses track of what it
// asked for and the ids in the next message match nothing.
messages.push({
  role: "assistant",
  content: first.content ?? null,
  tool_calls: [...first.toolCalls],
});

for (const call of first.toolCalls) {
  // `arguments` is a STRING the model wrote, and a generation stopped by max_tokens leaves it
  // truncated — so it is parsed on demand rather than eagerly. Parsing eagerly would fail the whole
  // response and lose the text and the correlation ids with it.
  let args: { city?: string };
  try {
    args = decodedArguments(call) as { city?: string };
  } catch (error) {
    console.error(`argumentos inválidos para ${call.name} (finish=${first.finishReason}):`, error);
    continue;
  }
  console.log(`-> ${call.name}(${args.city})`);

  messages.push({
    role: "tool",
    tool_call_id: call.id ?? "",
    content: getWeather(args.city ?? "Lima"),
  });
}

const second = await api.chat.completions.create({
  model: model.id,
  messages,
  tools,
  max_tokens: 300,
});

console.log(`\n${second.content ?? "(sin contenido)"}`);
