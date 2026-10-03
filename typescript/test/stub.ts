/**
 * A `fetch` that answers from a script, and records what it was asked.
 *
 * This is the whole test harness. There is no mocking library because `options.fetch` exists: a
 * function is handed in, so what the tests exercise is the real transport rather than a seam around
 * it, and nothing has to be restored afterwards.
 */
export interface Reply {
  status?: number;
  headers?: Record<string, string>;
  /** A string, or a function of how many requests that path has already had. */
  body?: string | ((index: number) => string);
  /** Thrown instead of answering, for the transport-failure paths. */
  throws?: Error;
  /** Never settles until the signal aborts, for the timeout paths. */
  hang?: boolean;
}

export interface Recorded {
  url: string;
  method: string;
  headers: Headers;
  body: string | undefined;
}

export class Stub {
  readonly requests: Recorded[] = [];
  private readonly replies = new Map<string, Reply[]>();

  /**
   * Queues replies for a path.
   *
   * The **last reply repeats** rather than running out. A caller that retries once too often should
   * fail on the request count, which says what it did, rather than on an exhausted queue, which says
   * only that the script was too short.
   */
  on(path: string, ...replies: Reply[]): this {
    this.replies.set(path, replies);
    return this;
  }

  /**
   * The token endpoint, which almost every test needs and no test is about.
   *
   * Issues a **different** token per request, numbered, because a real auth-service does. The first
   * version of this returned one constant string, which made "did the refresh actually replace the
   * token" unanswerable -- the assertion compared two identical values and passed or failed for
   * reasons that had nothing to do with the code.
   */
  token(expiresIn = 3600, scope = "inference:read inference:stream"): this {
    return this.on("/oauth2/token", {
      status: 200,
      headers: { "Content-Type": "application/json" },
      body: (index) =>
        JSON.stringify({
          access_token: `test-token-${index + 1}`,
          token_type: "bearer",
          expires_in: expiresIn,
          scope,
        }),
    });
  }

  /** How many requests reached a path. */
  countFor(path: string): number {
    return this.requests.filter((r) => new URL(r.url).pathname === path).length;
  }

  lastFor(path: string): Recorded | undefined {
    return this.requests.filter((r) => new URL(r.url).pathname === path).at(-1);
  }

  get fetch(): typeof globalThis.fetch {
    return async (input: Request | URL | string, init?: RequestInit): Promise<Response> => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const path = new URL(url).pathname;
      const headers = new Headers(init?.headers ?? {});
      const body =
        typeof init?.body === "string"
          ? init.body
          : init?.body instanceof URLSearchParams
            ? init.body.toString()
            : undefined;

      const index = this.requests.filter((r) => new URL(r.url).pathname === path).length;
      this.requests.push({ url, method: init?.method ?? "GET", headers, body });

      const queue = this.replies.get(path);
      if (!queue || queue.length === 0) {
        return new Response(JSON.stringify({ detail: `no stub for ${path}` }), {
          status: 501,
          headers: { "Content-Type": "application/json" },
        });
      }
      const reply = queue[Math.min(index, queue.length - 1)] as Reply;

      if (reply.throws) throw reply.throws;
      if (reply.hang) {
        return new Promise<Response>((_resolve, reject) => {
          const signal = init?.signal;
          if (!signal) return;
          const fail = (): void => {
            const error = new Error("aborted");
            error.name = signal.reason instanceof Error ? signal.reason.name : "AbortError";
            reject(error);
          };
          if (signal.aborted) fail();
          else signal.addEventListener("abort", fail, { once: true });
        });
      }

      const replyBody = typeof reply.body === "function" ? reply.body(index) : (reply.body ?? "{}");
      return new Response(replyBody, {
        status: reply.status ?? 200,
        headers: { "Content-Type": "application/json", ...reply.headers },
      });
    };
  }
}

/** A problem+json body, so a test states the suffix rather than the whole envelope. */
export function problemBody(suffix: string, overrides: Record<string, unknown> = {}): string {
  return JSON.stringify({
    type: `https://prometheus.internal/errors/${suffix}`,
    title: suffix,
    status: 400,
    detail: `a ${suffix}`,
    instance: "/v1/chat/completions",
    ...overrides,
  });
}

/**
 * A JWT with the given payload and an unverified signature, for the claim-reading paths.
 *
 * The payload is UTF-8 encoded **before** base64, which is what an issuer does and what the first
 * version of this helper got wrong: `btoa(JSON.stringify(...))` encodes latin1, so a claim containing
 * "ó" became one byte instead of two. The decoder read it as mangled and the test blamed the decoder.
 * Had the decoder been "fixed" to match, it would have broken every real token with a non-ASCII claim.
 */
export function jwt(payload: Record<string, unknown>): string {
  const encode = (value: unknown): string => {
    const bytes = new TextEncoder().encode(JSON.stringify(value));
    let binary = "";
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  };
  return `${encode({ alg: "HS256", typ: "JWT" })}.${encode(payload)}.not-a-signature`;
}
