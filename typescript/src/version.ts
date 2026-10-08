/**
 * This package's version, as it appears to the gateway.
 *
 * A literal, because reading `package.json` at runtime does not work in every target: a bundled edge
 * deployment has no package.json on disk, and `import … with { type: "json" }` is not portable across
 * Node, Deno, Bun and a bundler's CJS output. The honest cost is that it can drift from the manifest,
 * so a test asserts the two agree --- the Swift SDK shipped `0.1.1` announcing itself as `0.1.0`, and
 * the consumer found it by reading the tag.
 */
export const VERSION = "0.2.4";

/** The `User-Agent` this SDK sends. */
export const USER_AGENT = `axonium-ts/${VERSION}`;
