export type EnhancementPreset = "faithful" | "paper" | "mono" | "original";

/**
 * Applies client-side document/card enhancement presets
 */
export function applyEnhancement(
  canvas: HTMLCanvasElement,
  preset: EnhancementPreset = "faithful"
): HTMLCanvasElement {
  if (preset === "original") {
    return canvas;
  }

  const w = canvas.width;
  const h = canvas.height;
  const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
  const imgData = ctx.getImageData(0, 0, w, h);
  const data = imgData.data;
  const totalPixels = w * h;

  if (preset === "faithful") {
    // Subtle auto-levels: stretch contrast slightly without clipping color hues
    let minR = 255, maxR = 0;
    let minG = 255, maxG = 0;
    let minB = 255, maxB = 0;

    // Sample pixels for fast percentile calculation
    const step = Math.max(1, Math.floor(totalPixels / 20000));
    for (let i = 0; i < totalPixels; i += step) {
      const r = data[i * 4];
      const g = data[i * 4 + 1];
      const b = data[i * 4 + 2];
      if (r < minR) minR = r;
      if (r > maxR) maxR = r;
      if (g < minG) minG = g;
      if (g > maxG) maxG = g;
      if (b < minB) minB = b;
      if (b > maxB) maxB = b;
    }

    const rangeR = Math.max(1, maxR - minR);
    const rangeG = Math.max(1, maxG - minG);
    const rangeB = Math.max(1, maxB - minB);

    for (let i = 0; i < totalPixels; i++) {
      const idx = i * 4;
      data[idx] = Math.min(255, Math.max(0, Math.round(((data[idx] - minR) / rangeR) * 255)));
      data[idx + 1] = Math.min(255, Math.max(0, Math.round(((data[idx + 1] - minG) / rangeG) * 255)));
      data[idx + 2] = Math.min(255, Math.max(0, Math.round(((data[idx + 2] - minB) / rangeB) * 255)));
    }
  } else if (preset === "paper") {
    // Grayscale with gentle white point boosting to remove paper shadows
    for (let i = 0; i < totalPixels; i++) {
      const idx = i * 4;
      const luma = 0.299 * data[idx] + 0.587 * data[idx + 1] + 0.114 * data[idx + 2];
      // Boost bright areas to clean white while keeping text dark
      const enhanced = luma > 180 ? 255 : Math.min(255, Math.pow(luma / 180, 1.25) * 255);
      data[idx] = enhanced;
      data[idx + 1] = enhanced;
      data[idx + 2] = enhanced;
    }
  } else if (preset === "mono") {
    // High contrast black and white (Otsu-like threshold)
    let sumLuma = 0;
    const step = Math.max(1, Math.floor(totalPixels / 20000));
    let samples = 0;
    for (let i = 0; i < totalPixels; i += step) {
      sumLuma += 0.299 * data[i * 4] + 0.587 * data[i * 4 + 1] + 0.114 * data[i * 4 + 2];
      samples++;
    }
    const threshold = samples > 0 ? (sumLuma / samples) * 0.88 : 128;

    for (let i = 0; i < totalPixels; i++) {
      const idx = i * 4;
      const luma = 0.299 * data[idx] + 0.587 * data[idx + 1] + 0.114 * data[idx + 2];
      const val = luma > threshold ? 255 : 0;
      data[idx] = val;
      data[idx + 1] = val;
      data[idx + 2] = val;
    }
  }

  ctx.putImageData(imgData, 0, 0);
  return canvas;
}
