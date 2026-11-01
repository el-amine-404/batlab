import { copyFile, mkdir, readdir } from "node:fs/promises";
import { join } from "node:path";

const srcDir = "node_modules/onnxruntime-web/dist";
const dstDistDir = "dist/vendor/onnx";
const dstPublicDir = "public/vendor/onnx";

await mkdir(dstDistDir, { recursive: true });
await mkdir(dstPublicDir, { recursive: true });

try {
  const files = await readdir(srcDir);
  for (const file of files) {
    if (file.endsWith(".wasm") || file.endsWith(".mjs")) {
      await copyFile(join(srcDir, file), join(dstDistDir, file));
      await copyFile(join(srcDir, file), join(dstPublicDir, file));
    }
  }
  console.log("Successfully copied ONNX Runtime WASM binaries to vendor/onnx");
} catch (err) {
  console.error("Warning: Failed to copy ONNX WASM binaries:", err);
}
