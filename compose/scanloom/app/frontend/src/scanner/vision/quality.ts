import type { Point, Quad, QualityMetrics } from "../types";
import { areAnglesAcceptable, distSq, isConvexQuad, isWithinMargins, quadArea } from "../geometry";

/**
 * Analyzes image quality metrics on downsampled pixel buffer
 */
export function analyzeImageQuality(
  pixels: Uint8ClampedArray,
  width: number,
  height: number,
  quad: Quad | null,
  profile: "auto" | "card" | "document" = "auto"
): QualityMetrics {
  const numPixels = width * height;
  let totalLuma = 0;
  let overexposedCount = 0;
  let underexposedCount = 0;

  // 1. Exposure and Glare analysis
  for (let i = 0; i < numPixels; i++) {
    const r = pixels[i * 4];
    const g = pixels[i * 4 + 1];
    const b = pixels[i * 4 + 2];
    const luma = 0.299 * r + 0.587 * g + 0.114 * b;
    totalLuma += luma;

    if (r > 248 && g > 248 && b > 248) {
      overexposedCount++;
    } else if (luma < 30) {
      underexposedCount++;
    }
  }

  const avgLuma = totalLuma / numPixels;
  const exposureScore = avgLuma / 255;
  const glareRatio = overexposedCount / numPixels;
  const glareScore = Math.min(1, glareRatio * 15);

  // 2. Blur / Sharpness calculation using simplified Laplacian on center/document
  let laplacianSum = 0;
  let laplacianSumSq = 0;
  let laplacianCount = 0;

  for (let y = 1; y < height - 1; y += 2) {
    const rowOffset = y * width;
    for (let x = 1; x < width - 1; x += 2) {
      const idx = (rowOffset + x) * 4;
      const c = 0.299 * pixels[idx] + 0.587 * pixels[idx + 1] + 0.114 * pixels[idx + 2];
      const up = 0.299 * pixels[idx - width * 4] + 0.587 * pixels[idx - width * 4 + 1] + 0.114 * pixels[idx - width * 4 + 2];
      const down = 0.299 * pixels[idx + width * 4] + 0.587 * pixels[idx + width * 4 + 1] + 0.114 * pixels[idx + width * 4 + 2];
      const left = 0.299 * pixels[idx - 4] + 0.587 * pixels[idx - 3] + 0.114 * pixels[idx - 2];
      const right = 0.299 * pixels[idx + 4] + 0.587 * pixels[idx + 5] + 0.114 * pixels[idx + 6];

      const lap = 4 * c - (up + down + left + right);
      laplacianSum += lap;
      laplacianSumSq += lap * lap;
      laplacianCount++;
    }
  }

  const lapMean = laplacianCount > 0 ? laplacianSum / laplacianCount : 0;
  const lapVar = laplacianCount > 0 ? laplacianSumSq / laplacianCount - lapMean * lapMean : 0;
  // Map variance to 0..1 blurScore (higher is sharper)
  const blurScore = Math.min(1, Math.max(0, lapVar / 250));

  if (!quad) {
    let warningMessage: string | null = null;
    if (avgLuma < 35) warningMessage = "Low light — improve room lighting";
    else if (glareRatio > 0.08) warningMessage = "Avoid bright reflections and glare";
    else warningMessage = "Align document in camera frame";

    return {
      blurScore,
      glareScore,
      exposureScore,
      areaFraction: 0,
      isWithinBounds: false,
      isGeometryValid: false,
      isStable: false,
      overallQuality: "poor",
      warningMessage,
    };
  }

  // 3. Geometric validation
  const isConvex = isConvexQuad(quad);
  const anglesValid = areAnglesAcceptable(quad);
  const withinBounds = isWithinMargins(quad, 0.02);
  const area = quadArea(quad);

  const isGeometryValid = isConvex && anglesValid;

  // Profile-specific aspect ratio checks
  let profileRatioValid = true;
  if (profile === "card") {
    const w = Math.sqrt(distSq(quad[0], quad[1]));
    const h = Math.sqrt(distSq(quad[1], quad[2]));
    const ratio = h > 1e-4 ? w / h : 1;
    // Standard credit card is 85.6mm / 53.98mm = 1.586. Allow portrait or landscape with perspective leeway
    profileRatioValid = (ratio >= 1.15 && ratio <= 2.1) || (ratio >= 0.45 && ratio <= 0.88);
  }

  let warningMessage: string | null = null;
  let overallQuality: "good" | "warning" | "poor" = "good";

  if (!withinBounds) {
    overallQuality = "warning";
    warningMessage = "Keep entire document inside camera view";
  } else if (area < 0.07) {
    overallQuality = "warning";
    warningMessage = "Move camera closer to document";
  } else if (area > 0.94) {
    overallQuality = "warning";
    warningMessage = "Move camera further away";
  } else if (!isGeometryValid || !profileRatioValid) {
    overallQuality = "poor";
    warningMessage = profile === "card" ? "Align rectangular card squarely" : "Hold camera flatter above page";
  } else if (avgLuma < 40) {
    overallQuality = "warning";
    warningMessage = "Lighting is dim — turn on torch or light";
  } else if (glareScore > 0.6) {
    overallQuality = "warning";
    warningMessage = "Tilt slightly to eliminate glare";
  } else if (blurScore < 0.22) {
    overallQuality = "warning";
    warningMessage = "Hold camera steady to focus";
  }

  return {
    blurScore,
    glareScore,
    exposureScore,
    areaFraction: area,
    isWithinBounds: withinBounds,
    isGeometryValid: isGeometryValid && profileRatioValid,
    isStable: false, // Updated by temporal tracker
    overallQuality,
    warningMessage,
  };
}
