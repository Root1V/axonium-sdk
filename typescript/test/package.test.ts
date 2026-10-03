import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";

import { VERSION, USER_AGENT } from "../src/index.ts";

const root = fileURLToPath(new URL("..", import.meta.url));
const manifest = JSON.parse(readFileSync(join(root, "package.json"), "utf8")) as {
  version: string;
  dependencies?: Record<string, string>;
  peerDependencies?: Record<string, string>;
  devDependencies?: Record<string, string>;
  files?: string[];
  exports?: unknown;
};

test("the version constant agrees with package.json", () => {
  // Two places, one truth, and nothing would otherwise notice: the Swift SDK published 0.1.1
  // announcing itself as 0.1.0, and the consumer found it by reading the git tag. The constant
  // cannot be derived here -- a bundled edge deployment has no package.json on disk -- so the
  // duplication is unavoidable and only a check makes it safe.
  assert.equal(VERSION, manifest.version, "src/version.ts and package.json disagree");
  assert.equal(USER_AGENT, `axonium-ts/${manifest.version}`);
});

test("the package has no runtime dependencies", () => {
  // The headline promise of this package, asserted rather than intended. A transitive dependency is
  // how a 50 KB budget becomes 400 KB and how an edge deployment stops building, and both are
  // discovered by the consumer rather than here.
  assert.deepEqual(manifest.dependencies ?? {}, {}, "a runtime dependency appeared");
  assert.deepEqual(manifest.peerDependencies ?? {}, {}, "a peer dependency appeared");
});

test("nothing in src imports a node: module", () => {
  // The package must run on Deno, Bun, Cloudflare and Vercel edge, none of which have the whole
  // node: surface. The test suite and the build script use node: freely; src must not. A
  // `node:crypto` import for a request id would pass every test here and fail only on deployment.
  const offenders: string[] = [];
  const walk = (dir: string): void => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(path);
      } else if (entry.name.endsWith(".ts")) {
        const source = readFileSync(path, "utf8");
        for (const match of source.matchAll(/from\s+"(node:[^"]+)"/g)) {
          offenders.push(`${entry.name}: ${match[1]}`);
        }
      }
    }
  };
  walk(join(root, "src"));
  assert.deepEqual(offenders, [], `src imports node: modules: ${offenders.join(", ")}`);
});

test("the published file list and exports map are declared", () => {
  // `files` is what npm actually ships; without it the whole working tree goes up, tests included.
  assert.ok(manifest.files?.includes("dist"), "dist is not in files");
  assert.ok(manifest.exports, "no exports map, so the ESM/CJS split is not expressed");
});

test("the dev dependencies do not include an HTTP mocking library", () => {
  // Not tidiness: the transport takes a `fetch`, so the corpus replays against a function rather
  // than against a patched global. A mocking library here would mean something started intercepting
  // instead of being injected, and what the tests exercise would stop being the real transport.
  const dev = Object.keys(manifest.devDependencies ?? {});
  const mockers = dev.filter((name) => /nock|msw|fetch-mock|undici-mock|sinon/i.test(name));
  assert.deepEqual(mockers, [], `HTTP mocking library in devDependencies: ${mockers.join(", ")}`);
});
