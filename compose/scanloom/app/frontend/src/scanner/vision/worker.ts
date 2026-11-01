import * as ort from "onnxruntime-web";
import type { DetectionResult, DetectionProfile, Quad } from "../types";
import { analyzeImageQuality } from "./quality";
import { areAnglesAcceptable, isConvexQuad } from "../geometry";

// Point ONNX runtime to locally served WASM files
ort.env.wasm.wasmPaths = "/vendor/onnx/";
ort.env.wasm.numThreads = 1;
ort.env.wasm.simd = true;

let session: ort.InferenceSession | null = null;
let offscreenCanvas: OffscreenCanvas | null = null;
let offscreenCtx: OffscreenCanvasRenderingContext2D | null = null;

// Reusable buffers to avoid allocations per frame
const INPUT_SIZE = 256;
const HEATMAP_SIZE = 128;
const NUM_CHANNELS = 3;
const inputFloatArray = new Float32Array(1 * NUM_CHANNELS * INPUT_SIZE * INPUT_SIZE);

async function initSession(): Promise<boolean> {
  if (session) return true;
  try {
    session = await ort.InferenceSession.create("/models/docaligner-lcnet100.onnx", {
      executionProviders: ["wasm"],
      graphOptimizationLevel: "all",
    });
    offscreenCanvas = new OffscreenCanvas(INPUT_SIZE, INPUT_SIZE);
    offscreenCtx = offscreenCanvas.getContext("2d", { willReadFrequently: true });
    return true;
  } catch (err) {
    console.error("[Scanner Worker] Failed to create ONNX session:", err);
    return false;
  }
}

function extractSubpixelCentroid(
  heatmap: Float32Array,
  channelOffset: number,
  size: number
): { x: number; y: number; peak: number } {
  const numValues = size * size;
  let peak = -Infinity;
  let peakIdx = 0;

  for (let i = 0; i < numValues; i++) {
    const val = heatmap[channelOffset + i];
    if (val > peak) {
      peak = val;
      peakIdx = i;
    }
  }

  const peakY = Math.floor(peakIdx / size);
  const peakX = peakIdx % size;

  if (peak < 0.15) {
    return { x: peakX / size, y: peakY / size, peak };
  }

  // 5x5 subpixel centroid refinement
  let sumW = 0;
  let sumX = 0;
  let sumY = 0;
  const radius = 2;
  const cutoff = peak * 0.5;

  for (let dy = -radius; dy <= radius; dy++) {
    const y = peakY + dy;
    if (y < 0 || y >= size) continue;
    for (let dx = -radius; dx <= radius; dx++) {
      const x = peakX + dx;
      if (x < 0 || x >= size) continue;
      const v = heatmap[channelOffset + y * size + x];
      if (v > cutoff) {
        const w = v;
        sumW += w;
        sumX += w * x;
        sumY += w * y;
      }
    }
  }

  const subX = sumW > 0 ? sumX / sumW : peakX;
  const subY = sumW > 0 ? sumY / sumW : peakY;

  return {
    x: Math.max(0, Math.min(1, subX / size)),
    y: Math.max(0, Math.min(1, subY / size)),
    peak,
  };
}

async function processFrame(
  bitmap: ImageBitmap,
  profile: DetectionProfile = "auto"
): Promise<DetectionResult> {
  if (!session || !offscreenCanvas || !offscreenCtx) {
    const ready = await initSession();
    if (!ready || !session || !offscreenCtx) {
      bitmap.close();
      return { detected: false, quad: null, confidence: 0, cornerConfidences: [0, 0, 0, 0] };
    }
  }

  try {
    // 1. Draw input frame to 256x256 canvas
    offscreenCtx.drawImage(bitmap, 0, 0, INPUT_SIZE, INPUT_SIZE);
    const imageData = offscreenCtx.getImageData(0, 0, INPUT_SIZE, INPUT_SIZE);
    const pixels = imageData.data;

    // 2. Preprocess into [1, 3, 256, 256] planar Float32 normalized [0..1]
    const planeSize = INPUT_SIZE * INPUT_SIZE;
    const rOffset = 0;
    const gOffset = planeSize;
    const bOffset = planeSize * 2;

    for (let i = 0; i < planeSize; i++) {
      const pi = i * 4;
      inputFloatArray[rOffset + i] = pixels[pi] / 255.0;
      inputFloatArray[gOffset + i] = pixels[pi + 1] / 255.0;
      inputFloatArray[bOffset + i] = pixels[pi + 2] / 255.0;
    }

    // 3. Create tensor and run inference
    const inputTensor = new ort.Tensor("float32", inputFloatArray, [1, 3, INPUT_SIZE, INPUT_SIZE]);
    const feeds = { img: inputTensor };
    const outputs = await session.run(feeds);
    const heatmapOutput = outputs.heatmap;
    const heatmapData = heatmapOutput.data as Float32Array;

    // 4. Extract 4 corners from heatmaps: [TL=0, TR=1, BR=2, BL=3]
    const channelValues = HEATMAP_SIZE * HEATMAP_SIZE;
    const c0 = extractSubpixelCentroid(heatmapData, 0 * channelValues, HEATMAP_SIZE);
    const c1 = extractSubpixelCentroid(heatmapData, 1 * channelValues, HEATMAP_SIZE);
    const c2 = extractSubpixelCentroid(heatmapData, 2 * channelValues, HEATMAP_SIZE);
    const c3 = extractSubpixelCentroid(heatmapData, 3 * channelValues, HEATMAP_SIZE);

    const cornerConfidences: [number, number, number, number] = [c0.peak, c1.peak, c2.peak, c3.peak];
    const avgConfidence = (c0.peak + c1.peak + c2.peak + c3.peak) / 4;
    const minConfidence = Math.min(c0.peak, c1.peak, c2.peak, c3.peak);

    const rawQuad: Quad = [
      [c0.x, c0.y],
      [c1.x, c1.y],
      [c2.x, c2.y],
      [c3.x, c3.y],
    ];

    // Reject low confidence detections
    const meetsThreshold = avgConfidence >= 0.28 && minConfidence >= 0.16;
    const isGeometryValid = meetsThreshold && isConvexQuad(rawQuad) && areAnglesAcceptable(rawQuad);

    const quad: Quad | null = isGeometryValid ? rawQuad : null;
    const quality = analyzeImageQuality(pixels, INPUT_SIZE, INPUT_SIZE, quad, profile);

    return {
      detected: isGeometryValid,
      quad,
      confidence: avgConfidence,
      cornerConfidences,
      quality,
    };
  } finally {
    bitmap.close();
  }
}

// Handle Worker messages
self.onmessage = async (event: MessageEvent) => {
  const data = event.data;
  if (!data) return;

  switch (data.type) {
    case "INIT": {
      const success = await initSession();
      self.postMessage({ type: "INIT_RESULT", success });
      break;
    }
    case "DETECT": {
      const id = data.id;
      const bitmap: ImageBitmap = data.bitmap;
      const profile: DetectionProfile = data.profile ?? "auto";

      const result = await processFrame(bitmap, profile);
      self.postMessage({ type: "DETECT_RESULT", id, result });
      break;
    }
  }
};
