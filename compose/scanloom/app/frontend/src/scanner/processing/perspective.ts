import type { Point, Quad } from "../types";
import { dist } from "../geometry";

/**
 * Solves an 8x8 linear system using Gaussian elimination with partial pivoting
 */
function solve8x8(A: number[][], b: number[]): number[] | null {
  const n = 8;
  const M: number[][] = Array.from({ length: n }, (_, i) => [...A[i], b[i]]);

  for (let i = 0; i < n; i++) {
    // Pivot selection
    let maxRow = i;
    let maxVal = Math.abs(M[i][i]);
    for (let r = i + 1; r < n; r++) {
      const val = Math.abs(M[r][i]);
      if (val > maxVal) {
        maxVal = val;
        maxRow = r;
      }
    }
    if (maxVal < 1e-12) return null;

    if (maxRow !== i) {
      const tmp = M[i];
      M[i] = M[maxRow];
      M[maxRow] = tmp;
    }

    // Eliminate below
    for (let r = i + 1; r < n; r++) {
      const factor = M[r][i] / M[i][i];
      for (let c = i; c <= n; c++) {
        M[r][c] -= factor * M[i][c];
      }
    }
  }

  // Back-substitution
  const x = new Array(n).fill(0);
  for (let i = n - 1; i >= 0; i--) {
    let sum = M[i][n];
    for (let j = i + 1; j < n; j++) {
      sum -= M[i][j] * x[j];
    }
    x[i] = sum / M[i][i];
  }
  return x;
}

/**
 * Computes the 3x3 homography matrix mapping Destination (u, v) -> Source (x, y)
 */
export function computeInverseHomography(srcQuad: Quad, dstWidth: number, dstHeight: number): number[] | null {
  // dst points: (0,0), (W,0), (W,H), (0,H)
  const dst: [number, number][] = [
    [0, 0],
    [dstWidth, 0],
    [dstWidth, dstHeight],
    [0, dstHeight],
  ];

  const A: number[][] = [];
  const b: number[] = [];

  for (let i = 0; i < 4; i++) {
    const [u, v] = dst[i];
    const [x, y] = srcQuad[i];

    A.push([u, v, 1, 0, 0, 0, -u * x, -v * x]);
    b.push(x);

    A.push([0, 0, 0, u, v, 1, -u * y, -v * y]);
    b.push(y);
  }

  const h = solve8x8(A, b);
  if (!h) return null;
  return [...h, 1.0]; // 3x3 matrix row-major
}

/**
 * Calculates optimal rectangular output dimensions for a quadrilateral
 */
export function calculateOutputDimensions(
  quad: Quad,
  imageWidth: number,
  imageHeight: number,
  aspectRatioConstraint?: number
): { width: number; height: number } {
  const p = quad.map(([x, y]) => [x * imageWidth, y * imageHeight] as Point);
  const wTop = dist(p[0], p[1]);
  const wBottom = dist(p[3], p[2]);
  const hLeft = dist(p[0], p[3]);
  const hRight = dist(p[1], p[2]);

  let width = Math.round(Math.max(wTop, wBottom));
  let height = Math.round(Math.max(hLeft, hRight));

  // Minimum practical scan resolution
  width = Math.max(width, 320);
  height = Math.max(height, 240);

  // If a fixed aspect ratio is specified (e.g., standard ID-1 card ~ 1.586)
  if (aspectRatioConstraint && aspectRatioConstraint > 0) {
    if (width >= height) {
      height = Math.round(width / aspectRatioConstraint);
    } else {
      width = Math.round(height / aspectRatioConstraint);
    }
  }

  return { width, height };
}

/**
 * Performs true projective perspective warp with subpixel bilinear sampling
 */
export function warpPerspective(
  sourceCanvasOrImage: HTMLCanvasElement | HTMLImageElement,
  quad: Quad,
  options?: { targetAspectRatio?: number; maxWidth?: number; maxHeight?: number }
): HTMLCanvasElement {
  const srcW = "videoWidth" in sourceCanvasOrImage ? (sourceCanvasOrImage as any).videoWidth : sourceCanvasOrImage.width;
  const srcH = "videoHeight" in sourceCanvasOrImage ? (sourceCanvasOrImage as any).videoHeight : sourceCanvasOrImage.height;

  // Convert normalized quad to absolute pixel coordinates
  const pixelQuad: Quad = [
    [quad[0][0] * srcW, quad[0][1] * srcH],
    [quad[1][0] * srcW, quad[1][1] * srcH],
    [quad[2][0] * srcW, quad[2][1] * srcH],
    [quad[3][0] * srcW, quad[3][1] * srcH],
  ];

  let { width: dstW, height: dstH } = calculateOutputDimensions(
    quad,
    srcW,
    srcH,
    options?.targetAspectRatio
  );

  // Cap maximum output resolution if needed to prevent memory exhaustion
  const maxW = options?.maxWidth ?? 3840;
  const maxH = options?.maxHeight ?? 3840;
  if (dstW > maxW || dstH > maxH) {
    const scale = Math.min(maxW / dstW, maxH / dstH);
    dstW = Math.round(dstW * scale);
    dstH = Math.round(dstH * scale);
  }

  // Get source pixel data
  const srcCanvas = document.createElement("canvas");
  srcCanvas.width = srcW;
  srcCanvas.height = srcH;
  const srcCtx = srcCanvas.getContext("2d", { willReadFrequently: true })!;
  srcCtx.drawImage(sourceCanvasOrImage, 0, 0, srcW, srcH);
  const srcData = srcCtx.getImageData(0, 0, srcW, srcH);
  const srcPixels = srcData.data;

  // Compute mapping matrix from Destination to Source
  const H = computeInverseHomography(pixelQuad, dstW, dstH);
  if (!H) {
    // Fallback: return full cropped bounding box
    return srcCanvas;
  }

  const [m00, m01, m02, m10, m11, m12, m20, m21, m22] = H;

  // Destination canvas and buffer
  const dstCanvas = document.createElement("canvas");
  dstCanvas.width = dstW;
  dstCanvas.height = dstH;
  const dstCtx = dstCanvas.getContext("2d")!;
  const dstData = dstCtx.createImageData(dstW, dstH);
  const dstPixels = dstData.data;

  // Warp pixel loop with incremental affine projection & bilinear filtering
  let dstIdx = 0;
  for (let y = 0; y < dstH; y++) {
    let uX = m01 * y + m02;
    let uY = m11 * y + m12;
    let uW = m21 * y + m22;

    for (let x = 0; x < dstW; x++) {
      const invW = 1.0 / uW;
      const srcX = uX * invW;
      const srcY = uY * invW;

      // Bilinear interpolation
      if (srcX >= 0 && srcX < srcW - 1 && srcY >= 0 && srcY < srcH - 1) {
        const x0 = srcX | 0;
        const y0 = srcY | 0;
        const x1 = x0 + 1;
        const y1 = y0 + 1;

        const fx = srcX - x0;
        const fy = srcY - y0;
        const w00 = (1 - fx) * (1 - fy);
        const w10 = fx * (1 - fy);
        const w01 = (1 - fx) * fy;
        const w11 = fx * fy;

        const idx00 = (y0 * srcW + x0) * 4;
        const idx10 = (y0 * srcW + x1) * 4;
        const idx01 = (y1 * srcW + x0) * 4;
        const idx11 = (y1 * srcW + x1) * 4;

        dstPixels[dstIdx] = (w00 * srcPixels[idx00] + w10 * srcPixels[idx10] + w01 * srcPixels[idx01] + w11 * srcPixels[idx11]) | 0;
        dstPixels[dstIdx + 1] = (w00 * srcPixels[idx00 + 1] + w10 * srcPixels[idx10 + 1] + w01 * srcPixels[idx01 + 1] + w11 * srcPixels[idx11 + 1]) | 0;
        dstPixels[dstIdx + 2] = (w00 * srcPixels[idx00 + 2] + w10 * srcPixels[idx10 + 2] + w01 * srcPixels[idx01 + 2] + w11 * srcPixels[idx11 + 2]) | 0;
        dstPixels[dstIdx + 3] = 255;
      } else {
        // Pixel outside bounds (edge clamping)
        const clX = Math.max(0, Math.min(srcW - 1, srcX | 0));
        const clY = Math.max(0, Math.min(srcH - 1, srcY | 0));
        const clIdx = (clY * srcW + clX) * 4;
        dstPixels[dstIdx] = srcPixels[clIdx];
        dstPixels[dstIdx + 1] = srcPixels[clIdx + 1];
        dstPixels[dstIdx + 2] = srcPixels[clIdx + 2];
        dstPixels[dstIdx + 3] = 255;
      }

      dstIdx += 4;
      uX += m00;
      uY += m10;
      uW += m20;
    }
  }

  dstCtx.putImageData(dstData, 0, 0);
  return dstCanvas;
}
