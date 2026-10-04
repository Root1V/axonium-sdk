import { InvalidRequestError, type ResponseMeta } from "./errors.ts";

/**
 * Token accounting for one request.
 *
 * `cacheReadTokens` is a **subset** of `promptTokens`, not a separate bucket: the input counter
 * already includes the cached prefix. That convention is shared across this family because providers
 * report it that way, so an adapter copies instead of subtracting — copying cannot be done wrong,
 * while a forgotten subtraction double-counts the cache without producing any error.
 */
export interface Usage {
  readonly promptTokens: number | undefined;
  readonly completionTokens: number | undefined;
  readonly totalTokens: number | undefined;
  readonly cacheReadTokens: number | undefined;
  /** True when the counts were derived from a backend `timings` object rather than reported. */
  readonly estimated: boolean;
  readonly raw: Record<string, unknown>;
}

/** Everything a response carries beyond its payload. */
export interface Envelope {
  readonly meta: ResponseMeta;
  /** The decoded body as received, so a field this SDK does not model stays reachable. */
  readonly raw: Record<string, unknown>;
}

/** One entry of the catalogue. */
export interface Model {
  readonly id: string;
  readonly modality: string | undefined;
  /**
   * `undefined` for image models, which have no context window at all.
   *
   * A zero would read as "a window of zero" and make a caller checking `promptTokens < contextLength`
   * reject every image request — the platform changed this from `0` to `null` for that reason. For a
   * model served by several instances it is the **smallest** of them, so a request that fits the
   * advertised number fits whichever instance serves it.
   */
  readonly contextLength: number | undefined;
  readonly servedBy: number | undefined;
  readonly family: string | undefined;
  readonly quantization: string | undefined;
  readonly ownedBy: string | undefined;
  /**
   * A versioned identifier for the request body's contract.
   *
   * **This, not {@link modality}, is what identifies the shape to send.** It matters most on the
   * pass-through route, where the body belongs to the engine and two engines serving one modality can
   * want different ones: `sst2-clf` and `von-decide` are both classifiers and their payloads differ.
   */
  readonly payloadSchema: string | undefined;
  readonly raw: Record<string, unknown>;
}

export interface ModelList extends Envelope {
  readonly data: readonly Model[];
  /** Just the ids, which is what most callers want. */
  readonly ids: readonly string[];
  find(id: string): Model | undefined;
}

export interface ChatCompletion extends Envelope {
  readonly model: string | undefined;
  /** The assistant's text, or `undefined` when the model produced only tool calls or only reasoning. */
  readonly content: string | undefined;
  readonly reasoning: string | undefined;
  readonly finishReason: string | undefined;
  readonly toolCalls: readonly ToolCall[] | undefined;
  readonly usage: Usage | undefined;
}

export interface ToolCall {
  readonly id: string | undefined;
  /** `"function"` for everything the gateway forwards today. Modelled rather than assumed. */
  readonly type: string | undefined;
  /**
   * The call as it arrives on the wire.
   *
   * Nested rather than flattened because that is the shape the gateway sends and what every SDK in
   * this family exposes, so one contract case resolves `tool_calls.0.function.name` across all of
   * them. {@link name} and {@link arguments} are shortcuts over it, not a second representation.
   */
  readonly function: { readonly name: string; readonly arguments: string };
  /** The function name, without reaching through {@link function}. */
  readonly name: string;
  /**
   * The arguments as the model produced them: a JSON **string**, not a decoded object.
   *
   * Left as text on purpose. A generation stopped by `max_tokens` leaves this truncated and
   * unparseable, and parsing eagerly would turn that into a failure of the whole response -- losing
   * the text and the correlation ids with it. Decode it with {@link decodedArguments} once the finish
   * reason says the model was done.
   */
  readonly arguments: string;
}

/**
 * Parses a tool call's arguments.
 *
 * Separate from the field rather than parsed eagerly, because the model writes this string and a model
 * can write invalid JSON. Eager parsing would turn that into a failure of the whole response, losing
 * the text and the correlation ids along with it — when what a caller usually wants is to see the
 * malformed arguments and retry.
 */
export function decodedArguments(call: ToolCall): unknown {
  try {
    return JSON.parse(call.arguments);
  } catch (cause) {
    throw new InvalidRequestError(
      `The model produced arguments for ${call.name} that are not valid JSON: ` +
        `${call.arguments.slice(0, 200)}`,
      { cause },
    );
  }
}

export interface Embedding {
  readonly index: number | undefined;
  readonly embedding: readonly number[];
}

export interface EmbeddingList extends Envelope {
  readonly model: string | undefined;
  readonly data: readonly Embedding[];
  readonly usage: Usage | undefined;
}

export interface GeneratedImage {
  readonly b64JSON: string | undefined;
  /** The bytes, decoded from `b64_json`. */
  bytes(): Uint8Array;
}

export interface ImageList extends Envelope {
  readonly outputFormat: string | undefined;
  readonly data: readonly GeneratedImage[];
}

export interface RerankResult {
  /** Points into **the documents you sent**, never into the results. */
  readonly index: number | undefined;
  readonly relevanceScore: number | undefined;
}

export interface RerankList extends Envelope {
  readonly model: string | undefined;
  readonly results: readonly RerankResult[];
  /** The indices of your documents, best first. */
  readonly ranking: readonly number[];
  readonly usage: Usage | undefined;
}

/** The answer from a pass-through `predict` call, undecoded. */
export interface PredictResult extends Envelope {
  /**
   * Whatever the engine returned. **Not necessarily an object** — measured against a deployment:
   *
   * ```text
   * sst2-clf     [{"label":"POSITIVE","score":0.978}]   <- a top-level array
   * von-decide   {"sequence":…,"labels":[…],"scores":[…]}
   * laya-decide  {"model":…,"answers":{…},"routing":{…}}
   * ```
   *
   * so typing this as a record would have failed on the first engine the platform shipped here.
   */
  readonly value: unknown;
}

/** One row of the usage ledger. */
export interface RequestUsage extends Envelope {
  readonly requestId: string | undefined;
  readonly model: string | undefined;
  /** `chat`, `embedding`, `image`, `rerank` or `predict`. Open: the platform adds values. */
  readonly requestKind: string | undefined;
  readonly interrupted: boolean | undefined;
  readonly terminationReason: string | undefined;
  readonly usage: Usage | undefined;
}

/* --- decoding ----------------------------------------------------------------------------- */

const record = (value: unknown): Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};

const str = (source: Record<string, unknown>, key: string): string | undefined =>
  typeof source[key] === "string" ? (source[key] as string) : undefined;

const num = (source: Record<string, unknown>, key: string): number | undefined =>
  typeof source[key] === "number" && Number.isFinite(source[key])
    ? (source[key] as number)
    : undefined;

const bool = (source: Record<string, unknown>, key: string): boolean | undefined =>
  typeof source[key] === "boolean" ? (source[key] as boolean) : undefined;

const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);

/** Reads a usage object, lifting the cached count out of the OpenAI-shaped nested name. */
export function usageFrom(value: unknown): Usage | undefined {
  const raw = record(value);
  if (Object.keys(raw).length === 0) return undefined;
  const details = record(raw["prompt_tokens_details"]);
  return {
    promptTokens: num(raw, "prompt_tokens"),
    completionTokens: num(raw, "completion_tokens"),
    totalTokens: num(raw, "total_tokens"),
    // Lifted rather than left buried: a non-streaming response reports it here, so this is a
    // *measured* figure on the one path where somebody measured it. Leaving it nested would mean
    // telling a caller nobody looked.
    cacheReadTokens: num(details, "cached_tokens"),
    estimated: false,
    raw,
  };
}

export function modelFrom(value: unknown): Model {
  const raw = record(value);
  return {
    id: str(raw, "id") ?? "",
    modality: str(raw, "modality"),
    contextLength: num(raw, "context_length"),
    servedBy: num(raw, "served_by"),
    family: str(raw, "family"),
    quantization: str(raw, "quantization"),
    ownedBy: str(raw, "owned_by"),
    payloadSchema: str(raw, "payload_schema"),
    raw,
  };
}

export function modelListFrom(value: unknown, meta: ResponseMeta): ModelList {
  const raw = record(value);
  const data = list(raw["data"]).map(modelFrom);
  return {
    meta,
    raw,
    data,
    ids: data.map((m) => m.id),
    find(id) {
      return data.find((m) => m.id === id);
    },
  };
}

export function chatCompletionFrom(value: unknown, meta: ResponseMeta): ChatCompletion {
  const raw = record(value);
  const choice = record(list(raw["choices"])[0]);
  const message = record(choice["message"]);

  const calls = list(message["tool_calls"]).map((entry): ToolCall => {
    const call = record(entry);
    const fn = record(call["function"]);
    const name = str(fn, "name") ?? "";
    const args = str(fn, "arguments") ?? "";
    return {
      id: str(call, "id"),
      type: str(call, "type"),
      function: { name, arguments: args },
      name,
      arguments: args,
    };
  });

  return {
    meta,
    raw,
    model: str(raw, "model"),
    content: str(message, "content"),
    // Some backends put the chain of thought here rather than in `content`, and a caller showing
    // `content` alone would display nothing for a reasoning model that spent its budget thinking.
    reasoning: str(message, "reasoning_content") ?? str(message, "reasoning"),
    finishReason: str(choice, "finish_reason"),
    toolCalls: calls.length > 0 ? calls : undefined,
    usage: usageFrom(raw["usage"]),
  };
}

export function embeddingListFrom(value: unknown, meta: ResponseMeta): EmbeddingList {
  const raw = record(value);
  return {
    meta,
    raw,
    model: str(raw, "model"),
    data: list(raw["data"]).map((entry) => {
      const item = record(entry);
      return {
        index: num(item, "index"),
        embedding: list(item["embedding"]).filter((v): v is number => typeof v === "number"),
      };
    }),
    usage: usageFrom(raw["usage"]),
  };
}

export function imageListFrom(value: unknown, meta: ResponseMeta): ImageList {
  const raw = record(value);
  return {
    meta,
    raw,
    outputFormat: str(raw, "output_format"),
    data: list(raw["data"]).map((entry) => {
      const item = record(entry);
      const b64 = str(item, "b64_json");
      return {
        b64JSON: b64,
        bytes(): Uint8Array {
          if (b64 === undefined) {
            throw new InvalidRequestError("This image carried no b64_json to decode.");
          }
          const binary = atob(b64);
          return Uint8Array.from(binary, (c) => c.charCodeAt(0));
        },
      };
    }),
  };
}

export function rerankListFrom(value: unknown, meta: ResponseMeta): RerankList {
  const raw = record(value);
  const results = list(raw["results"]).map((entry) => {
    const item = record(entry);
    return { index: num(item, "index"), relevanceScore: num(item, "relevance_score") };
  });
  return {
    meta,
    raw,
    model: str(raw, "model"),
    results,
    ranking: results.map((r) => r.index).filter((i): i is number => i !== undefined),
    usage: usageFrom(raw["usage"]),
  };
}

export function requestUsageFrom(value: unknown, meta: ResponseMeta): RequestUsage {
  const raw = record(value);
  return {
    meta,
    raw,
    requestId: str(raw, "request_id"),
    model: str(raw, "model"),
    requestKind: str(raw, "request_kind"),
    interrupted: bool(raw, "interrupted"),
    terminationReason: str(raw, "termination_reason"),
    usage: usageFrom(raw["usage"]),
  };
}
