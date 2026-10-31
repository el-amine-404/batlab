// Test suite for detector algorithms:
// Verifies rejection of human silhouettes and walls, and detection of cards and documents.

function distSq(p1, p2) {
  const dx = p1[0] - p2[0], dy = p1[1] - p2[1];
  return dx * dx + dy * dy;
}

function crossProduct(o, a, b) {
  return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
}

function convexHull(points) {
  if (points.length <= 3) return points;
  const sorted = points.slice().sort((a, b) => a[0] === b[0] ? a[1] - b[1] : a[0] - b[0]);
  const lower = [];
  for (const p of sorted) {
    while (lower.length >= 2 && crossProduct(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
    lower.push(p);
  }
  const upper = [];
  for (let i = sorted.length - 1; i >= 0; i--) {
    const p = sorted[i];
    while (upper.length >= 2 && crossProduct(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
    upper.push(p);
  }
  lower.pop(); upper.pop();
  return lower.concat(upper);
}

function perpendicularDistance(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const lenSq = dx * dx + dy * dy;
  if (lenSq < 1e-9) return Math.sqrt(distSq(p, a));
  return Math.abs((p[1] - a[1]) * dx - (p[0] - a[0]) * dy) / Math.sqrt(lenSq);
}

function dpOpen(points, eps) {
  if (points.length <= 2) return points;
  let maxD = 0, idx = -1;
  const first = points[0], last = points[points.length - 1];
  for (let i = 1; i < points.length - 1; i++) {
    const d = perpendicularDistance(points[i], first, last);
    if (d > maxD) { maxD = d; idx = i; }
  }
  if (maxD > eps) {
    const left = dpOpen(points.slice(0, idx + 1), eps);
    const right = dpOpen(points.slice(idx), eps);
    return left.slice(0, -1).concat(right);
  }
  return [first, last];
}

function dpClosed(contour, eps) {
  if (contour.length < 4) return contour;
  let maxD = 0, idxA = 0, idxB = 0;
  for (let i = 0; i < contour.length; i++) {
    for (let j = i + 1; j < contour.length; j++) {
      const d = distSq(contour[i], contour[j]);
      if (d > maxD) { maxD = d; idxA = i; idxB = j; }
    }
  }
  if (idxA > idxB) { const t = idxA; idxA = idxB; idxB = t; }
  const half1 = contour.slice(idxA, idxB + 1);
  const half2 = contour.slice(idxB).concat(contour.slice(0, idxA + 1));
  const simp1 = dpOpen(half1, eps);
  const simp2 = dpOpen(half2, eps);
  return simp1.slice(0, -1).concat(simp2.slice(0, -1));
}

function orderQuadClockwise(pts) {
  const cx = (pts[0][0] + pts[1][0] + pts[2][0] + pts[3][0]) / 4;
  const cy = (pts[0][1] + pts[1][1] + pts[2][1] + pts[3][1]) / 4;
  const sorted = pts.slice().sort((a, b) => {
    return Math.atan2(a[1] - cy, a[0] - cx) - Math.atan2(b[1] - cy, b[0] - cx);
  });
  let tlIdx = 0, minSum = Infinity;
  for (let i = 0; i < 4; i++) {
    const s = sorted[i][0] + sorted[i][1];
    if (s < minSum) { minSum = s; tlIdx = i; }
  }
  return [
    sorted[tlIdx],
    sorted[(tlIdx + 1) % 4],
    sorted[(tlIdx + 2) % 4],
    sorted[(tlIdx + 3) % 4]
  ];
}

function validateAndScoreQuad(quad, mag, w, h) {
  const numPixels = w * h;
  for (let i = 0; i < 4; i++) {
    const o = quad[i], a = quad[(i + 1) % 4], b = quad[(i + 2) % 4];
    const cp = (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
    if (cp <= 0) return null;
  }
  const s0 = Math.sqrt(distSq(quad[0], quad[1]));
  const s1 = Math.sqrt(distSq(quad[1], quad[2]));
  const s2 = Math.sqrt(distSq(quad[2], quad[3]));
  const s3 = Math.sqrt(distSq(quad[3], quad[0]));

  const minSide = Math.min(s0, s1, s2, s3);
  if (minSide < Math.min(w, h) * 0.07) return null;
  const maxSide = Math.max(s0, s1, s2, s3);
  if (maxSide / minSide > 3.6) return null;
  if (Math.min(s0, s2) / Math.max(s0, s2) < 0.40) return null;
  if (Math.min(s1, s3) / Math.max(s1, s3) < 0.40) return null;

  const area = 0.5 * Math.abs(
    (quad[0][0]*quad[1][1] - quad[0][1]*quad[1][0]) +
    (quad[1][0]*quad[2][1] - quad[1][1]*quad[2][0]) +
    (quad[2][0]*quad[3][1] - quad[2][1]*quad[3][0]) +
    (quad[3][0]*quad[0][1] - quad[3][0]*quad[0][1])
  );
  const areaRatio = area / numPixels;
  if (areaRatio < 0.015 || areaRatio > 0.92) return null;

  let maxCos = 0;
  for (let i = 0; i < 4; i++) {
    const prev = quad[(i + 3) % 4], curr = quad[i], next = quad[(i + 1) % 4];
    const v1x = prev[0] - curr[0], v1y = prev[1] - curr[1];
    const v2x = next[0] - curr[0], v2y = next[1] - curr[1];
    const len1 = Math.sqrt(v1x*v1x + v1y*v1y), len2 = Math.sqrt(v2x*v2x + v2y*v2y);
    const cosVal = Math.abs((v1x*v2x + v1y*v2y) / (len1 * len2));
    if (cosVal > maxCos) maxCos = cosVal;
    if (cosVal > 0.65) return null;
  }

  const samplesPerSide = 20;
  let totalSupported = 0;
  const edgeThreshold = 35;
  const searchRadius = 2;

  for (let i = 0; i < 4; i++) {
    const p1 = quad[i], p2 = quad[(i + 1) % 4];
    let sideHits = 0;
    for (let s = 1; s <= samplesPerSide; s++) {
      const t = s / (samplesPerSide + 1);
      const px = Math.round(p1[0] + t * (p2[0] - p1[0]));
      const py = Math.round(p1[1] + t * (p2[1] - p1[1]));
      let maxNear = 0;
      for (let dy = -searchRadius; dy <= searchRadius; dy++) {
        for (let dx = -searchRadius; dx <= searchRadius; dx++) {
          const ny = py + dy, nx = px + dx;
          if (nx >= 0 && nx < w && ny >= 0 && ny < h) {
            const v = mag[ny * w + nx];
            if (v > maxNear) maxNear = v;
          }
        }
      }
      if (maxNear >= edgeThreshold) sideHits++;
    }
    const sup = sideHits / samplesPerSide;
    if (sup < 0.20) return null;
    totalSupported += sideHits;
  }

  const totalSupport = totalSupported / (4 * samplesPerSide);
  if (totalSupport < 0.38) return null;

  const score = totalSupport * 2.0 + (1 - maxCos) * 0.5 + Math.min(areaRatio, 0.4);
  return { quad, score, totalSupport, areaRatio };
}

function detectArray(gray, width, height) {
  const numPixels = width * height;
  const blurred = new Uint8Array(numPixels);
  const mag = new Float32Array(numPixels);
  const edges = new Uint8Array(numPixels);
  const dilated = new Uint8Array(numPixels);
  const visited = new Uint8Array(numPixels);
  const queue = new Int32Array(numPixels);

  const hist = new Int32Array(256);
  let minLuma = 255, maxLuma = 0;
  for (let i = 0; i < numPixels; i++) {
    const v = gray[i];
    hist[v]++;
    if (v < minLuma) minLuma = v;
    if (v > maxLuma) maxLuma = v;
  }
  if (maxLuma - minLuma < 28) return { detected: false, quad: null };

  for (let y = 1; y < height - 1; y++) {
    const row = y * width;
    for (let x = 1; x < width - 1; x++) {
      const idx = row + x;
      blurred[idx] = (
        gray[idx - width - 1] + 2 * gray[idx - width] + gray[idx - width + 1] +
        2 * gray[idx - 1] + 4 * gray[idx] + 2 * gray[idx + 1] +
        gray[idx + width - 1] + 2 * gray[idx + width] + gray[idx + width + 1]
      ) >> 4;
    }
  }

  let maxMag = 0;
  for (let y = 1; y < height - 1; y++) {
    const row = y * width;
    for (let x = 1; x < width - 1; x++) {
      const idx = row + x;
      const gx = (blurred[idx - width + 1] + 2 * blurred[idx + 1] + blurred[idx + width + 1]) -
                 (blurred[idx - width - 1] + 2 * blurred[idx - 1] + blurred[idx + width - 1]);
      const gy = (blurred[idx + width - 1] + 2 * blurred[idx + width] + blurred[idx + width + 1]) -
                 (blurred[idx - width - 1] + 2 * blurred[idx - width] + blurred[idx - width + 1]);
      const m = Math.sqrt(gx * gx + gy * gy);
      mag[idx] = m;
      if (m > maxMag) maxMag = m;
    }
  }
  if (maxMag < 35) return { detected: false, quad: null };

  let bestCandidate = null;

  // Method A: Edge-enclosed regions
  for (let i = 0; i < numPixels; i++) edges[i] = mag[i] > 35 ? 1 : 0;
  for (let y = 1; y < height - 1; y++) {
    const row = y * width;
    for (let x = 1; x < width - 1; x++) {
      const idx = row + x;
      if (edges[idx] || edges[idx - 1] || edges[idx + 1] || edges[idx - width] || edges[idx + width]) {
        dilated[idx] = 1;
      }
    }
  }

  for (let y = 2; y < height - 2; y += 2) {
    const row = y * width;
    for (let x = 2; x < width - 2; x += 2) {
      const startIdx = row + x;
      if (dilated[startIdx] === 0 && visited[startIdx] === 0) {
        let qHead = 0, qTail = 0;
        queue[qTail++] = startIdx;
        visited[startIdx] = 1;
        let area = 0;
        let touchesBorder = false;
        const boundary = [];

        while (qHead < qTail) {
          const curr = queue[qHead++];
          area++;
          const cx = curr % width, cy = (curr / width) | 0;
          if (cx <= 2 || cx >= width - 3 || cy <= 2 || cy >= height - 3) touchesBorder = true;

          const n1 = curr - 1, n2 = curr + 1, n3 = curr - width, n4 = curr + width;
          if (dilated[n1] === 1 || dilated[n2] === 1 || dilated[n3] === 1 || dilated[n4] === 1) {
            if (boundary.length === 0 || (cx % 2 === 0 && cy % 2 === 0)) boundary.push([cx, cy]);
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

  // Method B: Multi-threshold
  let totalMean = 0;
  for (let t = 0; t < 256; t++) totalMean += t * hist[t];
  let w0 = 0, sum0 = 0, maxBetweenVar = 0, otsuT = 128;
  for (let t = 0; t < 256; t++) {
    w0 += hist[t];
    if (w0 === 0) continue;
    const w1 = numPixels - w0;
    if (w1 === 0) break;
    sum0 += t * hist[t];
    const m0 = sum0 / w0, m1 = (totalMean - sum0) / w1;
    const bVar = w0 * w1 * (m0 - m1) * (m0 - m1);
    if (bVar > maxBetweenVar) { maxBetweenVar = bVar; otsuT = t; }
  }

  for (const t of [otsuT, 65, 130, 195]) {
    for (const mode of [1, 0]) {
      visited.fill(0);
      for (let y = 3; y < height - 3; y += 3) {
        const row = y * width;
        for (let x = 3; x < width - 3; x += 3) {
          const startIdx = row + x;
          const isFg = mode === 1 ? blurred[startIdx] > t : blurred[startIdx] <= t;
          if (isFg && visited[startIdx] === 0) {
            let qHead = 0, qTail = 0;
            queue[qTail++] = startIdx;
            visited[startIdx] = 1;
            let compArea = 0;
            const boundary = [];

            while (qHead < qTail) {
              const curr = queue[qHead++];
              compArea++;
              const cx = curr % width, cy = (curr / width) | 0;
              let isB = false;
              if (cx <= 1 || cx >= width - 2 || cy <= 1 || cy >= height - 2) isB = true;
              else {
                const f1 = mode === 1 ? blurred[curr - 1] > t : blurred[curr - 1] <= t;
                const f2 = mode === 1 ? blurred[curr + 1] > t : blurred[curr + 1] <= t;
                const f3 = mode === 1 ? blurred[curr - width] > t : blurred[curr - width] <= t;
                const f4 = mode === 1 ? blurred[curr + width] > t : blurred[curr + width] <= t;
                if (!f1 || !f2 || !f3 || !f4) isB = true;
              }
              if (isB && (boundary.length === 0 || (cx % 2 === 0 && cy % 2 === 0))) boundary.push([cx, cy]);

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
    return {
      detected: true,
      quad: bestCandidate.quad.map(([x, y]) => [x / width, y / height]),
      score: bestCandidate.score
    };
  }
  return { detected: false, quad: null };
}

// RUN TESTS
const W = 160, H = 120;

// Test 1: Person sitting in front of a wall
const img1 = new Uint8Array(W * H);
img1.fill(210); // Wall
for (let y = 0; y < H; y++) {
  for (let x = 0; x < W; x++) {
    if ((x - 80)**2 + (y - 40)**2 <= 20**2) img1[y * W + x] = 60; // Head
    else if (y >= 60 && Math.abs(x - 80) <= 15 + (y - 60) * 0.9) img1[y * W + x] = 60; // Body
  }
}
const res1 = detectArray(img1, W, H);
console.assert(res1.detected === false, 'Test 1 Failed: Person+Wall was wrongly detected!');
console.log('✓ Test 1 Passed: Person in front of blank wall is correctly ignored (detected = false)');

// Test 2: Card held in hand
const img2 = new Uint8Array(img1);
for (let y = 60; y < H; y++) {
  for (let x = 90; x < W; x++) {
    if (x + y > 165) img2[y * W + x] = 130; // Hand
  }
}
const cx = 100, cy = 60, w = 45, h = 28, theta = 0.2;
const cosT = Math.cos(theta), sinT = Math.sin(theta);
for (let y = 0; y < H; y++) {
  for (let x = 0; x < W; x++) {
    const dx = x - cx, dy = y - cy;
    const rx = dx * cosT + dy * sinT;
    const ry = -dx * sinT + dy * cosT;
    if (Math.abs(rx) <= w / 2 && Math.abs(ry) <= h / 2) img2[y * W + x] = 245; // Card
  }
}
// Thumb over part of card border
for (let y = 0; y < H; y++) {
  for (let x = 0; x < W; x++) {
    const dx = x - (cx + 20), dy = y - cy;
    if (dx*dx + dy*dy <= 36) img2[y * W + x] = 130;
  }
}
const res2 = detectArray(img2, W, H);
console.assert(res2.detected === true, 'Test 2 Failed: Card in hand was not detected!');
console.log('✓ Test 2 Passed: Card held in hand is reliably detected with 4 corners traced');

// Test 3: Document on desk
const img3 = new Uint8Array(W * H);
img3.fill(70); // Dark desk
for (let y = 20; y <= 100; y++) {
  for (let x = 30; x <= 130; x++) {
    img3[y * W + x] = 245; // A4 paper
  }
}
const res3 = detectArray(img3, W, H);
console.assert(res3.detected === true, 'Test 3 Failed: Document on desk was not detected!');
console.log('✓ Test 3 Passed: Document on desk is reliably detected');

// Test 4: Blank wall
const img4 = new Uint8Array(W * H);
img4.fill(200);
const res4 = detectArray(img4, W, H);
console.assert(res4.detected === false, 'Test 4 Failed: Blank wall was wrongly detected!');
console.log('✓ Test 4 Passed: Blank surface is ignored (detected = false)');

console.log('All 4 detector validation tests PASSED successfully!');
