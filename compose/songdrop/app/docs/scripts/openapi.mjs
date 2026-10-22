// The API reference's copy of songdrop's OpenAPI description
// (public/api/openapi.json), from a running songdrop: after a change to the API,
// like the page's types (frontend: npm run gen:api).
// SONGDROP_OPENAPI=http://songdrop.homelab.lan/api/v1/openapi.json npm run openapi
import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const url = process.env.SONGDROP_OPENAPI ?? "http://localhost:8810/api/v1/openapi.json";
const response = await fetch(url);
if (!response.ok) throw new Error(`${url}: ${response.status}`);
const target = join(dirname(fileURLToPath(import.meta.url)), "../public/api/openapi.json");
writeFileSync(target, `${JSON.stringify(await response.json(), null, 2)}\n`);
console.log(`api: ${url} → ${target}`);
