export type Point = [number, number]; // [x, y] in 0..1 normalized range
export type Quad = [Point, Point, Point, Point]; // [TL, TR, BR, BL]

export interface DetectionResult {
  detected: boolean;
  quad: Quad | null;
  confidence: number;
}

function distSq(p1: Point, p2: Point): number {
  const dx = p1[0] - p2[0], dy = p1[1] - p2[1];
  return dx * dx + dy * dy;
}

function crossProduct(o: [number, number], a: [number, number], b: [number, number]): number {
  return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
}

function convexHull(points: [number, number][]): [number, number][] {
  if (points.length <= 3) return points;
  const sorted = points.slice().sort((a, b) => a[0] === b[0] ? a[1] - b[1] : a[0] - b[0]);
  const lower: [number, number][] = [];
  for (const p of sorted) {
    while (lower.length >= 2 && crossProduct(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) {
      lower.pop();
    }
    lower.push(p);
  }
  const upper: [number, number][] = [];
  for (let i = sorted.length - 1; i >= 0; i--) {
    const p = sorted[i];
    while (upper.length >= 2 && crossProduct(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) {
      upper.pop();
    }
    upper.push(p);
  }
  lower.pop();
  upper.pop();
  return lower.concat(upper);
}

function shoelaceArea(quad: Quad): number {
  const [p0, p1, p2, p3] = quad;
  return 0.5 * Math.abs(
    (p0[0] * p1[1] - p0[1] * p1[0]) +
    (p1[0] * p2[1] - p1[1] * p2[0]) +
    (p2[0] * p3[1] - p2[1] * p3[0]) +
    (p3[0] * p0[1] - p3[1] * p0[0])
  );
}

function isConvexQuad(quad: Quad): boolean {
  const n = 4;
  let sign = 0;
  for (let i = 0; i < n; i++) {
    const o = quad[i];
    const a = quad[(i + 1) % n];
    const b = quad[(i + 2) % n];
    const cp = (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
    if (Math.abs(cp) < 1e-6) return false;
    if (sign === 0) sign = cp > 0 ? 1 : -1;
    else if ((cp > 0 ? 1 : -1) !== sign) return false;
  }
  return true;
}

export function detectDocument(
  source: HTMLVideoElement,
  canvas: HTMLCanvasElement,
  targetWidth = 160
): DetectionResult {
  if (!source.videoWidth || !source.videoHeight) {
    return { detected: false, quad: null, confidence: 0 };
  }

  const targetHeight = Math.max(90, Math.round(targetWidth * source.videoHeight / source.videoWidth));
  if (canvas.width !== targetWidth || canvas.height !== targetHeight) {
    canvas.width = targetWidth;
    canvas.height = targetHeight;
  }

  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) return { detected: false, quad: null, confidence: 0 };

  ctx.drawImage(source, 0, 0, targetWidth, targetHeight);
  const frame = ctx.getImageData(0, 0, targetWidth, targetHeight);
  const { data, width, height } = frame;
  const numPixels = width * height;

  const gray = new Uint8Array(numPixels);
  const hist = new Int32Array(256);
  let minLuma = 255, maxLuma = 0, sumLuma = 0;

  for (let i = 0, p = 0; i < data.length; i += 4, p++) {
    // Relative luminance
    const luma = (data[i] * 77 + data[i + 1] * 150 + data[i + 2] * 29) >> 8;
    gray[p] = luma;
    hist[luma]++;
    if (luma < minLuma) minLuma = luma;
    if (luma > maxLuma) maxLuma = luma;
    sumLuma += luma;
  }

  // If contrast is very low (e.g. pointing at blank surface/wall), no document
  const dynamicRange = maxLuma - minLuma;
  if (dynamicRange < 38) {
    return { detected: false, quad: null, confidence: 0 };
  }

  // Otsu's thresholding
  let totalMean = 0;
  for (let t = 0; t < 256; t++) totalMean += t * hist[t];

  let w0 = 0, sum0 = 0, maxBetweenVar = 0, bestThreshold = 128;
  for (let t = 0; t < 256; t++) {
    w0 += hist[t];
    if (w0 === 0) continue;
    const w1 = numPixels - w0;
    if (w1 === 0) break;
    sum0 += t * hist[t];
    const m0 = sum0 / w0;
    const m1 = (totalMean - sum0) / w1;
    const betweenVar = w0 * w1 * (m0 - m1) * (m0 - m1);
    if (betweenVar > maxBetweenVar) {
      maxBetweenVar = betweenVar;
      bestThreshold = t;
    }
  }

  // Separability check
  const mean = sumLuma / numPixels;
  let totalVar = 0;
  for (let t = 0; t < 256; t++) {
    totalVar += hist[t] * (t - mean) * (t - mean);
  }
  const separability = totalVar > 0 ? maxBetweenVar / (totalVar * numPixels) : 0;
  if (separability < 0.12) {
    return { detected: false, quad: null, confidence: 0 };
  }

  // Sample outer margins to detect whether background is dark or light
  let borderBright = 0, borderTotal = 0;
  const marginX = Math.max(2, Math.floor(width * 0.05));
  const marginY = Math.max(2, Math.floor(height * 0.05));

  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      if (x < marginX || x >= width - marginX || y < marginY || y >= height - marginY) {
        borderTotal++;
        if (gray[y * width + x] > bestThreshold) borderBright++;
      }
    }
  }

  const docIsBright = borderBright < borderTotal * 0.55;

  // Binary mask
  const binary = new Uint8Array(numPixels);
  for (let i = 0; i < numPixels; i++) {
    binary[i] = (docIsBright ? gray[i] > bestThreshold : gray[i] <= bestThreshold) ? 1 : 0;
  }

  // 3x3 Morphological close (dilation then erosion)
  const dilated = new Uint8Array(numPixels);
  for (let y = 1; y < height - 1; y++) {
    const row = y * width;
    for (let x = 1; x < width - 1; x++) {
      const idx = row + x;
      dilated[idx] = (binary[idx] || binary[idx - 1] || binary[idx + 1] ||
                      binary[idx - width] || binary[idx + width] ||
                      binary[idx - width - 1] || binary[idx - width + 1] ||
                      binary[idx + width - 1] || binary[idx + width + 1]) ? 1 : 0;
    }
  }

  const closed = new Uint8Array(numPixels);
  for (let y = 1; y < height - 1; y++) {
    const row = y * width;
    for (let x = 1; x < width - 1; x++) {
      const idx = row + x;
      closed[idx] = (dilated[idx] && dilated[idx - 1] && dilated[idx + 1] &&
                     dilated[idx - width] && dilated[idx + width] &&
                     dilated[idx - width - 1] && dilated[idx - width + 1] &&
                     dilated[idx + width - 1] && dilated[idx + width + 1]) ? 1 : 0;
    }
  }

  // Find the largest connected component (BFS)
  const visited = new Uint8Array(numPixels);
  const queue = new Int32Array(numPixels);
  let maxArea = 0;
  let bestPoints: [number, number][] = [];

  for (let y = marginY; y < height - marginY; y += 2) {
    const row = y * width;
    for (let x = marginX; x < width - marginX; x += 2) {
      const start = row + x;
      if (closed[start] === 1 && visited[start] === 0) {
        let qHead = 0, qTail = 0;
        queue[qTail++] = start;
        visited[start] = 1;
        let area = 0;
        const compBoundary: [number, number][] = [];

        while (qHead < qTail) {
          const curr = queue[qHead++];
          area++;
          const cx = curr % width;
          const cy = (curr / width) | 0;

          const isEdge = cx <= 1 || cx >= width - 2 || cy <= 1 || cy >= height - 2 ||
            closed[curr - 1] === 0 || closed[curr + 1] === 0 ||
            closed[curr - width] === 0 || closed[curr + width] === 0;

          if (isEdge && (compBoundary.length === 0 || (cx % 2 === 0 && cy % 2 === 0))) {
            compBoundary.push([cx, cy]);
          }

          const n1 = curr - 1;
          if (cx > 0 && closed[n1] === 1 && visited[n1] === 0) {
            visited[n1] = 1; queue[qTail++] = n1;
          }
          const n2 = curr + 1;
          if (cx < width - 1 && closed[n2] === 1 && visited[n2] === 0) {
            visited[n2] = 1; queue[qTail++] = n2;
          }
          const n3 = curr - width;
          if (cy > 0 && closed[n3] === 1 && visited[n3] === 0) {
            visited[n3] = 1; queue[qTail++] = n3;
          }
          const n4 = curr + width;
          if (cy < height - 1 && closed[n4] === 1 && visited[n4] === 0) {
            visited[n4] = 1; queue[qTail++] = n4;
          }
        }

        if (area > maxArea) {
          maxArea = area;
          bestPoints = compBoundary;
        }
      }
    }
  }

  const areaRatio = maxArea / numPixels;
  // A valid document occupies between 7% and 90% of the frame
  if (areaRatio < 0.07 || areaRatio > 0.90 || bestPoints.length < 12) {
    return { detected: false, quad: null, confidence: 0 };
  }

  // Compute 2D Convex Hull
  const hull = convexHull(bestPoints);
  if (hull.length < 4) {
    return { detected: false, quad: null, confidence: 0 };
  }

  // Extract initial 4 extreme corners from hull:
  // TL minimizes x + y
  // TR maximizes x - y
  // BR maximizes x + y
  // BL minimizes x - y
  let tl = hull[0], tr = hull[0], br = hull[0], bl = hull[0];
  let minSum = Infinity, maxSum = -Infinity;
  let maxDiff = -Infinity, minDiff = Infinity;

  for (const p of hull) {
    const sum = p[0] + p[1];
    const diff = p[0] - p[1];
    if (sum < minSum) { minSum = sum; tl = p; }
    if (sum > maxSum) { maxSum = sum; br = p; }
    if (diff > maxDiff) { maxDiff = diff; tr = p; }
    if (diff < minDiff) { minDiff = diff; bl = p; }
  }

  // Convert to normalized [0..1] quad
  const quad: Quad = [
    [tl[0] / width, tl[1] / height],
    [tr[0] / width, tr[1] / height],
    [br[0] / width, br[1] / height],
    [bl[0] / width, bl[1] / height],
  ];

  // Validate side lengths: all 4 sides must be non-trivial
  const topLen = Math.sqrt(distSq(quad[0], quad[1]));
  const rightLen = Math.sqrt(distSq(quad[1], quad[2]));
  const bottomLen = Math.sqrt(distSq(quad[2], quad[3]));
  const leftLen = Math.sqrt(distSq(quad[3], quad[0]));

  const minSide = Math.min(topLen, rightLen, bottomLen, leftLen);
  if (minSide < 0.15) {
    return { detected: false, quad: null, confidence: 0 };
  }

  // Opposite sides ratio
  const horizRatio = Math.min(topLen, bottomLen) / Math.max(topLen, bottomLen);
  const vertRatio = Math.min(leftLen, rightLen) / Math.max(leftLen, rightLen);
  if (horizRatio < 0.35 || vertRatio < 0.35) {
    return { detected: false, quad: null, confidence: 0 };
  }

  // Convexity check
  if (!isConvexQuad(quad)) {
    return { detected: false, quad: null, confidence: 0 };
  }

  // Area check
  const quadArea = shoelaceArea(quad);
  if (quadArea < 0.06 || quadArea > 0.92) {
    return { detected: false, quad: null, confidence: 0 };
  }

  const confidence = Math.min(0.98, Math.max(0.4, 0.4 + areaRatio * 0.4 + separability * 0.3));
  return { detected: true, quad, confidence };
}
