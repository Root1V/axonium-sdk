import { StreamInterruptedError, type ResponseMeta } from "./errors.ts";
import type { Usage } from "./resources.ts";

/** One `data:` payload from the stream, decoded. */
export interface Chunk {
  readonly raw: Record<string, unknown>;
  /** The text this chunk added, or `""`. */
  readonly delta: string;
}

/** The sentinel that ends a well-behaved stream. */
const DONE = "[DONE]";

function asInt(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? Math.trunc(value) : undefined;
}

/**
 * Splits a byte stream into SSE events.
 *
 * Done by hand over `ReadableStream` rather than with a library, because the framing is four rules
 * and a library would be a runtime dependency on every edge deployment.
 *
 * **The buffer is flushed at end of stream.** A final event with no trailing blank line is otherwise
 * dropped silently — and a truncated stream is exactly when a caller most needs what did arrive.
 */
export async function* events(body: ReadableStream<Uint8Array>): AsyncGenerator<string> {
  const decoder = new TextDecoder();
  const reader = body.getReader();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      // `stream: true` matters: a multi-byte character split across two network packets would
      // otherwise decode as two replacement characters, and the text would be quietly wrong rather
      // than visibly broken.
      buffer += decoder.decode(value, { stream: true });

      for (;;) {
        // Both framings, because a proxy may rewrite line endings and the gateway's own bytes use
        // \n. Checked longest-first or \r\n\r\n would match \n\n at an offset.
        const boundary = findBoundary(buffer);
        if (!boundary) break;
        const event = buffer.slice(0, boundary.index);
        buffer = buffer.slice(boundary.index + boundary.length);
        if (event.trim()) yield event;
      }
    }
    buffer += decoder.decode();
    if (buffer.trim()) yield buffer;
  } finally {
    // Releasing matters on an early exit -- a `break` out of the caller's `for await` -- or the
    // connection stays open until the socket times out.
    reader.releaseLock();
    await body.cancel().catch(() => {});
  }
}

function findBoundary(buffer: string): { index: number; length: number } | undefined {
  const rn = buffer.indexOf("\r\n\r\n");
  const nn = buffer.indexOf("\n\n");
  if (rn !== -1 && (nn === -1 || rn < nn)) return { index: rn, length: 4 };
  if (nn !== -1) return { index: nn, length: 2 };
  return undefined;
}

/** A tool call reassembled from a stream's chunks. */
export interface StreamedToolCall {
  readonly id?: string;
  readonly function: { readonly name: string; readonly arguments: string };
}

/** The `data:` payload of one event, with the SSE field prefixes removed. */
export function dataOf(event: string): string | undefined {
  const lines = event.split(/\r?\n/);
  const data: string[] = [];
  for (const line of lines) {
    if (line.startsWith(":")) continue; // a comment, which the spec allows as a keep-alive
    if (!line.startsWith("data:")) continue; // id:, event:, retry: are not ours to interpret
    data.push(line.slice(5).replace(/^ /, ""));
  }
  return data.length > 0 ? data.join("\n") : undefined;
}

/**
 * A chat stream: an `AsyncIterable` of chunks, with what was assembled available afterwards.
 *
 * `content`, `usage` and `toolCalls` are only complete once iteration finishes, which is what
 * {@link finalMessage} waits for.
 */
export class ChatStream implements AsyncIterable<Chunk> {
  readonly meta: ResponseMeta;
  private readonly body: ReadableStream<Uint8Array>;
  private readonly pieces: string[] = [];
  private readonly thoughts: string[] = [];
  private reportedUsage: Record<string, unknown> | undefined;
  private timings: Record<string, unknown> | undefined;
  private readonly calls = new Map<number, { id?: string; name: string; arguments: string }>();
  private finishReason: string | undefined;
  private consumed = false;

  constructor(body: ReadableStream<Uint8Array>, meta: ResponseMeta) {
    this.body = body;
    this.meta = meta;
  }

  /** The text assembled so far. Complete once iteration has finished. */
  get content(): string {
    return this.pieces.join("");
  }

  get finish(): string | undefined {
    return this.finishReason;
  }

  /**
   * The chain of thought, assembled from `reasoning_content` deltas.
   *
   * **Separate from {@link content}, and a caller streaming a reasoning model needs both.** Those
   * models put their thinking here and often spend the whole token budget on it, so `content` finishes
   * empty and a UI showing only `content` displays nothing at all -- which looks exactly like a broken
   * SDK. Measured against a live `qwen3-0.6b`: 120 chunks, `content` empty, every delta reasoning.
   *
   * This was missing from the first version of this class while Python, Go, Rust and Swift all had it,
   * and no contract case asserts it -- the corpus pins a stream's content through `expect.content` and
   * has no key for reasoning at all, so five SDKs agreeing was a coincidence rather than a guarantee.
   */
  get reasoning(): string {
    return this.thoughts.join("");
  }

  /**
   * Token counts, reported if the backend sent them and **derived from `timings` otherwise**.
   *
   * llama.cpp-family backends emit no usage chunk when streaming, so the counts can only be inferred
   * from the final chunk's timings — and a derived figure is flagged `estimated` so a caller never
   * mistakes it for a measurement. `prompt_n` excludes the cached prefix while `prompt_tokens`
   * includes it, so `cache_n` is added rather than subtracted.
   */
  get usage(): Usage | undefined {
    if (this.reportedUsage) {
      const details = this.reportedUsage["prompt_tokens_details"];
      const cached =
        typeof details === "object" && details !== null
          ? asInt((details as Record<string, unknown>)["cached_tokens"])
          : undefined;
      return {
        promptTokens: asInt(this.reportedUsage["prompt_tokens"]),
        completionTokens: asInt(this.reportedUsage["completion_tokens"]),
        totalTokens: asInt(this.reportedUsage["total_tokens"]),
        cacheReadTokens: cached,
        estimated: false,
        raw: this.reportedUsage,
      };
    }
    if (!this.timings) return undefined;

    const cached = asInt(this.timings["cache_n"]);
    const prompt = asInt(this.timings["prompt_n"]);
    const completion = asInt(this.timings["predicted_n"]);
    if (prompt === undefined && completion === undefined) return undefined;

    const promptTotal = (prompt ?? 0) + (cached ?? 0);
    return {
      promptTokens: promptTotal,
      completionTokens: completion,
      totalTokens: promptTotal + (completion ?? 0),
      // By presence, not by value: an absent cache_n must stay absent rather than become a measured
      // zero, which would claim the cache was checked and found empty.
      cacheReadTokens: this.timings["cache_n"] === undefined ? undefined : cached,
      estimated: true,
      raw: this.timings,
    };
  }

  /** Tool calls reassembled across chunks, in the order the model opened them. */
  get toolCalls(): readonly StreamedToolCall[] | undefined {
    if (this.calls.size === 0) return undefined;
    return [...this.calls.entries()]
      .sort(([a], [b]) => a - b)
      .map(([, call]) => ({
        ...(call.id === undefined ? {} : { id: call.id }),
        function: { name: call.name, arguments: call.arguments },
      }));
  }

  /** Drains the stream and returns what it assembled. */
  async finalMessage(): Promise<{
    content: string;
    reasoning: string;
    usage: Usage | undefined;
    toolCalls: readonly StreamedToolCall[] | undefined;
  }> {
    for await (const _ of this) {
      // Drained for its side effects; the accumulators are what the caller wants.
    }
    return {
      content: this.content,
      reasoning: this.reasoning,
      usage: this.usage,
      toolCalls: this.toolCalls,
    };
  }

  async *[Symbol.asyncIterator](): AsyncGenerator<Chunk> {
    if (this.consumed) {
      throw new StreamInterruptedError(
        "This stream has already been consumed. A stream is a one-shot sequence over a socket; " +
          "hold the assembled content rather than iterating twice.",
        this.content,
        this.meta,
      );
    }
    this.consumed = true;

    for await (const event of events(this.body)) {
      const data = dataOf(event);
      if (data === undefined) continue;
      if (data.trim() === DONE) return;

      let decoded: unknown;
      try {
        decoded = JSON.parse(data);
      } catch {
        // Not JSON, and not the sentinel. Skipped rather than fatal: the gateway sends comments as
        // keep-alives and a proxy may inject its own, and neither is a failed generation.
        continue;
      }
      if (typeof decoded !== "object" || decoded === null) continue;
      const chunk = decoded as Record<string, unknown>;

      // An in-band failure, detected by the PRESENCE of a top-level `error` key rather than by
      // matching its text. Today exactly one site emits exactly one message, and the platform has
      // said that is implementation detail rather than contract.
      if ("error" in chunk) {
        throw new StreamInterruptedError(
          `The stream was interrupted after ${this.content.length} characters: ` +
            `${JSON.stringify(chunk["error"])}`,
          this.content,
          this.meta,
        );
      }

      this.absorb(chunk);
      yield { raw: chunk, delta: deltaOf(chunk) };
    }
    // Ended without [DONE]. Not raised: the content assembled so far is real, the gateway closes
    // cleanly on some paths, and a caller who needs certainty has `finish`.
  }

  private absorb(chunk: Record<string, unknown>): void {
    const usage = chunk["usage"];
    if (typeof usage === "object" && usage !== null)
      this.reportedUsage = usage as Record<string, unknown>;
    const timings = chunk["timings"];
    if (typeof timings === "object" && timings !== null)
      this.timings = timings as Record<string, unknown>;

    const piece = deltaOf(chunk);
    if (piece) this.pieces.push(piece);
    const thought = reasoningOf(chunk);
    if (thought) this.thoughts.push(thought);

    const choices = chunk["choices"];
    if (!Array.isArray(choices)) return;
    for (const entry of choices) {
      if (typeof entry !== "object" || entry === null) continue;
      const choice = entry as Record<string, unknown>;
      if (typeof choice["finish_reason"] === "string") this.finishReason = choice["finish_reason"];

      const delta = choice["delta"];
      if (typeof delta !== "object" || delta === null) continue;
      const calls = (delta as Record<string, unknown>)["tool_calls"];
      if (!Array.isArray(calls)) continue;

      for (const rawCall of calls) {
        if (typeof rawCall !== "object" || rawCall === null) continue;
        const call = rawCall as Record<string, unknown>;
        // Keyed by `index`, which is what makes two interleaved calls reassemble correctly. Appending
        // to whichever was last would concatenate their arguments into one unparseable string.
        const index = asInt(call["index"]) ?? 0;
        const existing = this.calls.get(index) ?? { name: "", arguments: "" };
        if (typeof call["id"] === "string") existing.id = call["id"];
        const fn = call["function"];
        if (typeof fn === "object" && fn !== null) {
          const func = fn as Record<string, unknown>;
          if (typeof func["name"] === "string") existing.name += func["name"];
          if (typeof func["arguments"] === "string") existing.arguments += func["arguments"];
        }
        this.calls.set(index, existing);
      }
    }
  }
}

/** The reasoning a chunk added, under either of the two names backends use. */
function reasoningOf(chunk: Record<string, unknown>): string {
  const choices = chunk["choices"];
  if (!Array.isArray(choices) || choices.length === 0) return "";
  const first = choices[0];
  if (typeof first !== "object" || first === null) return "";
  const delta = (first as Record<string, unknown>)["delta"];
  if (typeof delta !== "object" || delta === null) return "";
  const fields = delta as Record<string, unknown>;
  for (const name of ["reasoning_content", "reasoning"]) {
    if (typeof fields[name] === "string") return fields[name] as string;
  }
  return "";
}

/** The text a chunk added, reading both the streaming and the non-streaming shapes. */
function deltaOf(chunk: Record<string, unknown>): string {
  const choices = chunk["choices"];
  if (!Array.isArray(choices) || choices.length === 0) return "";
  const first = choices[0];
  if (typeof first !== "object" || first === null) return "";
  const choice = first as Record<string, unknown>;

  const delta = choice["delta"];
  if (typeof delta === "object" && delta !== null) {
    const content = (delta as Record<string, unknown>)["content"];
    if (typeof content === "string") return content;
  }
  // Some backends send a complete `message` on the final chunk instead of a delta.
  const message = choice["message"];
  if (typeof message === "object" && message !== null) {
    const content = (message as Record<string, unknown>)["content"];
    if (typeof content === "string") return content;
  }
  return "";
}
