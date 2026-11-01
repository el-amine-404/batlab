import type { Point, Quad } from "./types";

export function distSq(p1: Point, p2: Point): number {
  const dx = p1[0] - p2[0];
  const dy = p1[1] - p2[1];
  return dx * dx + dy * dy;
}

export function dist(p1: Point, p2: Point): number {
  return Math.sqrt(distSq(p1, p2));
}

export function crossProduct(o: Point, a: Point, b: Point): number {
  return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
}

/**
 * Calculates quadrilateral area using Shoelace formula (0..1 normalized range)
 */
export function quadArea(quad: Quad): number {
  let area = 0;
  const n = 4;
  for (let i = 0; i < n; i++) {
    const j = (i + 1) % n;
    area += quad[i][0] * quad[j][1];
    area -= quad[j][0] * quad[i][1];
  }
  return Math.abs(area) * 0.5;
}

/**
 * Validates that quadrilateral is strictly convex with reasonable interior angles
 */
export function isConvexQuad(quad: Quad): boolean {
  for (let i = 0; i < 4; i++) {
    const o = quad[i];
    const a = quad[(i + 1) % 4];
    const b = quad[(i + 2) % 4];
    // Cross product must be strictly positive (counter-clockwise) or strictly negative (clockwise)
    const cp = crossProduct(o, a, b);
    if (cp <= 0.0001) {
      return false;
    }
  }
  return true;
}

/**
 * Checks if all interior angles are within acceptable document range (e.g., 40° to 140°)
 */
export function areAnglesAcceptable(quad: Quad, minDeg = 35, maxDeg = 145): boolean {
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

    if (angleDeg < minDeg || angleDeg > maxDeg) {
      return false;
    }
  }
  return true;
}

/**
 * Checks whether all corners are within a safe screen margin
 */
export function isWithinMargins(quad: Quad, margin = 0.015): boolean {
  for (const [x, y] of quad) {
    if (x < margin || x > 1 - margin || y < margin || y > 1 - margin) {
      return false;
    }
  }
  return true;
}

/**
 * Estimates aspect ratio (width / height) in physical projection
 */
export function estimateQuadAspectRatio(quad: Quad, frameWidth = 1, frameHeight = 1): number {
  // Convert normalized to pixel coordinates
  const p = quad.map(([x, y]) => [x * frameWidth, y * frameHeight] as [number, number]);
  const topW = dist(p[0], p[1]);
  const bottomW = dist(p[3], p[2]);
  const leftH = dist(p[0], p[3]);
  const rightH = dist(p[1], p[2]);

  const avgW = (topW + bottomW) / 2;
  const avgH = (leftH + rightH) / 2;

  if (avgH < 1e-5) return 1.0;
  return avgW / avgH;
}

/**
 * Computes centroid of quadrilateral
 */
export function quadCentroid(quad: Quad): Point {
  const cx = (quad[0][0] + quad[1][0] + quad[2][0] + quad[3][0]) / 4;
  const cy = (quad[0][1] + quad[1][1] + quad[2][1] + quad[3][1]) / 4;
  return [cx, cy];
}

/**
 * Smooths quad with exponential moving average
 */
export function smoothQuad(prev: Quad, current: Quad, alpha: number): Quad {
  const inv = 1 - alpha;
  return [
    [prev[0][0] * alpha + current[0][0] * inv, prev[0][1] * alpha + current[0][1] * inv],
    [prev[1][0] * alpha + current[1][0] * inv, prev[1][1] * alpha + current[1][1] * inv],
    [prev[2][0] * alpha + current[2][0] * inv, prev[2][1] * alpha + current[2][1] * inv],
    [prev[3][0] * alpha + current[3][0] * inv, prev[3][1] * alpha + current[3][1] * inv],
  ];
}
