/** The geometry of a crop: pure, so tests can run it. */
type Point = [number, number];

/** Four corners in order, turning one way all round: no crossed sides, no flat one. */
export function isValidQuad(q: Point[]): boolean {
  let sign = 0;
  for (let i = 0; i < 4; i += 1) {
    const [a, b, c] = [q[i], q[(i + 1) % 4], q[(i + 2) % 4]];
    const turn = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]);
    if (Math.abs(turn) < 1e-4) return false;
    if (sign && Math.sign(turn) !== sign) return false;
    sign = Math.sign(turn);
  }
  return true;
}

/** Solves the eight unknowns of the perspective that takes the four src to the four dst. */
function homography(src: Point[], dst: Point[]): number[] {
  const m: number[][] = [];
  for (let i = 0; i < 4; i += 1) {
    const [x, y] = src[i], [u, v] = dst[i];
    m.push([x, y, 1, 0, 0, 0, -x * u, -y * u, u]);
    m.push([0, 0, 0, x, y, 1, -x * v, -y * v, v]);
  }
  for (let c = 0; c < 8; c += 1) {
    let pivot = c;
    for (let r = c + 1; r < 8; r += 1) if (Math.abs(m[r][c]) > Math.abs(m[pivot][c])) pivot = r;
    [m[c], m[pivot]] = [m[pivot], m[c]];
    for (let r = 0; r < 8; r += 1) {
      if (r === c) continue;
      const factor = m[r][c] / m[c][c];
      for (let k = c; k < 9; k += 1) m[r][k] -= factor * m[c][k];
    }
  }
  return m.map((row, i) => row[8] / row[i]);
}

/** The straightened page, about long pixels on its long side: a CSS transform for the photo at its natural size. */
export function previewMatrix(corners: Point[], natural: [number, number], long: number) {
  const px = corners.map(([x, y]) => [x * natural[0], y * natural[1]] as Point);
  const side = (a: number, b: number) => Math.hypot(px[a][0] - px[b][0], px[a][1] - px[b][1]);
  const w = Math.max(side(0, 1), side(3, 2)), h = Math.max(side(0, 3), side(1, 2));
  const scale = long / Math.max(w, h);
  const width = Math.max(1, Math.round(w * scale)), height = Math.max(1, Math.round(h * scale));
  const [a, b, c, d, e, f, g, i] = homography(px, [[0, 0], [width, 0], [width, height], [0, height]]);
  return { width, height, transform: `matrix3d(${a},${d},0,${g},${b},${e},0,${i},0,0,1,0,${c},${f},0,1)` };
}

export type CornerField = { width: number; height: number; response: Float32Array; floor: number };

/** How corner-like each spot of a small grey picture is (Harris): where real corners of a page are. */
export function cornerField(gray: Uint8ClampedArray | number[], width: number, height: number): CornerField {
  const response = new Float32Array(width * height);
  const xx = new Float32Array(width * height), yy = new Float32Array(width * height), xy = new Float32Array(width * height);
  for (let y = 1; y < height - 1; y += 1) {
    for (let x = 1; x < width - 1; x += 1) {
      const at = y * width + x;
      const dx = gray[at + 1] - gray[at - 1], dy = gray[at + width] - gray[at - width];
      xx[at] = dx * dx; yy[at] = dy * dy; xy[at] = dx * dy;
    }
  }
  const R = 2;
  let highest = 0;
  for (let y = R + 1; y < height - R - 1; y += 1) {
    for (let x = R + 1; x < width - R - 1; x += 1) {
      let a = 0, b = 0, c = 0;
      for (let j = -R; j <= R; j += 1) {
        for (let i = -R; i <= R; i += 1) {
          const at = (y + j) * width + x + i;
          a += xx[at]; b += yy[at]; c += xy[at];
        }
      }
      const value = a * b - c * c - 0.05 * (a + b) * (a + b);
      response[y * width + x] = value;
      if (value > highest) highest = value;
    }
  }
  return { width, height, response, floor: highest * 0.02 };
}

/** The strongest real corner within reach of a point (0..1), or the point itself when there is none. */
export function snapToCorner(field: CornerField, point: Point, reach: number): Point {
  const cx = point[0] * field.width, cy = point[1] * field.height;
  let best = field.floor, found: Point | null = null;
  const r = Math.ceil(reach);
  for (let y = Math.max(0, Math.floor(cy - r)); y <= Math.min(field.height - 1, Math.ceil(cy + r)); y += 1) {
    for (let x = Math.max(0, Math.floor(cx - r)); x <= Math.min(field.width - 1, Math.ceil(cx + r)); x += 1) {
      if (Math.hypot(x - cx, y - cy) > reach) continue;
      const value = field.response[y * field.width + x];
      if (value > best) { best = value; found = [x / field.width, y / field.height]; }
    }
  }
  return found ?? point;
}

/** The lines dividing a four-sided page into thirds, as pairs of points: straight lines to judge it by. */
export function thirds(q: Point[]): [Point, Point][] {
  const mix = (a: Point, b: Point, t: number): Point => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
  return [1 / 3, 2 / 3].flatMap((t): [Point, Point][] => [[mix(q[0], q[1], t), mix(q[3], q[2], t)], [mix(q[0], q[3], t), mix(q[1], q[2], t)]]);
}
