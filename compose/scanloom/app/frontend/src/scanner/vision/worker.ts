import * as ort from "onnxruntime-web/wasm";

import { areAnglesAcceptable, isConvexQuad, quadArea } from "../geometry";
import type { DetectionResult, Quad } from "../types";
import { analyzeImageQuality } from "./quality";

const INPUT = 256;
const HEATMAP = 128;
ort.env.wasm.wasmPaths = "/vendor/onnx/";
// Several threads need the page to be cross-origin isolated (Caddyfile says so).
ort.env.wasm.numThreads = self.crossOriginIsolated ? Math.max(1, Math.min(3, (navigator.hardwareConcurrency || 2) - 1)) : 1;

let session: Promise<ort.InferenceSession> | null = null;
const input = new Float32Array(3 * INPUT * INPUT);

const load = () => (session ??= ort.InferenceSession.create("/models/docaligner-lcnet100.onnx", {
  executionProviders: ["wasm"],
  graphOptimizationLevel: "all",
}));

function corner(heat: Float32Array, offset: number) {
  let peak = -Infinity, at = 0;
  for (let i = 0; i < HEATMAP * HEATMAP; i += 1) {
    if (heat[offset + i] > peak) { peak = heat[offset + i]; at = i; }
  }
  const py = Math.floor(at / HEATMAP), px = at % HEATMAP;
  let sum = 0, sx = 0, sy = 0;
  for (let dy = -2; dy <= 2; dy += 1) {
    for (let dx = -2; dx <= 2; dx += 1) {
      const y = py + dy, x = px + dx;
      if (y < 0 || y >= HEATMAP || x < 0 || x >= HEATMAP) continue;
      const v = heat[offset + y * HEATMAP + x];
      if (v > peak * 0.5) { sum += v; sx += v * x; sy += v * y; }
    }
  }
  return { x: (sum > 0 ? sx / sum : px) / HEATMAP, y: (sum > 0 ? sy / sum : py) / HEATMAP, peak };
}

async function detect(pixels: Uint8ClampedArray, tracking: boolean): Promise<DetectionResult> {
  const started = performance.now();
  const model = await load();
  const size = INPUT * INPUT;
  for (let i = 0; i < size; i += 1) {
    input[i] = pixels[i * 4] / 255;
    input[size + i] = pixels[i * 4 + 1] / 255;
    input[size * 2 + i] = pixels[i * 4 + 2] / 255;
  }
  const outputs = await model.run({ img: new ort.Tensor("float32", input, [1, 3, INPUT, INPUT]) });
  const heat = outputs.heatmap.data as Float32Array;
  const channel = HEATMAP * HEATMAP;
  const found = [0, 1, 2, 3].map((index) => corner(heat, index * channel));
  const average = found.reduce((sum, item) => sum + item.peak, 0) / 4;
  const lowest = Math.min(...found.map((item) => item.peak));
  const quad = found.map(({ x, y }) => [x, y]) as Quad;
  // Harder to start following a page than to keep following one: no flicker at the threshold.
  const sure = tracking ? average >= 0.22 && lowest >= 0.12 : average >= 0.34 && lowest >= 0.2;
  const valid = sure && isConvexQuad(quad) && areAnglesAcceptable(quad) && quadArea(quad) >= 0.03;
  return {
    detected: valid,
    quad: valid ? quad : null,
    confidence: average,
    quality: analyzeImageQuality(pixels, INPUT, INPUT, valid ? quad : null),
    ms: performance.now() - started,
  };
}

self.onmessage = async (event: MessageEvent) => {
  const { type, id, pixels, tracking } = event.data;
  try {
    if (type === "load") {
      await load();
      self.postMessage({ type: "loaded" });
    } else if (type === "detect") {
      self.postMessage({ type: "result", id, result: await detect(pixels, tracking) });
    }
  } catch (error) {
    self.postMessage({ type: "failed", id, message: String(error) });
  }
};
