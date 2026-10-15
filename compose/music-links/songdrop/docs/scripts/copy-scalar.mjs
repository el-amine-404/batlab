// The API reference (/docs/api/, public/api/index.html) runs Scalar from the
// docs' own files, not a CDN: the page works without internet.
import { copyFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const docs = join(dirname(fileURLToPath(import.meta.url)), "..");
const scalar = join(docs, "node_modules/@scalar/api-reference/dist/browser/standalone.js");
const target = join(docs, ".vitepress/dist/api/scalar.js");
copyFileSync(scalar, target);
console.log(`api: Scalar → ${target}`);
