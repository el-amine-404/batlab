/** The geometry of a crop: pure, so tests can run it. */
type Point = [number, number];

/**
 * Four corners in order, turning clockwise all round: no crossed sides, no flat one, and
 * not turned over either, which a side pushed past its opposite does and which would
 * mirror the page.
 */
export function isValidQuad(q: Point[]): boolean {
  for (let i = 0; i < 4; i += 1) {
    const [a, b, c] = [q[i], q[(i + 1) % 4], q[(i + 2) % 4]];
    if ((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]) < 1e-4) return false;
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

const IN_REACH = (q: Point[]) => q.every(([x, y]) => x >= -0.05 && x <= 1.05 && y >= -0.05 && y <= 1.05);

/** As far along a move as it can go with the page whole and on the photo: it stops, it never breaks. */
function furthest(move: (share: number) => Point[]): Point[] {
  const allowed = (q: Point[]) => IN_REACH(q) && isValidQuad(q);
  if (allowed(move(1))) return move(1);
  if (!allowed(move(0))) return move(0);
  let low = 0, high = 1;
  for (let i = 0; i < 24; i += 1) {
    const middle = (low + high) / 2;
    if (allowed(move(middle))) low = middle; else high = middle;
  }
  return move(low);
}

/**
 * A side moved without turning: straight out or in, its two corners running along their
 * neighbours' lines, so all four sides keep their direction. Pixels matter here, since a
 * photo's 0..1 coordinates are stretched differently across and down.
 */
export function slideSide(q: Point[], side: number, delta: Point, size: [number, number]): Point[] {
  const a = q[side], b = q[(side + 1) % 4], before = q[(side + 3) % 4], after = q[(side + 2) % 4];
  const ux = (b[0] - a[0]) * size[0], uy = (b[1] - a[1]) * size[1];
  const length = Math.hypot(ux, uy);
  if (!length) return q;
  const nx = -uy / length, ny = ux / length;
  const by = delta[0] * size[0] * nx + delta[1] * size[1] * ny;
  /** How far along a neighbour's line the corner goes for each pixel the side moves. */
  const rate = (corner: Point, other: Point) => {
    const vx = corner[0] - other[0], vy = corner[1] - other[1];
    const along = vx * size[0] * nx + vy * size[1] * ny;
    const span = Math.hypot(vx * size[0], vy * size[1]);
    // A neighbour nearly in line with this side can't carry it anywhere.
    return span && Math.abs(along) / span > 0.05 ? [vx / along, vy / along] : null;
  };
  const ra = rate(a, before), rb = rate(b, after);
  if (!ra || !rb) return q;
  return furthest((share) => {
    const t = by * share;
    return q.map((point, index): Point => {
      if (index === side) return [a[0] + ra[0] * t, a[1] + ra[1] * t];
      if (index === (side + 1) % 4) return [b[0] + rb[0] * t, b[1] + rb[1] * t];
      return point;
    });
  });
}

/**
 * A corner moved along one of its two sides, so that side's line stays exactly where it
 * is. The hand chooses: whichever side the drag runs closer to. `along` is the side's
 * number, or null until the drag has gone far enough to tell.
 */
export function slideCorner(q: Point[], corner: number, delta: Point, size: [number, number]): { corners: Point[]; along: number | null } {
  const dx = delta[0] * size[0], dy = delta[1] * size[1];
  if (Math.hypot(dx, dy) < 4) return { corners: q, along: null };
  const options = [{ side: corner, other: q[(corner + 1) % 4] }, { side: (corner + 3) % 4, other: q[(corner + 3) % 4] }].map(({ side, other }) => {
    const vx = (other[0] - q[corner][0]) * size[0], vy = (other[1] - q[corner][1]) * size[1];
    const length = Math.hypot(vx, vy) || 1;
    return { side, other, by: (dx * vx + dy * vy) / length, length };
  });
  const best = Math.abs(options[0].by) >= Math.abs(options[1].by) ? options[0] : options[1];
  const start = q[corner], share = best.by / best.length;
  const corners = furthest((part) => q.map((point, index): Point => index === corner
    ? [start[0] + (best.other[0] - start[0]) * share * part, start[1] + (best.other[1] - start[1]) * share * part]
    : point));
  return { corners, along: best.side };
}

export type EdgeField = { width: number; height: number; gx: Float32Array; gy: Float32Array };

/** How the brightness of a small grey picture changes at each spot, softened so paper grain doesn't count. */
export function edgeField(gray: Uint8ClampedArray | number[], width: number, height: number): EdgeField {
  const soft = new Float32Array(width * height);
  for (let y = 1; y < height - 1; y += 1) {
    for (let x = 1; x < width - 1; x += 1) {
      let sum = 0;
      for (let j = -1; j <= 1; j += 1) for (let i = -1; i <= 1; i += 1) sum += gray[(y + j) * width + x + i];
      soft[y * width + x] = sum / 9;
    }
  }
  const gx = new Float32Array(width * height), gy = new Float32Array(width * height);
  for (let y = 2; y < height - 2; y += 1) {
    for (let x = 2; x < width - 2; x += 1) {
      const at = y * width + x;
      gx[at] = soft[at + 1] - soft[at - 1];
      gy[at] = soft[at + width] - soft[at - width];
    }
  }
  return { width, height, gx, gy };
}

const EDGE_LEVEL = 10; // the least step in brightness that counts as an edge
const EDGE_CAP = 60; // past this it's no more of an edge: a sharp line of print can't outweigh a long side
const EDGE_SHARE = 0.6; // of the side's length, all the same way round: light on one side, dark on the other

/**
 * The real straight edge of the page close to one side, as the whole outline with that
 * side laid along it and the other three lines untouched; null when there's none. Each end
 * of the side may move up to reach pixels of the field, or both by the same when the angle
 * is to be kept. The side's ends aren't read: a finger or a shadow is usually there.
 */
export function magnetSide(field: EdgeField, q: Point[], side: number, reach: number, keepAngle = false): Point[] | null {
  const { width, height, gx, gy } = field;
  const ai = side, bi = (side + 1) % 4;
  const ax = q[ai][0] * width, ay = q[ai][1] * height, bx = q[bi][0] * width, by = q[bi][1] * height;
  const length = Math.hypot(bx - ax, by - ay);
  if (length < 8) return null;
  const nx = -(by - ay) / length, ny = (bx - ax) / length;
  const r = Math.min(48, Math.max(2, Math.ceil(reach)));
  const samples = Math.max(8, Math.min(60, Math.round(length / 3)));
  /** How much this line is an edge, 0 when it isn't one. */
  const score = (d0: number, d1: number, cap: number) => {
    let up = 0, down = 0, upSum = 0, downSum = 0;
    for (let s = 0; s < samples; s += 1) {
      const t = 0.1 + (0.8 * s) / (samples - 1), off = d0 + (d1 - d0) * t;
      const x = Math.round(ax + (bx - ax) * t + nx * off), y = Math.round(ay + (by - ay) * t + ny * off);
      if (x < 0 || y < 0 || x >= width || y >= height) continue;
      const g = gx[y * width + x] * nx + gy[y * width + x] * ny;
      if (g >= EDGE_LEVEL) { up += 1; upSum += Math.min(g, cap); }
      else if (g <= -EDGE_LEVEL) { down += 1; downSum += Math.min(-g, cap); }
    }
    return Math.max(up, down) < samples * EDGE_SHARE ? 0 : up >= down ? upSum : downSum;
  };
  let best = 0, end0 = 0, end1 = 0;
  for (let d0 = -r; d0 <= r; d0 += 1) {
    for (let d1 = keepAngle ? d0 : -r; d1 <= (keepAngle ? d0 : r); d1 += 1) {
      // Between equals, the line closest to where the hand left it.
      const value = score(d0, d1, EDGE_CAP) - (Math.abs(d0) + Math.abs(d1)) * 0.01;
      if (value > best) { best = value; end0 = d0; end1 = d1; }
    }
  }
  if (!best) return null;
  // The cap makes a plateau around a strong edge: now the edge is chosen, find its very crest.
  let crest = 0;
  const [from0, from1] = [end0, end1];
  for (let d0 = from0 - 3; d0 <= from0 + 3; d0 += 1) {
    for (let d1 = keepAngle ? d0 : from1 - 3; d1 <= (keepAngle ? d0 : from1 + 3); d1 += 1) {
      const value = score(d0, d1, Infinity);
      if (value > crest) { crest = value; end0 = d0; end1 = d1; }
    }
  }
  const px = ax + nx * end0, py = ay + ny * end0, qx = bx + nx * end1, qy = by + ny * end1;
  /** Where the found line meets a neighbour's line, in 0..1; null when they barely meet. */
  const meet = (corner: Point, other: Point): Point | null => {
    const cx = corner[0] * width, cy = corner[1] * height, vx = other[0] * width - cx, vy = other[1] * height - cy;
    const lx = qx - px, ly = qy - py, det = vx * ly - vy * lx;
    if (Math.abs(det) < 0.05 * Math.hypot(vx, vy) * Math.hypot(lx, ly)) return null;
    const along = ((px - cx) * ly - (py - cy) * lx) / det;
    return [(cx + vx * along) / width, (cy + vy * along) / height];
  };
  const a = meet(q[ai], q[(side + 3) % 4]), b = meet(q[bi], q[(side + 2) % 4]);
  if (!a || !b) return null;
  const next = q.map((point, index): Point => (index === ai ? a : index === bi ? b : point));
  return IN_REACH(next) && isValidQuad(next) ? next : null;
}
