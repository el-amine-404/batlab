import { copyFile, mkdir } from "node:fs/promises";
await mkdir("dist/api-docs", { recursive: true });
await copyFile("node_modules/@scalar/api-reference/dist/browser/standalone.js", "dist/api-docs/scalar.js");
