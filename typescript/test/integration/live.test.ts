/**
 * Integration tests against a real Prometheus gateway.
 *
 * **Skipped unless `AXONIUM_INTEGRATION=1` and credentials are present**, and never run in CI. They
 * spend real inference, so a contributor who has not opted in must not pay for them — and a suite that
 * failed on a missing credential would train everyone to ignore a red run.
 *
 * ```bash
 * AXONIUM_INTEGRATION=1 \
 *   AXONIUM_GATEWAY_BASE_URL=https://gateway.example \
 *   AXONIUM_CLIENT_ID=… AXONIUM_CLIENT_SECRET=… \
 *   npm run test:integration
 * ```
 *
 * **The model ids are discovered, not hardcoded.** A fixed id fails on any deployment that does not
 * serve it, which is every deployment but one — and the failure reads as a broken SDK rather than as a
 * different catalogue. Each test picks a model by **modality** from the live catalogue and skips with a
 * reason when the token holds no grant for that kind.
 *
 * What these cover that the contract corpus cannot: the corpus replays recorded bytes, so it proves
 * this SDK agrees with four others about a past response. Only a live gateway proves the contract still
 * holds — which is how a premise that quietly expired gets found, and this repository has found several.
 */
import { before, describe, it } from "node:test";
import assert from "node:assert/strict";

import { Axonium, imageFromBytes, jsonSchema } from "../../src/index.ts";
import type { Model } from "../../src/index.ts";
import { APIError, UnknownModelError } from "../../src/errors.ts";

const enabled =
  process.env["AXONIUM_INTEGRATION"] === "1" &&
  Boolean(process.env["AXONIUM_GATEWAY_BASE_URL"]) &&
  (Boolean(process.env["AXONIUM_CLIENT_ID"]) || Boolean(process.env["AXONIUM_CLIENT_SECRET"]));

/** An 8×8 PNG, half red and half blue, so a vision model has something unambiguous to describe. */
const RED_AND_BLUE_PNG =
  "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAIAgMAAAAPLIa+AAAAD1BMVEX/AAAAAP8AAAAAAAAAAACfKy1cAAAAEElEQVR4nGNgQAb/GdABNgAAQAAP9eUKcwAAAABJRU5ErkJggg==";

describe(
  "against a live gateway",
  { skip: enabled ? false : "set AXONIUM_INTEGRATION=1 and credentials" },
  () => {
    let api: Axonium;
    let catalogue: Model[];

    const byModality = (...modalities: string[]): Model | undefined =>
      catalogue.find((m) => m.modality !== undefined && modalities.includes(m.modality));

    before(async () => {
      api = new Axonium();
      catalogue = [...(await api.models.list()).data];
      assert.ok(catalogue.length > 0, "the catalogue is empty: this token holds no model grants");
    });

    it("lists the catalogue and every entry carries a payload schema", async () => {
      const list = await api.models.list();
      assert.ok(list.data.length > 0);
      // payload_schema is the field that says which BODY a model takes, and it is the answer to the
      // question `modality` cannot answer -- two classifiers, two shapes.
      const without = list.data.filter((m) => !m.payloadSchema).map((m) => m.id);
      assert.deepEqual(without, [], `models with no payload_schema: ${without.join(", ")}`);
      // An image model has no context window at all, and null must not arrive as a billable zero.
      for (const model of list.data) {
        if (model.modality === "image") assert.equal(model.contextLength, undefined);
      }
    });

    it("models.mine is an alias of the catalogue since PRM-167", async () => {
      const [all, mine] = [await api.models.list(), await api.models.mine()];
      assert.deepEqual([...mine.ids].sort(), [...all.ids].sort());
    });

    it("answers a chat completion with usage and correlation ids", async () => {
      const model = byModality("text");
      if (!model) return assert.ok(true, "no text model granted");

      const answer = await api.chat.completions.create({
        model: model.id,
        messages: [{ role: "user", content: "Responde solo con la palabra: listo" }],
        max_tokens: 400,
      });

      assert.ok(answer.meta.requestId, "no request id to correlate with");
      assert.ok((answer.usage?.promptTokens ?? 0) > 0);
      // Content OR reasoning: a reasoning model can spend the whole budget thinking and finish with
      // content empty, which is the model's behaviour and not a fault. Asserting content alone made an
      // earlier run of this look broken.
      assert.ok(
        (answer.content ?? "").length > 0 || (answer.reasoning ?? "").length > 0,
        `neither content nor reasoning, finish=${answer.finishReason}`,
      );
    });

    it("streams, and assembles content or reasoning across chunks", async () => {
      const model = byModality("text");
      if (!model) return assert.ok(true, "no text model granted");

      const stream = await api.chat.completions.stream({
        model: model.id,
        messages: [{ role: "user", content: "Cuenta del uno al cinco." }],
        max_tokens: 200,
      });
      let chunks = 0;
      for await (const _ of stream) chunks += 1;

      assert.ok(chunks > 1, `${chunks} chunks: this did not stream`);
      assert.ok(stream.content.length > 0 || stream.reasoning.length > 0);
      // Streamed usage is derived from the final chunk's timings on a llama.cpp-family backend, and the
      // flag is what keeps a caller from mistaking a derived number for a measured one.
      if (stream.usage) assert.equal(typeof stream.usage.estimated, "boolean");
    });

    it("cancels a stream through an AbortSignal", async () => {
      const model = byModality("text");
      if (!model) return assert.ok(true, "no text model granted");

      const controller = new AbortController();
      const stream = await api.chat.completions.stream(
        {
          model: model.id,
          messages: [{ role: "user", content: "Escribe un ensayo largo." }],
          max_tokens: 500,
        },
        { signal: controller.signal },
      );
      let chunks = 0;
      try {
        for await (const _ of stream) {
          chunks += 1;
          if (chunks === 2) controller.abort();
        }
      } catch {
        // An abort mid-iteration surfaces as a read failure, which is the honest outcome: the generation
        // was stopped, not completed.
      }
      assert.ok(chunks >= 2 && chunks < 500, `${chunks} chunks after aborting at 2`);
    });

    it("describes an image sent as bytes", async () => {
      const model = byModality("vision");
      if (!model) return assert.ok(true, "no vision model granted");

      const bytes = Uint8Array.from(atob(RED_AND_BLUE_PNG), (c) => c.charCodeAt(0));
      const answer = await api.chat.completions.create({
        model: model.id,
        max_tokens: 80,
        messages: [
          {
            role: "user",
            content: [
              { type: "text", text: "Nombra los dos colores que ves, en dos palabras." },
              imageFromBytes(bytes, "image/png"),
            ],
          },
        ],
      });
      assert.ok((answer.content ?? "").length > 0, "the vision model returned nothing");
    });

    it("calls a tool and the arguments decode", async () => {
      const model = byModality("text");
      if (!model) return assert.ok(true, "no text model granted");

      const answer = await api.chat.completions.create({
        model: model.id,
        max_tokens: 200,
        messages: [{ role: "user", content: "¿Qué tiempo hace en Lima? Usa la herramienta." }],
        tools: [
          {
            type: "function",
            function: {
              name: "get_weather",
              description: "El tiempo actual de una ciudad",
              parameters: {
                type: "object",
                properties: { city: { type: "string" } },
                required: ["city"],
              },
            },
          },
        ],
        tool_choice: "auto",
      });

      // Not asserted as mandatory: whether a model chooses to call is the model's decision, and a small
      // one often answers in prose instead. What IS asserted is that a call, if made, is well-formed.
      if (!answer.toolCalls)
        return assert.ok(true, "the model answered in prose rather than calling");
      const call = answer.toolCalls[0];
      assert.ok(call);
      assert.equal(
        call.name,
        call.function.name,
        "the flat shortcut disagrees with the wire shape",
      );
      assert.ok(call.arguments.length > 0);
    });

    it("constrains an answer with a json schema", async () => {
      const model = byModality("text");
      if (!model) return assert.ok(true, "no text model granted");

      const answer = await api.chat.completions.create({
        model: model.id,
        max_tokens: 200,
        messages: [{ role: "user", content: "¿Cuál es la capital de Perú?" }],
        response_format: jsonSchema("capital", {
          type: "object",
          properties: { capital: { type: "string" } },
          required: ["capital"],
          additionalProperties: false,
        }),
      });
      // The content arrives as a JSON STRING, not as a nested object -- which is the thing the guide
      // says twice and that a caller gets wrong once.
      if (!answer.content) return assert.ok(true, `no content, finish=${answer.finishReason}`);
      const parsed = JSON.parse(answer.content) as { capital?: unknown };
      assert.equal(typeof parsed.capital, "string");
    });

    it("embeds, and the vectors have a consistent width", async () => {
      const model = byModality("embedding");
      if (!model) return assert.ok(true, "no embedding model granted");

      const list = await api.embeddings.create({ model: model.id, input: ["hola", "mundo"] });
      assert.equal(list.data.length, 2);
      const widths = new Set(list.data.map((d) => d.embedding.length));
      assert.equal(widths.size, 1, `inconsistent widths: ${[...widths].join(", ")}`);
      assert.ok((list.data[0]?.embedding.length ?? 0) > 0);
      // An embedding generates nothing, so a completion count of zero would be a number where the
      // absence of one belongs.
      assert.equal(list.usage?.completionTokens, undefined);
    });

    it("reranks, and the indices point into the documents that were sent", async () => {
      const model = byModality("rerank");
      if (!model) return assert.ok(true, "no rerank model granted");

      const documents = [
        "Lima es la capital de Perú.",
        "Un reranker es un cross-encoder.",
        "Horario de apertura.",
      ];
      const ranked = await api.rerank.create({
        model: model.id,
        query: "¿qué es un reranker?",
        documents,
      });
      assert.equal(ranked.results.length, documents.length);
      // The property the whole accessor exists for, and that no contract case pins (AXO-129).
      assert.deepEqual([...ranked.ranking].sort(), [0, 1, 2]);
      assert.equal(ranked.ranking[0], 1, "the reranker did not put the reranker sentence first");
    });

    it("passes a predict body through untouched, whatever shape comes back", async () => {
      const model = byModality("classification", "zero_shot", "typed_decision");
      if (!model) return assert.ok(true, "no pass-through model granted");

      const body =
        model.modality === "zero_shot"
          ? {
              inputs: "Me cobraron dos veces",
              parameters: { candidate_labels: ["cargo duplicado", "satisfecho"] },
            }
          : model.modality === "typed_decision"
            ? {
                state: { email: "Me cobraron dos veces" },
                questions: { dup: { type: "noul", instructions: "¿Cargo duplicado?" } },
              }
            : { inputs: "El servicio ha sido excelente" };

      const result = await api.predict.create(model.id, body);
      assert.notEqual(result.value, undefined, "the engine's answer was dropped");
      // All three pass-through modalities share ONE budget, which a caller pacing itself has to know.
      assert.equal(result.meta.rateLimit?.scope, "predict");
    });

    it("reads back the usage row of a request it just made", async () => {
      const model = byModality("text");
      if (!model) return assert.ok(true, "no text model granted");

      const answer = await api.chat.completions.create({
        model: model.id,
        messages: [{ role: "user", content: "hola" }],
        max_tokens: 16,
      });
      assert.ok(answer.meta.requestId);

      const row = await api.usage.get(answer.meta.requestId);
      assert.equal(row.requestId, answer.meta.requestId);
      assert.equal(row.requestKind, "chat");
      assert.equal(
        row.usage?.promptTokens,
        answer.usage?.promptTokens,
        "the ledger and the response disagree",
      );
    });

    it("types a real failure, and refuses a bad model locally never", async () => {
      // "never" is the point: absence from the catalogue stopped being evidence at PRM-167, because the
      // catalogue now holds only what this token is granted. So an unknown model costs one request and
      // the gateway answers the question only it can answer.
      await assert.rejects(
        api.chat.completions.create({
          model: "no-existe-de-verdad",
          messages: [{ role: "user", content: "x" }],
        }),
        (err: unknown) => {
          assert.ok(err instanceof UnknownModelError, `got ${String(err)}`);
          assert.equal(err.status, 400);
          assert.ok(err.meta.requestId, "a failure with no id to report");
          return true;
        },
      );
    });

    it("wraps an unserved route in the gateway's own envelope", async () => {
      // PRM-174. Before it, this answered a bare {"detail": "Not Found"} with no type and no ids.
      await assert.rejects(
        api.usage.get("00000000-0000-0000-0000-000000000000"),
        (err: unknown) => {
          assert.ok(err instanceof APIError);
          assert.ok(
            ["not-found", "unknown-route"].includes(err.typeSuffix),
            `suffix was ${err.typeSuffix}`,
          );
          return true;
        },
      );
    });

    it("reports the rate-limit budget, and names which one", async () => {
      await api.models.list();
      const snapshot = api.lastRateLimit;
      assert.ok(snapshot, "no rate-limit snapshot after a successful call");
      assert.ok(snapshot.scope, "the budget was reported without saying which");
      assert.ok((snapshot.limitRequests ?? 0) > 0);
    });

    it("reads the granted scope back, and it is not the one requested", async () => {
      const scope = api.grantedScope;
      assert.ok(scope.length > 0, "no granted scope on the token");
      assert.ok(scope.includes("inference:read"), `scope was ${scope.join(" ")}`);
      // sub, not client_id: the claim name this SDK got wrong once (AXO-127).
      assert.ok(api.tokenClaims?.subject, "the token carries no subject");
    });
  },
);
