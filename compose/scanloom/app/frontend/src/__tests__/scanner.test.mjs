import assert from "node:assert";

// 1. Geometry functions under test
function distSq(p1, p2) {
  const dx = p1[0] - p2[0];
  const dy = p1[1] - p2[1];
  return dx * dx + dy * dy;
}

function dist(p1, p2) {
  return Math.sqrt(distSq(p1, p2));
}

function crossProduct(o, a, b) {
  return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
}

function quadArea(quad) {
  let area = 0;
  for (let i = 0; i < 4; i++) {
    const j = (i + 1) % 4;
    area += quad[i][0] * quad[j][1];
    area -= quad[j][0] * quad[i][1];
  }
  return Math.abs(area) * 0.5;
}

function isConvexQuad(quad) {
  for (let i = 0; i < 4; i++) {
    const o = quad[i];
    const a = quad[(i + 1) % 4];
    const b = quad[(i + 2) % 4];
    const cp = crossProduct(o, a, b);
    if (cp <= 0.0001) return false;
  }
  return true;
}

function areAnglesAcceptable(quad, minDeg = 35, maxDeg = 145) {
  for (let i = 0; i < 4; i++) {
    const prev = quad[(i + 3) % 4];
    const curr = quad[i];
    const next = quad[(i + 1) % 4];

    const v1x = prev[0] - curr[0];
    const v1y = prev[1] - curr[1];
    const v2x = next[0] - curr[0];
    const v2y = next[1] - curr[1];

    const dot = v1x * v2x + v1y * v2y;
    const len1 = Math.sqrt(v1x * v1x + v1y * v1y);
    const len2 = Math.sqrt(v2x * v2x + v2y * v2y);
    if (len1 < 1e-6 || len2 < 1e-6) return false;

    const cosTheta = Math.max(-1, Math.min(1, dot / (len1 * len2)));
    const angleDeg = (Math.acos(cosTheta) * 180) / Math.PI;
    if (angleDeg < minDeg || angleDeg > maxDeg) return false;
  }
  return true;
}

function isWithinMargins(quad, margin = 0.015) {
  for (const [x, y] of quad) {
    if (x < margin || x > 1 - margin || y < margin || y > 1 - margin) return false;
  }
  return true;
}

function estimateQuadAspectRatio(quad, frameWidth = 1, frameHeight = 1) {
  const p = quad.map(([x, y]) => [x * frameWidth, y * frameHeight]);
  const topW = dist(p[0], p[1]);
  const bottomW = dist(p[3], p[2]);
  const leftH = dist(p[0], p[3]);
  const rightH = dist(p[1], p[2]);
  const avgW = (topW + bottomW) / 2;
  const avgH = (leftH + rightH) / 2;
  return avgH < 1e-5 ? 1.0 : avgW / avgH;
}

// 2. Homography Solver
function solve8x8(A, b) {
  const n = 8;
  const M = Array.from({ length: n }, (_, i) => [...A[i], b[i]]);

  for (let i = 0; i < n; i++) {
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

    for (let r = i + 1; r < n; r++) {
      const factor = M[r][i] / M[i][i];
      for (let c = i; c <= n; c++) {
        M[r][c] -= factor * M[i][c];
      }
    }
  }

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

function computeInverseHomography(srcQuad, dstWidth, dstHeight) {
  const dst = [
    [0, 0],
    [dstWidth, 0],
    [dstWidth, dstHeight],
    [0, dstHeight],
  ];
  const A = [];
  const b = [];
  for (let i = 0; i < 4; i++) {
    const [u, v] = dst[i];
    const [x, y] = srcQuad[i];
    A.push([u, v, 1, 0, 0, 0, -u * x, -v * x]);
    b.push(x);
    A.push([0, 0, 0, u, v, 1, -u * y, -v * y]);
    b.push(y);
  }
  const h = solve8x8(A, b);
  return h ? [...h, 1.0] : null;
}

// -------------------------------------------------------------
// Test Executions
// -------------------------------------------------------------
console.log("Starting Smart Scanner Test Suite...");

// Test 1: Geometry & Convexity
{
  const validCardQuad = [
    [0.2, 0.3],
    [0.8, 0.3],
    [0.8, 0.7],
    [0.2, 0.7],
  ];
  assert.strictEqual(isConvexQuad(validCardQuad), true, "Convex quad must be recognized");
  assert.strictEqual(areAnglesAcceptable(validCardQuad), true, "Rectangle angles must be 90 deg");
  assert.strictEqual(isWithinMargins(validCardQuad), true, "Quad must be inside 0.015 margin");
  assert(Math.abs(quadArea(validCardQuad) - 0.24) < 1e-5, "Area should be 0.6 * 0.4 = 0.24");

  // Non-convex / folded quad
  const foldedQuad = [
    [0.2, 0.3],
    [0.8, 0.3],
    [0.5, 0.5], // inward fold
    [0.2, 0.7],
  ];
  assert.strictEqual(isConvexQuad(foldedQuad), false, "Folded non-convex quad must be rejected");

  console.log("✓ Test 1: Geometry & Convexity Validation PASSED");
}

// Test 2: Card vs Document Aspect Ratio
{
  const cardQuad = [
    [0.1, 0.2],
    [0.734, 0.2], // width = 0.634, height = 0.4 -> ratio approx 1.585
    [0.734, 0.6],
    [0.1, 0.6],
  ];
  const cardRatio = estimateQuadAspectRatio(cardQuad, 1000, 1000);
  assert(Math.abs(cardRatio - 1.585) < 0.05, `Aspect ratio of card should be ~1.586, got ${cardRatio}`);

  const a4PortraitQuad = [
    [0.2, 0.1],
    [0.6, 0.1], // width = 0.4, height = 0.565 -> ratio ~ 0.707 (1 / 1.414)
    [0.6, 0.665],
    [0.2, 0.665],
  ];
  const a4Ratio = estimateQuadAspectRatio(a4PortraitQuad, 1000, 1000);
  assert(Math.abs(a4Ratio - 0.707) < 0.05, `A4 portrait ratio should be ~0.707, got ${a4Ratio}`);

  console.log("✓ Test 2: Aspect Ratio Estimation PASSED");
}

// Test 3: Homography Projection
{
  const src = [
    [100, 100],
    [500, 100],
    [500, 400],
    [100, 400],
  ];
  const dstW = 400;
  const dstH = 300;
  const H = computeInverseHomography(src, dstW, dstH);
  assert(H !== null, "Homography solution must exist for standard rectangle");

  // Mapping test: top-left (u=0, v=0) -> (x=100, y=100)
  const [m00, m01, m02, m10, m11, m12, m20, m21, m22] = H;
  const x0 = (m01 * 0 + m02) / (m21 * 0 + m22);
  const y0 = (m11 * 0 + m12) / (m21 * 0 + m22);
  assert(Math.abs(x0 - 100) < 0.01, `Expected x0=100, got ${x0}`);
  assert(Math.abs(y0 - 100) < 0.01, `Expected y0=100, got ${y0}`);

  // Mapping test: bottom-right (u=400, v=300) -> (x=500, y=400)
  const invW = 1.0 / (m20 * 400 + m21 * 300 + m22);
  const xBR = (m00 * 400 + m01 * 300 + m02) * invW;
  const yBR = (m10 * 400 + m11 * 300 + m12) * invW;
  assert(Math.abs(xBR - 500) < 0.01, `Expected xBR=500, got ${xBR}`);
  assert(Math.abs(yBR - 400) < 0.01, `Expected yBR=400, got ${yBR}`);

  console.log("✓ Test 3: Homography 4-Point Perspective Warp Mapping PASSED");
}

// Test 4: Temporal Tracking & Jitter Stabilization
{
  let stableCount = 0;
  let prevQuad = null;
  const frames = [
    [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]],
    [[0.201, 0.2], [0.801, 0.2], [0.8, 0.801], [0.2, 0.8]],
    [[0.2, 0.201], [0.8, 0.2], [0.799, 0.8], [0.201, 0.8]],
    [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]],
    [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]],
  ];

  for (const f of frames) {
    if (prevQuad) {
      const c1x = (prevQuad[0][0] + prevQuad[1][0] + prevQuad[2][0] + prevQuad[3][0]) / 4;
      const c1y = (prevQuad[0][1] + prevQuad[1][1] + prevQuad[2][1] + prevQuad[3][1]) / 4;
      const c2x = (f[0][0] + f[1][0] + f[2][0] + f[3][0]) / 4;
      const c2y = (f[0][1] + f[1][1] + f[2][1] + f[3][1]) / 4;
      const d = (c1x - c2x) ** 2 + (c1y - c2y) ** 2;
      if (d < 0.001) stableCount++;
    }
    prevQuad = f;
  }

  assert(stableCount >= 4, "Must reach stable frame threshold under steady hold");
  console.log("✓ Test 4: Temporal Tracking & Jitter Stabilization PASSED");
}

console.log("\nAll Smart Scanner tests PASSED successfully!");
