// Renames the CommonJS build's .js to .cjs.
//
// Needed because package.json declares "type": "module", which makes every .js in this package ESM
// regardless of how it was compiled. A CJS bundle sitting at dist/cjs/index.js would be loaded as
// ESM by Node and fail on its `exports` assignment -- and only for the `require` half of the
// package, which is exactly the half an ESM-only test suite never exercises.
//
// The alternative is a second package.json inside dist/cjs saying "type": "commonjs". That works
// too, and this was chosen because an explicit extension is visible in a stack trace while a
// nested manifest is not.
import { readdir, rename, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";

const dir = new URL("../dist/cjs/", import.meta.url);

const entries = await readdir(dir, { recursive: true, withFileTypes: true });
for (const entry of entries) {
  if (!entry.isFile() || !entry.name.endsWith(".js")) continue;
  const from = join(entry.parentPath ?? entry.path, entry.name);
  const to = from.replace(/\.js$/, ".cjs");
  // Rewrite the require() targets too, or index.cjs asks for ./errors.js and finds nothing.
  const source = await readFile(from, "utf8");
  await writeFile(from, source.replace(/(require\("\.[^"]*?)\.js"\)/g, '$1.cjs")'));
  await rename(from, to);
}
console.log("cjs: renamed .js to .cjs and rewrote relative require() targets");
