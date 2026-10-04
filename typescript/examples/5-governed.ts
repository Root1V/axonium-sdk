/**
 * Governed mode: a host supplies tokens and this SDK never sees a credential.
 *
 *   node 5-governed.ts
 *
 * This is the shape for multi-tenant service: one client per tenant, each with its own
 * `client_id` — which is the principal that model grants and every usage row are keyed to, so this is
 * also what makes per-tenant quotas and per-tenant billing work.
 *
 * `TokenProvider` is the whole seam. A token is the entire surface this package needs, so nothing about
 * *how* one was obtained — a vault, an internal service, a human — can become a breaking change here.
 */
import { Axonium, type TokenProvider } from "axonium";

/** Stands in for your own credential service. It, not this process, holds the secrets. */
const vault = {
  async mint(tenant: string): Promise<{ token: string; expiresInSeconds: number }> {
    const response = await fetch(`${process.env["VAULT_URL"] ?? "http://localhost:8080"}/token`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tenant }),
    });
    if (!response.ok) throw new Error(`el vault respondió ${response.status} para ${tenant}`);
    return (await response.json()) as { token: string; expiresInSeconds: number };
  },
};

function providerFor(tenant: string): TokenProvider {
  let cached: { token: string; expiresAt: number } | undefined;
  let inFlight: Promise<string> | undefined;

  const mint = async (): Promise<string> => {
    const { token, expiresInSeconds } = await vault.mint(tenant);
    // Refreshed at 80% of the life rather than on expiry, so a request never races the deadline.
    cached = { token, expiresAt: Date.now() + expiresInSeconds * 1000 * 0.8 };
    return token;
  };

  return {
    async token() {
      if (cached && cached.expiresAt > Date.now()) return cached.token;
      // Concurrent callers share one mint. Without this, ten simultaneous requests on a cold provider
      // make ten calls to the vault and nine are wasted.
      inFlight ??= mint().finally(() => {
        inFlight = undefined;
      });
      return inFlight;
    },

    async refresh(rejected) {
      // Called once after the gateway rejects a token with a 401. Returning the same value is refused
      // by the SDK rather than retried, because retrying would fail identically and hide the real
      // fault — a provider that is not refreshing.
      if (cached?.token === rejected) cached = undefined;
      inFlight ??= mint().finally(() => {
        inFlight = undefined;
      });
      return inFlight;
    },
  };
}

const api = new Axonium({
  gatewayBaseURL: process.env["AXONIUM_GATEWAY_BASE_URL"] ?? "https://gateway.example",
  tokenProvider: providerFor("tenant-42"),
  // No clientId or clientSecret, and passing both modes at once is refused rather than one silently
  // winning: a caller who passes both has a belief about which, and would be wrong half the time.
});

const catalogue = await api.models.mine();
console.log(
  `tenant-42 puede llamar a ${catalogue.data.length} modelos: ${catalogue.ids.join(", ")}`,
);

// The GRANTED scope, read back off the token rather than assumed from what was asked for.
console.log(`scope concedido: ${api.grantedScope.join(" ")}`);
// `sub`, which IS the client_id -- the thing grants and usage rows are keyed to.
console.log(`principal: ${api.tokenClaims?.subject}`);
