/**
 * Does a reasoning model's own analysis go back OUT, or only come in?
 *
 * `PRM-VRT-003`, 2026-10-13. Prometheus found their gateway **deleting `reasoning_content` from
 * every message of every client** — their internal message model did not name the field, Pydantic
 * dropped what it did not name, and the serializer wrote the rest to the engine. Fixed on their
 * side, and then they asked the three frameworks the one question they could not answer
 * themselves: does that field survive *your* serialization?
 *
 * **Not cosmetic.** llama.cpp issue #27720 is closed as not fixable in the parser and the thread
 * measures the cost: **7 failed turns out of 120** with the reasoning withheld, **0 of 120** with
 * it returned. A reasoning model continuing from its own tool call, without its analysis channel,
 * emits malformed headers that lose the whole turn. On models that do not use Harmony it is not an
 * error at all — it is quietly worse answers.
 *
 * **What measuring found here.** The runtime always passed the field through, so an object built
 * as `unknown` and cast reached the wire. But the `Message` interface did not declare it, so a
 * caller writing the turn as a typed literal got
 * `TS2353: 'reasoning_content' does not exist in type 'Message'` — **the supported way to do the
 * right thing did not compile.** Swift was worse: it could not send it at all.
 *
 * **And the hole was never the field, it was that nothing looked.** Synaptum and Aeon each
 * reported the same shape in their own words: their suites asserted what arrived and none asserted
 * what was sent back. A field that enters and does not leave breaks nothing visible.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { Axonium, type Message } from "../src/client.ts";
import { Stub } from "./stub.ts";

const CHAT = "/v1/chat/completions";

function client(stub: Stub): Axonium {
  return new Axonium({
    gatewayBaseURL: "https://gw.test",
    clientId: "id",
    clientSecret: "secret",
    fetch: stub.fetch,
  });
}

/**
 * The shape the measurement is about, written as a **typed literal on purpose**.
 *
 * This is the half a runtime test cannot cover: before the interface declared the field, this
 * declaration was a compile error, so `npm run build` is part of the assertion.
 */
const REASONING = "The user wants weather. I should call get_weather.";

const turnThatCalledATool: Message = {
  role: "assistant",
  content: null,
  reasoning_content: REASONING,
  tool_calls: [
    {
      id: "c1",
      type: "function",
      function: { name: "get_weather", arguments: '{"city":"Quito"}' },
    },
  ],
};

test("reasoning_content leaves on an assistant turn", async () => {
  const stub = new Stub().token().on(CHAT, { body: JSON.stringify({ ok: true }) });

  await client(stub).chat.completions.create({
    model: "gpt-oss-20b-mxfp4",
    messages: [
      { role: "user", content: "weather in Quito?" },
      turnThatCalledATool,
      { role: "tool", tool_call_id: "c1", content: "cloudy, 17C" },
    ],
  });

  const sent = JSON.parse(stub.lastFor(CHAT)?.body ?? "{}");
  assert.equal(sent.messages[1].reasoning_content, REASONING);
  // The tool call has to survive beside it: the measured turn carries both, and a fix that sent the
  // reasoning while dropping the call would satisfy a weaker assertion.
  assert.equal(sent.messages[1].tool_calls[0].id, "c1");
});

test("reasoning_content is absent, not empty, when the model reported none", async () => {
  // Absent and empty are different assertions, and the second one claims the model thought about
  // nothing. Aeon and Synaptum fixed this field in the same week and both wrote down this rule.
  const stub = new Stub().token().on(CHAT, { body: JSON.stringify({ ok: true }) });

  await client(stub).chat.completions.create({
    model: "m",
    messages: [{ role: "assistant", content: "hi" }],
  });

  const sent = JSON.parse(stub.lastFor(CHAT)?.body ?? "{}");
  assert.equal("reasoning_content" in sent.messages[0], false);
});

test("a completion's reasoning can be handed straight back", async () => {
  // The round trip, which is what a tool-use loop does. End to end rather than field by field,
  // because the failure lives in the join: the response side exposed the reasoning and the request
  // side would not type it, and each half looked right on its own.
  const completionBody = JSON.stringify({
    id: "c",
    object: "chat.completion",
    created: 0,
    model: "gpt-oss-20b-mxfp4",
    choices: [
      {
        index: 0,
        finish_reason: "tool_calls",
        message: {
          role: "assistant",
          content: null,
          reasoning_content: "thinking out loud",
          tool_calls: [{ id: "c1", type: "function", function: { name: "f", arguments: "{}" } }],
        },
      },
    ],
  });
  const stub = new Stub()
    .token()
    .on(CHAT, { body: completionBody }, { body: JSON.stringify({ ok: true }) });
  const axonium = client(stub);

  const first = await axonium.chat.completions.create({
    model: "gpt-oss-20b-mxfp4",
    messages: [{ role: "user", content: "weather?" }],
  });
  assert.equal(first.reasoning, "thinking out loud");

  await axonium.chat.completions.create({
    model: "gpt-oss-20b-mxfp4",
    messages: [
      { role: "user", content: "weather?" },
      {
        role: "assistant",
        content: first.content ?? null,
        reasoning_content: first.reasoning ?? undefined,
      },
      { role: "tool", tool_call_id: "c1", content: "cloudy" },
    ],
  });

  const sent = JSON.parse(stub.lastFor(CHAT)?.body ?? "{}");
  assert.equal(sent.messages[1].reasoning_content, "thinking out loud");
});
