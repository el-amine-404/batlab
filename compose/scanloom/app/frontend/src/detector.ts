export type Point = [number, number]; // [x, y] in 0..1 normalized range
export type Quad = [Point, Point, Point, Point]; // [TL, TR, BR, BL]

export interface DetectionResult {
  detected: boolean;
  quad: Quad | null;
  confidence: number;
}

function distSq(p1: [number, number], p2: [number, number]): number {
  const dx = p1[0] - p2[0];
  const dy = p1[1] - p2[1];
  return dx * dx + dy * dy;
}

function crossProduct(o: [number, number], a: [number, number], b: [number, number]): number {
  return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
}

/**
 * Monotone chain algorithm for 2D Convex Hull
 */
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

/**
 * Perpendicular distance from point p to line segment (a, b)
 */
function perpendicularDistance(p: [number, number], a: [number, number], b: [number, number]): number {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const lenSq = dx * dx + dy * dy;
  if (lenSq < 1e-9) return Math.sqrt(distSq(p, a));
  return Math.abs((p[1] - a[1]) * dx - (p[0] - a[0]) * dy) / Math.sqrt(lenSq);
}

/**
 * Open Douglas-Peucker polyline simplification
 */
function dpOpen(points: [number, number][], eps: number): [number, number][] {
  if (points.length <= 2) return points;
  let maxD = 0;
  let idx = -1;
  const first = points[0];
  const last = points[points.length - 1];

  for (let i = 1; i < points.length - 1; i++) {
    const d = perpendicularDistance(points[i], first, last);
    if (d > maxD) {
      maxD = d;
      idx = i;
    }
  }

  if (maxD > eps) {
    const left = dpOpen(points.slice(0, idx + 1), eps);
    const right = dpOpen(points.slice(idx), eps);
    return left.slice(0, -1).concat(right);
  }

  return [first, last];
}

/**
 * Closed-loop Douglas-Peucker polygon simplification
 */
function dpClosed(contour: [number, number][], eps: number): [number, number][] {
  if (contour.length < 4) return contour;
  let maxD = 0;
  let idxA = 0;
  let idxB = 0;

  for (let i = 0; i < contour.length; i++) {
    for (let j = i + 1; j < contour.length; j++) {
      const d = distSq(contour[i], contour[j]);
      if (d > maxD) {
        maxD = d;
        idxA = i;
        idxB = j;
      }
    }
  }

  if (idxA > idxB) {
    const t = idxA;
    idxA = idxB;
    idxB = t;
  }

  const half1 = contour.slice(idxA, idxB + 1);
  const half2 = contour.slice(idxB).concat(contour.slice(0, idxA + 1));
  const simp1 = dpOpen(half1, eps);
  const simp2 = dpOpen(half2, eps);
  return simp1.slice(0, -1).concat(simp2.slice(0, -1));
}

/**
 * Order quad vertices clockwise starting from Top-Left (minimizing x + y)
 */
function orderQuadClockwise(pts: [number, number][]): Quad {
  const cx = (pts[0][0] + pts[1][0] + pts[2][0] + pts[3][0]) / 4;
  const cy = (pts[0][1] + pts[1][1] + pts[2][1] + pts[3][1]) / 4;
  const sorted = pts.slice().sort((a, b) => {
    return Math.atan2(a[1] - cy, a[0] - cx) - Math.atan2(b[1] - cy, b[0] - cx);
  });

  let tlIdx = 0;
  let minSum = Infinity;
  for (let i = 0; i < 4; i++) {
    const s = sorted[i][0] + sorted[i][1];
    if (s < minSum) {
      minSum = s;
      tlIdx = i;
    }
  }

  return [
    sorted[tlIdx],
    sorted[(tlIdx + 1) % 4],
    sorted[(tlIdx + 2) % 4],
    sorted[(tlIdx + 3) % 4],
  ];
}

interface CandidateEvaluation {
  quad: Quad;
  score: number;
  totalSupport: number;
  areaRatio: number;
}

/**
 * Verifies geometry and samples edge support along all 4 quadrilateral sides.
 */
function validateAndScoreQuad(
  quad: Quad,
  mag: Float32Array,
  w: number,
  h: number
): CandidateEvaluation | null {
  const numPixels = w * h;

  // Strict clockwise convexity check
  for (let i = 0; i < 4; i++) {
    const o = quad[i];
    const a = quad[(i + 1) % 4];
    const b = quad[(i + 2) % 4];
    const cp = (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
    if (cp <= 0) return null;
  }

  // Side lengths
  const s0 = Math.sqrt(distSq(quad[0], quad[1]));
  const s1 = Math.sqrt(distSq(quad[1], quad[2]));
  const s2 = Math.sqrt(distSq(quad[2], quad[3]));
  const s3 = Math.sqrt(distSq(quad[3], quad[0]));

  const minSide = Math.min(s0, s1, s2, s3);
  if (minSide < Math.min(w, h) * 0.07) return null;

  const maxSide = Math.max(s0, s1, s2, s3);
  if (maxSide / minSide > 3.6) return null;

  // Perspective ratio between opposing sides
  if (Math.min(s0, s2) / Math.max(s0, s2) < 0.40) return null;
  if (Math.min(s1, s3) / Math.max(s1, s3) < 0.40) return null;

  // Shoelace area
  const area = 0.5 * Math.abs(
    (quad[0][0] * quad[1][1] - quad[0][1] * quad[1][0]) +
    (quad[1][0] * quad[2][1] - quad[1][1] * quad[2][0]) +
    (quad[2][0] * quad[3][1] - quad[2][1] * quad[3][0]) +
    (quad[3][0] * quad[0][1] - quad[3][0] * quad[0][1])
  );
  const areaRatio = area / numPixels;
  // A card in hand can occupy ~1.5% - 15% of frame; a page on desk up to 90%
  if (areaRatio < 0.015 || areaRatio > 0.92) return null;

  // Interior angles: all 4 corners must be roughly rectangular (50° to 130° => |cos| < 0.65)
  let maxCos = 0;
  for (let i = 0; i < 4; i++) {
    const prev = quad[(i + 3) % 4];
    const curr = quad[i];
    const next = quad[(i + 1) % 4];
    const v1x = prev[0] - curr[0], v1y = prev[1] - curr[1];
    const v2x = next[0] - curr[0], v2y = next[1] - curr[1];
    const len1 = Math.sqrt(v1x * v1x + v1y * v1y);
    const len2 = Math.sqrt(v2x * v2x + v2y * v2y);
    const cosVal = Math.abs((v1x * v2x + v1y * v2y) / (len1 * len2));
    if (cosVal > maxCos) maxCos = cosVal;
    if (cosVal > 0.65) return null;
  }

  // Edge support check: sample points along the 4 straight line segments
  const samplesPerSide = 20;
  let totalSupported = 0;
  const edgeThreshold = 35;
  const searchRadius = 2;

  for (let i = 0; i < 4; i++) {
    const p1 = quad[i];
    const p2 = quad[(i + 1) % 4];
    let sideHits = 0;

    for (let s = 1; s <= samplesPerSide; s++) {
      const t = s / (samplesPerSide + 1);
      const px = Math.round(p1[0] + t * (p2[0] - p1[0]));
      const py = Math.round(p1[1] + t * (p2[1] - p1[1]));

      let maxNear = 0;
      for (let dy = -searchRadius; dy <= searchRadius; dy++) {
        for (let dx = -searchRadius; dx <= searchRadius; dx++) {
          const ny = py + dy;
          const nx = px + dx;
          if (nx >= 0 && nx < w && ny >= 0 && ny < h) {
            const val = mag[ny * w + nx];
            if (val > maxNear) maxNear = val;
          }
        }
      }
      if (maxNear >= edgeThreshold) sideHits++;
    }

    const sideSupport = sideHits / samplesPerSide;
    // Each side must have at least 20% edge support (no imaginary side in empty air/wall)
    if (sideSupport < 0.20) return null;
    totalSupported += sideHits;
  }

  const totalSupport = totalSupported / (4 * samplesPerSide);
  // Overall edge support must be at least 38%
  if (totalSupport < 0.38) return null;

  const score = totalSupport * 2.0 + (1 - maxCos) * 0.5 + Math.min(areaRatio, 0.4);
  return { quad, score, totalSupport, areaRatio };
}

// Reusable buffers to avoid memory allocations and GC pauses during continuous detection
let bufSize = 0;
let grayBuf: Uint8Array | null = null;
let blurredBuf: Uint8Array | null = null;
let magBuf: Float32Array | null = null;
let edgesBuf: Uint8Array | null = null;
let dilatedBuf: Uint8Array | null = null;
let visitedBuf: Uint8Array | null = null;
let queueBuf: Int32Array | null = null;

function ensureBuffers(size: number) {
  if (bufSize === size && grayBuf && blurredBuf && magBuf && edgesBuf && dilatedBuf && visitedBuf && queueBuf) {
    return;
  }
  bufSize = size;
  grayBuf = new Uint8Array(size);
  blurredBuf = new Uint8Array(size);
  magBuf = new Float32Array(size);
  edgesBuf = new Uint8Array(size);
  dilatedBuf = new Uint8Array(size);
  visitedBuf = new Uint8Array(size);
  queueBuf = new Int32Array(size);
}

export function detectDocument(
  source: HTMLVideoElement,
  canvas: HTMLCanvasElement,
  targetWidth = 160
): DetectionResult {
  if (!source.videoWidth || !source.videoHeight) {
    return { detected: false, quad: null, confidence: 0 };
  }

  const targetHeight = Math.max(80, Math.round((targetWidth * source.videoHeight) / source.videoWidth));
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

  ensureBuffers(numPixels);
  const gray = grayBuf!;
  const blurred = blurredBuf!;
  const mag = magBuf!;
  const edges = edgesBuf!;
  const dilated = dilatedBuf!;
  const visited = visitedBuf!;
  const queue = queueBuf!;

  const hist = new Int32Array(256);
  let minLuma = 255;
  let maxLuma = 0;
  let sumLuma = 0;

  // 1. Grayscale
  for (let i = 0, p = 0; i < data.length; i += 4, p++) {
    const luma = (data[i] * 77 + data[i + 1] * 150 + data[i + 2] * 29) >> 8;
    gray[p] = luma;
    hist[luma]++;
    if (luma < minLuma) minLuma = luma;
    if (luma > maxLuma) maxLuma = luma;
    sumLuma += luma;
  }

  // Reject blank/flat surfaces (e.g. pointing directly at blank wall)
  const dynamicRange = maxLuma - minLuma;
  if (dynamicRange < 28) {
    return { detected: false, quad: null, confidence: 0 };
  }

  // 2. 3x3 Box/Gaussian blur to eliminate sensor noise
  for (let y = 1; y < height - 1; y++) {
    const row = y * width;
    for (let x = 1; x < width - 1; x++) {
      const idx = row + x;
      const val = (
        gray[idx - width - 1] + 2 * gray[idx - width] + gray[idx - width + 1] +
        2 * gray[idx - 1] + 4 * gray[idx] + 2 * gray[idx + 1] +
        gray[idx + width - 1] + 2 * gray[idx + width] + gray[idx + width + 1]
      ) >> 4;
      blurred[idx] = val;
    }
  }

  // 3. Sobel gradient magnitude
  mag.fill(0);
  let maxMag = 0;
  for (let y = 1; y < height - 1; y++) {
    const row = y * width;
    for (let x = 1; x < width - 1; x++) {
      const idx = row + x;
      const gx = (
        blurred[idx - width + 1] + 2 * blurred[idx + 1] + blurred[idx + width + 1]
      ) - (
        blurred[idx - width - 1] + 2 * blurred[idx - 1] + blurred[idx + width - 1]
      );
      const gy = (
        blurred[idx + width - 1] + 2 * blurred[idx + width] + blurred[idx + width + 1]
      ) - (
        blurred[idx - width - 1] + 2 * blurred[idx - width] + blurred[idx - width + 1]
      );
      const m = Math.sqrt(gx * gx + gy * gy);
      mag[idx] = m;
      if (m > maxMag) maxMag = m;
    }
  }

  if (maxMag < 35) {
    return { detected: false, quad: null, confidence: 0 };
  }

  let bestCandidate: CandidateEvaluation | null = null;

  // 4. Candidate Generation Method A: Edge-enclosed non-edge regions
  // Detects cards held in hand or documents on textured surfaces
  const edgeThreshold = 35;
  for (let i = 0; i < numPixels; i++) {
    edges[i] = mag[i] > edgeThreshold ? 1 : 0;
  }

  dilated.fill(0);
  for (let y = 1; y < height - 1; y++) {
    const row = y * width;
    for (let x = 1; x < width - 1; x++) {
      const idx = row + x;
      if (edges[idx] || edges[idx - 1] || edges[idx + 1] || edges[idx - width] || edges[idx + width]) {
        dilated[idx] = 1;
      }
    }
  }

  visited.fill(0);
  for (let y = 2; y < height - 2; y += 2) {
    const row = y * width;
    for (let x = 2; x < width - 2; x += 2) {
      const startIdx = row + x;
      if (dilated[startIdx] === 0 && visited[startIdx] === 0) {
        let qHead = 0;
        let qTail = 0;
        queue[qTail++] = startIdx;
        visited[startIdx] = 1;
        let area = 0;
        let touchesBorder = false;
        const boundary: [number, number][] = [];

        while (qHead < qTail) {
          const curr = queue[qHead++];
          area++;
          const cx = curr % width;
          const cy = (curr / width) | 0;

          if (cx <= 2 || cx >= width - 3 || cy <= 2 || cy >= height - 3) {
            touchesBorder = true;
          }

          const n1 = curr - 1, n2 = curr + 1, n3 = curr - width, n4 = curr + width;
          if (dilated[n1] === 1 || dilated[n2] === 1 || dilated[n3] === 1 || dilated[n4] === 1) {
            if (boundary.length === 0 || (cx % 2 === 0 && cy % 2 === 0)) {
              boundary.push([cx, cy]);
            }
          }

          if (cx > 0 && dilated[n1] === 0 && visited[n1] === 0) { visited[n1] = 1; queue[qTail++] = n1; }
          if (cx < width - 1 && dilated[n2] === 0 && visited[n2] === 0) { visited[n2] = 1; queue[qTail++] = n2; }
          if (cy > 0 && dilated[n3] === 0 && visited[n3] === 0) { visited[n3] = 1; queue[qTail++] = n3; }
          if (cy < height - 1 && dilated[n4] === 0 && visited[n4] === 0) { visited[n4] = 1; queue[qTail++] = n4; }
        }

        const compRatio = area / numPixels;
        if (!touchesBorder && compRatio >= 0.015 && compRatio <= 0.90 && boundary.length >= 8) {
          const hull = convexHull(boundary);
          if (hull.length >= 4) {
            for (const eps of [2, 3, 4, 6]) {
              const simp = dpClosed(hull, eps);
              if (simp.length === 4) {
                const ordered = orderQuadClockwise(simp);
                const scored = validateAndScoreQuad(ordered, mag, width, height);
                if (scored && (!bestCandidate || scored.score > bestCandidate.score)) {
                  bestCandidate = scored;
                }
              }
            }
          }
        }
      }
    }
  }

  // 5. Candidate Generation Method B: Multi-threshold intensity segmentation
  // Detects papers and high-contrast cards against tables/backgrounds
  let totalMean = 0;
  for (let t = 0; t < 256; t++) totalMean += t * hist[t];
  let w0 = 0, sum0 = 0, maxBetweenVar = 0, otsuT = 128;
  for (let t = 0; t < 256; t++) {
    w0 += hist[t];
    if (w0 === 0) continue;
    const w1 = numPixels - w0;
    if (w1 === 0) break;
    sum0 += t * hist[t];
    const m0 = sum0 / w0;
    const m1 = (totalMean - sum0) / w1;
    const bVar = w0 * w1 * (m0 - m1) * (m0 - m1);
    if (bVar > maxBetweenVar) {
      maxBetweenVar = bVar;
      otsuT = t;
    }
  }

  const testThresholds = [otsuT, 65, 130, 195];

  for (const t of testThresholds) {
    for (const mode of [1, 0]) { // 1: bright objects (> t), 0: dark objects (<= t)
      visited.fill(0);
      for (let y = 3; y < height - 3; y += 3) {
        const row = y * width;
        for (let x = 3; x < width - 3; x += 3) {
          const startIdx = row + x;
          const isFg = mode === 1 ? blurred[startIdx] > t : blurred[startIdx] <= t;
          if (isFg && visited[startIdx] === 0) {
            let qHead = 0;
            let qTail = 0;
            queue[qTail++] = startIdx;
            visited[startIdx] = 1;
            let compArea = 0;
            const boundary: [number, number][] = [];

            while (qHead < qTail) {
              const curr = queue[qHead++];
              compArea++;
              const cx = curr % width;
              const cy = (curr / width) | 0;

              let isB = false;
              if (cx <= 1 || cx >= width - 2 || cy <= 1 || cy >= height - 2) {
                isB = true;
              } else {
                const f1 = mode === 1 ? blurred[curr - 1] > t : blurred[curr - 1] <= t;
                const f2 = mode === 1 ? blurred[curr + 1] > t : blurred[curr + 1] <= t;
                const f3 = mode === 1 ? blurred[curr - width] > t : blurred[curr - width] <= t;
                const f4 = mode === 1 ? blurred[curr + width] > t : blurred[curr + width] <= t;
                if (!f1 || !f2 || !f3 || !f4) isB = true;
              }
              if (isB && (boundary.length === 0 || (cx % 2 === 0 && cy % 2 === 0))) {
                boundary.push([cx, cy]);
              }

              if (cx > 0) {
                const n = curr - 1;
                const f = mode === 1 ? blurred[n] > t : blurred[n] <= t;
                if (f && visited[n] === 0) { visited[n] = 1; queue[qTail++] = n; }
              }
              if (cx < width - 1) {
                const n = curr + 1;
                const f = mode === 1 ? blurred[n] > t : blurred[n] <= t;
                if (f && visited[n] === 0) { visited[n] = 1; queue[qTail++] = n; }
              }
              if (cy > 0) {
                const n = curr - width;
                const f = mode === 1 ? blurred[n] > t : blurred[n] <= t;
                if (f && visited[n] === 0) { visited[n] = 1; queue[qTail++] = n; }
              }
              if (cy < height - 1) {
                const n = curr + width;
                const f = mode === 1 ? blurred[n] > t : blurred[n] <= t;
                if (f && visited[n] === 0) { visited[n] = 1; queue[qTail++] = n; }
              }
            }

            const compRatio = compArea / numPixels;
            if (compRatio >= 0.015 && compRatio <= 0.90 && boundary.length >= 8) {
              const hull = convexHull(boundary);
              if (hull.length >= 4) {
                for (const eps of [2, 3, 4, 6]) {
                  const simp = dpClosed(hull, eps);
                  if (simp.length === 4) {
                    const ordered = orderQuadClockwise(simp);
                    const scored = validateAndScoreQuad(ordered, mag, width, height);
                    if (scored && (!bestCandidate || scored.score > bestCandidate.score)) {
                      bestCandidate = scored;
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  }

  if (bestCandidate) {
    const normQuad: Quad = [
      [bestCandidate.quad[0][0] / width, bestCandidate.quad[0][1] / height],
      [bestCandidate.quad[1][0] / width, bestCandidate.quad[1][1] / height],
      [bestCandidate.quad[2][0] / width, bestCandidate.quad[2][1] / height],
      [bestCandidate.quad[3][0] / width, bestCandidate.quad[3][1] / height],
    ];
    return {
      detected: true,
      quad: normQuad,
      confidence: Math.min(0.98, Math.max(0.40, bestCandidate.score / 2.0)),
    };
  }

  return { detected: false, quad: null, confidence: 0 };
}
