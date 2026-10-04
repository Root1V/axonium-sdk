import { InvalidRequestError, TransportError } from "./errors.ts";
import { resolveConfig, type AxoniumOptions, type ResolvedConfig } from "./config.ts";
import { DEFAULT_RETRY, type RetryPolicy } from "./retry.ts";
import { Transport, type CallOptions } from "./transport.ts";
import { ChatStream } from "./sse.ts";
import {
  chatCompletionFrom,
  embeddingListFrom,
  imageListFrom,
  modelListFrom,
  rerankListFrom,
  requestUsageFrom,
  type ChatCompletion,
  type EmbeddingList,
  type ImageList,
  type ModelList,
  type PredictResult,
  type RerankList,
  type RequestUsage,
} from "./resources.ts";
import type { RateLimitSnapshot, TokenClaims } from "./index.ts";

/** A message in a chat request. `content` is a string or an array of parts. */
export interface Message {
  role: "system" | "user" | "assistant" | "tool";
  content: string | ContentPart[] | null;
  name?: string;
  tool_call_id?: string;
  tool_calls?: unknown[];
}

/** One part of a multi-part message. */
export type ContentPart =
  { type: "text"; text: string } | { type: "image_url"; image_url: { url: string } };

/**
 * An image part from bytes, as a base64 data URI.
 *
 * **There is no helper for a remote URL, and that is the point.** The gateway refuses `http(s)://`
 * as an SSRF mitigation, so an API that accepted one would accept something that always fails. The
 * bytes go on the wire.
 */
export function imageFromBytes(bytes: Uint8Array, mediaType: string): ContentPart {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return { type: "image_url", image_url: { url: `data:${mediaType};base64,${btoa(binary)}` } };
}

/** Refuses a part the gateway will refuse, before the round trip. */
function checkParts(messages: readonly Message[]): void {
  for (const message of messages) {
    if (!Array.isArray(message.content)) continue;
    if (message.content.length === 0) {
      throw new InvalidRequestError(
        "A message's content array is empty. Send a string, or at least one part.",
      );
    }
    for (const part of message.content) {
      if (part.type !== "image_url") continue;
      if (/^https?:\/\//i.test(part.image_url.url)) {
        throw new InvalidRequestError(
          `An image_url part carries ${part.image_url.url.slice(0, 60)}, and the gateway refuses ` +
            `http(s):// as an SSRF mitigation. Use imageFromBytes() to send a data URI instead.`,
        );
      }
    }
  }
}

/** A function the model may call. The gateway forwards `tools` as-is and validates no schemas. */
export interface Tool {
  type: "function";
  function: {
    name: string;
    description?: string;
    /** A JSON Schema object. Typed loosely because the gateway does not validate it either. */
    parameters?: Record<string, unknown>;
  };
}

/** How the model should choose. `"auto"` lets it decide; a named function forces one. */
export type ToolChoice =
  "auto" | "none" | "required" | { type: "function"; function: { name: string } };

/**
 * How the answer should be shaped.
 *
 * With `json_schema` the content comes back as a JSON **string** in the message, not as a nested
 * object — parse it. The engine enforces the schema rather than the prompt asking nicely.
 *
 * **There is no Zod overload here, and that is deliberate.** Apeiron's request asked for one and it
 * contradicts the same request's zero-runtime-dependency requirement, since Zod is one. The seam is
 * {@link jsonSchema}: anything exposing `toJSONSchema()` is accepted without this package knowing what
 * Zod is, so `jsonSchema("name", z.toJSONSchema(schema))` works today and a `zod/v4` import never has
 * to appear here.
 */
export type ResponseFormat =
  | { type: "text" }
  | { type: "json_object" }
  | {
      type: "json_schema";
      json_schema: { name: string; schema: Record<string, unknown>; strict?: boolean };
    };

/** Builds a `json_schema` response format from anything that can produce a JSON Schema. */
export function jsonSchema(
  name: string,
  schema: Record<string, unknown> | { toJSONSchema(): Record<string, unknown> },
  options: { strict?: boolean } = {},
): ResponseFormat {
  const resolved =
    typeof (schema as { toJSONSchema?: unknown }).toJSONSchema === "function"
      ? (schema as { toJSONSchema(): Record<string, unknown> }).toJSONSchema()
      : (schema as Record<string, unknown>);
  return {
    type: "json_schema",
    json_schema: {
      name,
      schema: resolved,
      ...(options.strict === undefined ? {} : { strict: options.strict }),
    },
  };
}

export interface ChatRequest {
  model: string;
  messages: Message[];
  max_tokens?: number;
  temperature?: number;
  top_p?: number;
  stop?: string | string[];
  /** Set by {@link Axonium.chat}`.completions.stream()`; passing it to `create` is a type error. */
  stream?: never;
  tools?: Tool[];
  tool_choice?: ToolChoice;
  response_format?: ResponseFormat;
  /** The gateway silently drops a field it does not support; `requireParameters` makes it say so. */
  [key: string]: unknown;
}

export interface EmbeddingRequest {
  model: string;
  input: string | string[];
  [key: string]: unknown;
}

export interface ImageRequest {
  model: string;
  prompt: string;
  n?: number;
  size?: string;
  [key: string]: unknown;
}

export interface RerankRequest {
  model: string;
  query: string;
  documents: string[];
  top_n?: number;
  [key: string]: unknown;
}

/**
 * The client.
 *
 * Promise-based throughout; there is no synchronous variant and none is coming, because every call
 * here is a network call.
 */
export class Axonium {
  readonly config: ResolvedConfig;
  private readonly transport: Transport;
  private mine: ModelList | undefined;

  constructor(options: AxoniumOptions & { retry?: RetryPolicy } = {}) {
    const { retry, ...rest } = options;
    this.config = resolveConfig(rest);
    this.transport = new Transport(this.config, retry ?? DEFAULT_RETRY);
  }

  /** The budget as of the most recent response, whichever call produced it. */
  get lastRateLimit(): RateLimitSnapshot | undefined {
    return this.transport.lastRateLimit;
  }

  /** The claims of the token currently held, decoded but not verified. */
  get tokenClaims(): TokenClaims | undefined {
    return this.transport.tokens.claims;
  }

  /** The scope the gateway **granted**, which may be narrower than the one requested. */
  get grantedScope(): readonly string[] {
    return this.transport.tokens.grantedScope;
  }

  readonly models = {
    /**
     * The models this token may call.
     *
     * **Not every deployed model, and not since `PRM-167`.** This was the platform's one public route
     * and returned the whole catalogue; it now requires a token and answers exactly what
     * {@link mine} answers. An empty list means this token holds no `model:<id>` grants, **not** that
     * the platform has no models — two different facts that only an operator can tell apart.
     */
    list: async (options: CallOptions = {}): Promise<ModelList> => {
      const { value, meta } = await this.transport.sendJSON("GET", "/v1/models", options);
      return modelListFrom(value, meta);
    },

    /**
     * The models this token is allowed to call, cached for the client's lifetime.
     *
     * Cached because scope grants do not change within one process's run. This is what belongs behind
     * a "test connection" button: it proves the gateway answers, the credential works, and there is
     * something this caller may send. `GET /health` proves that a process replied.
     */
    mine: async (options: CallOptions = {}): Promise<ModelList> => {
      if (this.mine) return this.mine;
      const { value, meta } = await this.transport.sendJSON("GET", "/v1/models/mine", options);
      this.mine = modelListFrom(value, meta);
      return this.mine;
    },
  };

  readonly chat = {
    completions: {
      create: async (request: ChatRequest, options: CallOptions = {}): Promise<ChatCompletion> => {
        checkParts(request.messages);
        const { value, meta } = await this.transport.sendJSON("POST", "/v1/chat/completions", {
          ...options,
          body: request,
          model: request.model,
        });
        return chatCompletionFrom(value, meta);
      },

      /**
       * Opens a stream.
       *
       * A separate method rather than `create({stream: true})`, which keeps the return type honest —
       * no union of a completion and an iterable — makes the `inference:stream` scope requirement
       * explicit, and gives one place to say that **a stream is never retried once it has begun**.
       */
      stream: async (request: ChatRequest, options: CallOptions = {}): Promise<ChatStream> => {
        checkParts(request.messages);
        const { response, meta } = await this.transport.send("POST", "/v1/chat/completions", {
          ...options,
          body: { ...request, stream: true },
          model: request.model,
          streaming: true,
        });
        if (!response.body) {
          throw new TransportError(
            "The gateway accepted the stream and sent no body, which this runtime reports as a " +
              "missing ReadableStream. Nothing can be read from it.",
          );
        }
        return new ChatStream(response.body, meta);
      },
    },
  };

  readonly embeddings = {
    create: async (
      request: EmbeddingRequest,
      options: CallOptions = {},
    ): Promise<EmbeddingList> => {
      const { value, meta } = await this.transport.sendJSON("POST", "/v1/embeddings", {
        ...options,
        body: request,
        model: request.model,
      });
      return embeddingListFrom(value, meta);
    },
  };

  readonly images = {
    generate: async (request: ImageRequest, options: CallOptions = {}): Promise<ImageList> => {
      const { value, meta } = await this.transport.sendJSON("POST", "/v1/images/generations", {
        ...options,
        body: request,
        model: request.model,
      });
      return imageListFrom(value, meta);
    },
  };

  readonly rerank = {
    /**
     * Scores `documents` against `query`, best first.
     *
     * The whole document set is **one** request rather than one per document, which against a 60 RPM
     * budget is the difference between scoring 50 candidates for 1 unit and for 50. Each result's
     * `index` points into the array you sent.
     */
    create: async (request: RerankRequest, options: CallOptions = {}): Promise<RerankList> => {
      if (request.documents.length === 0) {
        throw new InvalidRequestError("documents must contain at least one document.");
      }
      const { value, meta } = await this.transport.sendJSON("POST", "/v1/rerank", {
        ...options,
        body: request,
        model: request.model,
      });
      return rerankListFrom(value, meta);
    },
  };

  readonly predict = {
    /**
     * Sends `body` to `model` unchanged and returns its answer unchanged.
     *
     * The pass-through route, for `classification`, `zero_shot` and `typed_decision`. Deliberately not
     * a `classify(text)` typed per modality: the shape is the engine's, and two engines serving one
     * modality can want different bodies. Dispatch on the catalogue's `payloadSchema`.
     */
    create: async (
      model: string,
      body: Record<string, unknown>,
      options: CallOptions = {},
    ): Promise<PredictResult> => {
      if (!model.trim()) throw new InvalidRequestError("model is required.");
      if (typeof body !== "object" || body === null || Array.isArray(body)) {
        throw new InvalidRequestError(
          "The predict body must be a JSON object. The engine's own contract is named by " +
            "payloadSchema in the catalogue.",
        );
      }
      // The model is a path SEGMENT here rather than a body field, so it is encoded: a slash in an id
      // would otherwise address a different endpoint, and the gateway would answer about a route.
      const path = `/v1/models/${encodeURIComponent(model)}/predict`;
      const { value, meta } = await this.transport.sendJSON("POST", path, {
        ...options,
        body,
        model,
      });
      return {
        value,
        meta,
        raw:
          typeof value === "object" && value !== null && !Array.isArray(value)
            ? (value as Record<string, unknown>)
            : {},
      };
    },
  };

  readonly usage = {
    /**
     * One request's usage row.
     *
     * A `404` here has a third cause beyond "no such id" and it is the common one: **the id belongs to
     * a replay**, which reached no model and has no row. `meta.idempotentReplayOf` on the original
     * response is the id that *was* charged.
     */
    get: async (requestId: string, options: CallOptions = {}): Promise<RequestUsage> => {
      if (!requestId.trim()) throw new InvalidRequestError("requestId is required.");
      const { value, meta } = await this.transport.sendJSON(
        "GET",
        `/v1/usage/${encodeURIComponent(requestId)}`,
        options,
      );
      return requestUsageFrom(value, meta);
    },
  };
}
