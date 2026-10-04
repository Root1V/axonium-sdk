import { ConfigurationError } from "./errors.ts";
import { USER_AGENT } from "./version.ts";

/**
 * How long to wait, in milliseconds.
 *
 * The non-streaming default is **600s and that is deliberate, not an oversight**. The gateway allows
 * its backends 600s, and some image backends legitimately take 2-8 minutes; a client timeout shorter
 * than the server's, combined with a retry, is a known failure mode --- the backend keeps computing
 * after the client gives up and the retry queues a second expensive generation on top of the first.
 *
 * An edge runtime cannot wait that long. That is a real conflict rather than a tuning question, and
 * it is why these are per-call overridable: a deployment that cannot afford 600s should say so
 * explicitly and know that it is giving up on image generation, instead of discovering it as a
 * truncated request.
 *
 * `stream` is above the gateway's own 120s read timeout against the backend, so this SDK does not
 * give up before the gateway would.
 */
export interface Timeouts {
  readonly connect: number;
  readonly request: number;
  readonly stream: number;
}

export const DEFAULT_TIMEOUTS: Timeouts = {
  connect: 10_000,
  request: 600_000,
  stream: 180_000,
};

/**
 * A host that supplies tokens, so this SDK never sees a credential.
 *
 * The seam that keeps this package's public API stable: a token is the whole surface, so nothing
 * about *how* one was obtained --- a vault, a per-tenant service, a human --- can become a breaking
 * change here later.
 */
export interface TokenProvider {
  /** A token to use now. Called often; cache on your side. */
  token(): Promise<string>;
  /** Called once after the gateway rejects `rejected` with a 401. Must not return the same value. */
  refresh(rejected?: string): Promise<string>;
}

/** What a caller passes in. Everything optional; the environment fills the gaps. */
export interface AxoniumOptions {
  gatewayBaseURL?: string;
  clientId?: string;
  clientSecret?: string;
  scope?: string;
  timeouts?: Partial<Timeouts>;
  tokenProvider?: TokenProvider;
  /**
   * The `fetch` to use. Defaults to the global one.
   *
   * Injected rather than monkey-patched so tests replay the contract corpus against a function
   * instead of a mocking library --- which is also why this package has no dev dependency for HTTP
   * mocking, and why what the tests exercise is the real transport rather than a seam around it.
   */
  fetch?: typeof globalThis.fetch;
  /**
   * Allows a `clientSecret` where this SDK cannot establish that it is running on a server.
   *
   * Off by default. The rule it enforces is **not** "never in a browser" --- the platform's rule is
   * about *whose* credential it is: an integrator's must never reach a machine its users control, and
   * an end client's own may live on their own device. A bundle cannot tell those apart, so the
   * default refuses and this flag is how a caller states which case theirs is.
   */
  allowInsecureCredential?: boolean;
}

/** The resolved configuration, with every default applied. */
export interface ResolvedConfig {
  readonly gatewayBaseURL: string;
  readonly clientId: string;
  readonly clientSecret: string;
  readonly scope: string;
  readonly timeouts: Timeouts;
  readonly tokenProvider: TokenProvider | undefined;
  readonly fetch: typeof globalThis.fetch;
  readonly userAgent: string;
}

const ENV_KEYS = {
  gatewayBaseURL: "AXONIUM_GATEWAY_BASE_URL",
  clientId: "AXONIUM_CLIENT_ID",
  clientSecret: "AXONIUM_CLIENT_SECRET",
  scope: "AXONIUM_SCOPE",
} as const;

/**
 * Reads one variable from whatever this runtime calls the environment.
 *
 * Node, Bun and Deno-with-`--unstable-node-globals` expose `process.env`; Deno exposes `Deno.env`;
 * an edge runtime may expose neither. Absent is not an error here --- it becomes one in
 * {@link resolveConfig}, which can name the setting *and* its variable.
 */
function readEnv(key: string): string | undefined {
  const proc = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process;
  if (proc?.env && typeof proc.env[key] === "string") return proc.env[key];
  const deno = (globalThis as { Deno?: { env?: { get(k: string): string | undefined } } }).Deno;
  try {
    const value = deno?.env?.get(key);
    if (typeof value === "string") return value;
  } catch {
    // Deno throws without --allow-env rather than returning undefined. A permission the host chose
    // not to grant is not a configuration error, so it reads as "unset" and the constructor
    // argument wins.
  }
  return undefined;
}

/**
 * True when this SDK can establish it is **not** in a browser.
 *
 * Deliberately not `typeof window !== "undefined"`: jsdom defines `window`, so that test fires in
 * anyone's vitest or jest suite, and a guard with false positives in CI is a guard somebody
 * disables. This looks for positive evidence of a server runtime instead, so an environment nobody
 * anticipated is treated as unknown rather than as safe.
 */
function looksLikeServer(): boolean {
  const g = globalThis as {
    process?: { versions?: { node?: string } };
    Deno?: unknown;
    Bun?: unknown;
    WorkerGlobalScope?: unknown;
    EdgeRuntime?: unknown;
  };
  return Boolean(
    g.process?.versions?.node ||
    g.Deno ||
    g.Bun ||
    g.EdgeRuntime ||
    // A service worker or Cloudflare Worker: no DOM, and the credential is not in a page.
    (typeof g.WorkerGlobalScope !== "undefined" &&
      typeof (globalThis as { document?: unknown }).document === "undefined"),
  );
}

/** Applies the precedence --- argument, then environment, then an error naming both. */
export function resolveConfig(options: AxoniumOptions = {}): ResolvedConfig {
  const pick = (key: keyof typeof ENV_KEYS): string =>
    (options[key] ?? readEnv(ENV_KEYS[key]) ?? "").trim();

  const gatewayBaseURL = pick("gatewayBaseURL").replace(/\/+$/, "");
  const clientId = pick("clientId");
  const clientSecret = pick("clientSecret");
  const scope = pick("scope");
  const tokenProvider = options.tokenProvider;

  if (!gatewayBaseURL) {
    throw new ConfigurationError(
      `Missing gatewayBaseURL. Pass it to the constructor or set ${ENV_KEYS.gatewayBaseURL}. ` +
        `There is no default: a wrong gateway is worse than a missing one.`,
    );
  }
  let parsed: URL;
  try {
    parsed = new URL(gatewayBaseURL);
  } catch {
    throw new ConfigurationError(
      `gatewayBaseURL is not a URL: ${JSON.stringify(gatewayBaseURL)}. It needs a scheme, e.g. https://gateway.example.`,
    );
  }
  if (
    parsed.protocol !== "https:" &&
    parsed.hostname !== "localhost" &&
    parsed.hostname !== "127.0.0.1"
  ) {
    throw new ConfigurationError(
      `gatewayBaseURL is ${parsed.protocol}//, which sends the credential in clear. Use https, ` +
        `or localhost for a development gateway.`,
    );
  }

  // Both modes named at once is a contradiction rather than a preference, so it is refused instead
  // of one silently winning -- a caller who passes both has a belief about which, and would be wrong
  // half the time.
  if (tokenProvider && (clientId || clientSecret)) {
    throw new ConfigurationError(
      `Both a tokenProvider and a clientId/clientSecret were supplied, and they are different modes. ` +
        `Pass the provider alone to keep credentials out of this SDK, or the credentials alone to let ` +
        `it fetch tokens.`,
    );
  }

  if (!tokenProvider && (!clientId || !clientSecret)) {
    const missing = [!clientId && "clientId", !clientSecret && "clientSecret"].filter(Boolean);
    throw new ConfigurationError(
      `Missing ${missing.join(" and ")}. Set ${ENV_KEYS.clientId} and ${ENV_KEYS.clientSecret}, pass ` +
        `them to the constructor, or pass a tokenProvider to let a host supply tokens instead.`,
    );
  }

  if (clientSecret && !options.allowInsecureCredential && !looksLikeServer()) {
    throw new ConfigurationError(
      `A clientSecret was supplied and this does not look like a server runtime. A credential is an ` +
        `account: model grants and every usage row are keyed to its clientId, so a copy anywhere its ` +
        `owner does not control is a copy of the thing that gets billed. Use a tokenProvider backed ` +
        `by a service you run. If this credential belongs to the person using the application, pass ` +
        `allowInsecureCredential: true to say so.`,
    );
  }

  const fetchImpl = options.fetch ?? globalThis.fetch;
  if (typeof fetchImpl !== "function") {
    throw new ConfigurationError(
      `No fetch available. This SDK uses the platform's fetch and adds no HTTP dependency; on Node ` +
        `below 18 there is none, so pass one as options.fetch.`,
    );
  }

  return {
    gatewayBaseURL,
    clientId,
    clientSecret,
    scope,
    timeouts: { ...DEFAULT_TIMEOUTS, ...options.timeouts },
    tokenProvider,
    fetch: fetchImpl,
    userAgent: USER_AGENT,
  };
}
