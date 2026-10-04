/**
 * When to try again, and how long to wait.
 *
 * **The default retries only what the platform says did not reach a model.** This API has no
 * server-side deduplication of its own, so a retried generation is a *new billable one* rather than a
 * replay — which makes an over-eager policy expensive rather than merely noisy. An idempotency key is
 * what turns a repeat into a replay, and the request methods take one.
 */
export interface RetryPolicy {
  /** Total attempts, not retries. 3 means the original and two more. */
  readonly maxAttempts: number;
  /** Base delay in ms when the platform supplies no `Retry-After`. Doubles per attempt. */
  readonly initialBackoff: number;
  /**
   * The longest this SDK will block inside one call, in ms.
   *
   * Also caps a server-supplied `Retry-After`: a longer wait is surfaced to the caller rather than
   * slept through, because a library that silently blocks for ten minutes has made a scheduling
   * decision that belongs to the application.
   */
  readonly maxBackoff: number;
  /** Spread retries so concurrent callers recovering from one outage do not resynchronise. */
  readonly jitter: boolean;
}

/**
 * Three attempts, 1s base, 60s cap, jitter on.
 *
 * These exact numbers are shared by every SDK in this family, and the contract corpus depends on
 * them: a case asserting `expect.requests` counts attempts whose denominator is this policy. A
 * runner that disables retries to keep its error cases fast sends one request and fails the case on
 * its own configuration rather than on the SDK.
 */
export const DEFAULT_RETRY: RetryPolicy = {
  maxAttempts: 3,
  initialBackoff: 1_000,
  maxBackoff: 60_000,
  jitter: true,
};

/** Never retry. For a caller who would rather see the first failure. */
export const NO_RETRY: RetryPolicy = { ...DEFAULT_RETRY, maxAttempts: 1 };

/**
 * How long to wait before attempt `attempt` (1-based), or `undefined` to stop.
 *
 * `retryAfter` is honoured **verbatim** rather than being replaced by the backoff: the gateway
 * computes it from the real reset time or the breaker's expected recovery, which is better
 * information than any local guess. It is still capped — a wait longer than the cap is returned as
 * `undefined` so the caller decides, instead of this SDK blocking for it.
 */
export function delayFor(
  policy: RetryPolicy,
  attempt: number,
  retryAfterMs: number | undefined,
): number | undefined {
  if (attempt >= policy.maxAttempts) return undefined;

  if (retryAfterMs !== undefined) {
    return retryAfterMs > policy.maxBackoff ? undefined : retryAfterMs;
  }

  const exponential = policy.initialBackoff * 2 ** (attempt - 1);
  const capped = Math.min(exponential, policy.maxBackoff);
  // Full jitter: anywhere in [0, capped). Equal-jitter would leave half the wait synchronised, which
  // is the half that matters when a hundred clients come back from the same outage.
  return policy.jitter ? Math.random() * capped : capped;
}

/**
 * Reads `Retry-After`, which RFC 9110 allows as either seconds or an HTTP date.
 *
 * Returns milliseconds. A negative or absurd value reads as absent rather than as zero: a date in the
 * past means the clocks disagree, and treating that as "retry immediately" turns a disagreement into
 * a hot loop.
 */
export function retryAfterMs(headers: Headers, now: number = Date.now()): number | undefined {
  const raw = headers.get("Retry-After");
  if (!raw) return undefined;

  const seconds = Number(raw.trim());
  if (Number.isFinite(seconds)) return seconds > 0 ? seconds * 1000 : undefined;

  const date = Date.parse(raw);
  if (Number.isNaN(date)) return undefined;
  const wait = date - now;
  return wait > 0 ? wait : undefined;
}

/**
 * Per-model cooldowns, honouring a `Retry-After` the gateway already supplied.
 *
 * This is the whole of this SDK's circuit breaking, and deliberately so: the gateway runs its own
 * breaker per backend and reports `503 backend-unavailable` with a wait computed from real recovery
 * time. A second breaker on top would open on signals the server has already counted. What the
 * gateway cannot report is that the gateway itself is unreachable, and the remaining gap is this —
 * fail fast locally until a wait the server asked for has elapsed.
 *
 * **Keyed by model, not by gateway.** `backend-unavailable` is a per-model condition: every replica
 * of *that* model is out while other models on the same gateway keep serving. Cooling the whole
 * gateway would refuse requests it would have answered.
 */
export class CooldownRegistry {
  private readonly until = new Map<string, number>();

  remaining(key: string, now: number = performance.now()): number {
    const deadline = this.until.get(key);
    if (deadline === undefined) return 0;
    if (deadline <= now) {
      this.until.delete(key);
      return 0;
    }
    return deadline - now;
  }

  record(key: string, waitMs: number, now: number = performance.now()): void {
    if (!(waitMs > 0)) return;
    const deadline = now + waitMs;
    // Never shorten an existing cooldown: two concurrent failures should not let the second one's
    // smaller wait override the first one's larger.
    const existing = this.until.get(key);
    if (existing === undefined || deadline > existing) this.until.set(key, deadline);
  }

  clear(key: string): void {
    this.until.delete(key);
  }
}
