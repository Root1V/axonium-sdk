/**
 * Describing a screenshot — the shape a computer-use agent needs.
 *
 *   node 3-vision.ts path/to/screenshot.png
 */
import { readFileSync } from "node:fs";
import { Axonium, imageFromBytes, jsonSchema } from "axonium";

const path = process.argv[2];
if (!path) throw new Error("usage: node 3-vision.ts <image>");

const api = new Axonium();

const catalogue = await api.models.mine();
const model = catalogue.data.find((m) => m.modality === "vision");
if (!model)
  throw new Error(`No vision model granted. This token holds: ${catalogue.ids.join(", ")}`);

const bytes = new Uint8Array(readFileSync(path));
const mediaType = path.endsWith(".jpg") || path.endsWith(".jpeg") ? "image/jpeg" : "image/png";

// `imageFromBytes` produces a base64 data URI. **There is no helper for a remote URL**, because the
// gateway refuses `http(s)://` as an SSRF mitigation — so an API that accepted one would accept
// something that always fails. A part carrying an http(s) url is refused here before the round trip.
const answer = await api.chat.completions.create({
  model: model.id,
  max_tokens: 300,
  messages: [
    {
      role: "user",
      content: [
        { type: "text", text: "¿Qué elemento hay que pulsar para enviar el formulario?" },
        imageFromBytes(bytes, mediaType),
      ],
    },
  ],
  // Structured output so the answer is usable rather than prose. The content comes back as a JSON
  // **string**, not a nested object — parse it.
  response_format: jsonSchema("target", {
    type: "object",
    properties: {
      label: { type: "string" },
      x: { type: "number" },
      y: { type: "number" },
    },
    required: ["label"],
    additionalProperties: false,
  }),
});

console.log(`modelo: ${model.id} (${model.contextLength ?? "?"} de contexto)`);
console.log(answer.content ?? "(sin contenido)");

if (answer.content) {
  const target = JSON.parse(answer.content) as { label: string; x?: number; y?: number };
  console.log(
    `\n-> pulsar ${target.label}`,
    target.x !== undefined ? `en (${target.x}, ${target.y})` : "",
  );
}
