// The API reference (/api/docs) runs Scalar from the page's own files, not a
// CDN: it works without internet.
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const scalar = join(root, "node_modules/@scalar/api-reference/dist/browser/standalone.js");
mkdirSync(join(root, "dist/api-docs"), { recursive: true });
copyFileSync(scalar, join(root, "dist/api-docs/scalar.js"));
console.log("api docs: Scalar → dist/api-docs/scalar.js");
