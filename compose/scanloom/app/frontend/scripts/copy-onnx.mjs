// Only what the scanner's worker loads: the threaded SIMD WebAssembly build.
import { copyFile, mkdir } from "node:fs/promises";
await mkdir("dist/vendor/onnx", { recursive: true });
for (const file of ["ort-wasm-simd-threaded.wasm", "ort-wasm-simd-threaded.mjs"]) {
  await copyFile(`node_modules/onnxruntime-web/dist/${file}`, `dist/vendor/onnx/${file}`);
}
