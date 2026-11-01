import assert from "node:assert";
import { cornerField, edgeField, isValidQuad, magnetSide, slideCorner, slideSide, snapToCorner, thirds } from "../components/scan/quad.ts";

const W = 200, H = 150;
const gray = new Uint8ClampedArray(W * H).fill(40);
for (let y = 30; y < 120; y += 1) for (let x = 50; x < 150; x += 1) gray[y * W + x] = 230;
const field = cornerField(gray, W, H);
const [x, y] = snapToCorner(field, [55 / W, 35 / H], 12);
assert.ok(Math.abs(x * W - 50) <= 2 && Math.abs(y * H - 30) <= 2, `snapped to the corner, was ${x * W},${y * H}`);
assert.deepEqual(snapToCorner(field, [100 / W, 75 / H], 12), [100 / W, 75 / H], "nothing nearby: stays");
assert.deepEqual(snapToCorner(field, [5 / W, 5 / H], 12), [5 / W, 5 / H], "plain background: stays");
assert.ok(isValidQuad([[0, 0], [1, 0], [1, 1], [0, 1]]));
assert.ok(!isValidQuad([[0, 0], [1, 1], [1, 0], [0, 1]]), "crossed");
assert.ok(!isValidQuad([[0, 1], [1, 1], [1, 0], [0, 0]]), "turned over: would mirror the page");
assert.equal(thirds([[0, 0], [1, 0], [1, 1], [0, 1]]).length, 4);

// Angle lock. A tilted page on a photo that isn't square, since 0..1 is stretched.
const SIZE = [4000, 3000];
const page = [[0.2, 0.15], [0.8, 0.2], [0.75, 0.85], [0.15, 0.8]];
const direction = (q, side) => {
  const [a, b] = [q[side], q[(side + 1) % 4]];
  return Math.atan2((b[1] - a[1]) * SIZE[1], (b[0] - a[0]) * SIZE[0]);
};
const close = (a, b, message) => assert.ok(Math.abs(a - b) < 1e-9, `${message}: ${a} vs ${b}`);

for (const side of [0, 1, 2, 3]) {
  const moved = slideSide(page, side, [0.03, 0.04], SIZE);
  assert.notDeepEqual(moved, page, `side ${side} moved`);
  for (const any of [0, 1, 2, 3]) close(direction(moved, any), direction(page, any), `side ${side} drag keeps side ${any}'s angle`);
  assert.ok(isValidQuad(moved));
}
// Dragging along the side, not across it, does nothing.
const a = page[0], b = page[1];
assert.deepEqual(slideSide(page, 0, [(b[0] - a[0]) / 2, (b[1] - a[1]) / 2], SIZE).map((p) => p.map((v) => +v.toFixed(9))), page.map((p) => p.map((v) => +v.toFixed(9))), "a drag along the side stays");
// Too far: it stops whole, on the photo, with the angles kept.
for (const delta of [[0, 5], [0, -5], [5, 0], [-5, 0]]) {
  const far = slideSide(page, 0, delta, SIZE);
  assert.ok(isValidQuad(far), `whole after ${delta}`);
  assert.ok(far.flat().every((v) => v >= -0.05 && v <= 1.05), `on the photo after ${delta}`);
  for (const any of [0, 1, 2, 3]) close(direction(far, any), direction(page, any), `angle kept after ${delta}`);
}
// A neighbour in line with the side leaves nowhere to go, not a jump across the photo.
const flat = [[0.1, 0.1], [0.5, 0.1], [0.9, 0.1001], [0.1, 0.9]];
assert.deepEqual(slideSide(flat, 0, [0, 0.1], SIZE), flat, "no way to move");

// A corner: the side closest to the drag holds its line, the others stay.
const across = slideCorner(page, 0, [0.05, 0], SIZE);
assert.equal(across.along, 0, "a drag across runs along the top");
close(direction(across.corners, 0), direction(page, 0), "the top keeps its angle");
assert.deepEqual(across.corners.slice(1), page.slice(1), "only that corner moves");
const down = slideCorner(page, 0, [0, 0.05], SIZE);
assert.equal(down.along, 3, "a drag down runs along the left");
close(direction(down.corners, 3), direction(page, 3), "the left keeps its angle");
// Outward too: past the corner, the line carries on the same way.
const out = slideCorner(page, 0, [-0.05, 0], SIZE);
assert.equal(out.along, 0);
assert.ok(out.corners[0][0] < page[0][0], "went outward");
close(direction(out.corners, 0), direction(page, 0), "outward keeps the angle");
assert.deepEqual(slideCorner(page, 0, [0.0002, 0], SIZE), { corners: page, along: null }, "too small to tell");
const stopped = slideCorner(page, 0, [5, 0], SIZE);
assert.ok(isValidQuad(stopped.corners), "a corner stops before crossing");

// Edge magnet. A tilted bright page on a dark table, with a dark line of print inside.
const FW = 640, FH = 480;
const lit = new Uint8ClampedArray(FW * FH).fill(50);
const topAt = (x) => 100 + (x - 100) * 0.06, bottomAt = (x) => 400 + (x - 100) * 0.03;
for (let y = 0; y < FH; y += 1) for (let x = 100; x < 540; x += 1) if (y >= topAt(x) && y <= bottomAt(x)) lit[y * FW + x] = 235;
for (let x = 220; x < 300; x += 1) for (let y = 200; y < 203; y += 1) lit[y * FW + x] = 40;
const edges = edgeField(lit, FW, FH);
// Off by 8 pixels at one end and 3 at the other, top side.
const rough = [[100 / FW, 92 / FH], [540 / FW, 129.4 / FH], [540 / FW, 413 / FH], [100 / FW, 400 / FH]];
const laid = magnetSide(edges, rough, 0, 14);
assert.ok(laid, "found the top edge");
assert.ok(Math.abs(laid[0][1] * FH - 100) <= 2 && Math.abs(laid[1][1] * FH - topAt(540)) <= 2, `on the edge, was ${laid[0][1] * FH}, ${laid[1][1] * FH}`);
assert.deepEqual(laid.slice(2), rough.slice(2), "the others stay");
const flatFar = [[100 / FW, 150 / FH], [540 / FW, 150 / FH], [540 / FW, 413 / FH], [100 / FW, 400 / FH]];
assert.equal(magnetSide(edges, flatFar, 0, 8), null, "no edge within reach: no snap, and a short line of print doesn't count");
const blank = edgeField(new Uint8ClampedArray(FW * FH).fill(120), FW, FH);
assert.equal(magnetSide(blank, rough, 0, 14), null, "a plain picture has no edge");
// Angle kept: only moves across, whatever the edge's own tilt.
const parallel = [[100 / FW, 90 / FH], [540 / FW, 116.4 / FH], [540 / FW, 413 / FH], [100 / FW, 400 / FH]];
const kept = magnetSide(edges, parallel, 0, 14, true);
assert.ok(kept, "found it, angle kept");
close(Math.atan2((kept[1][1] - kept[0][1]) * FH, (kept[1][0] - kept[0][0]) * FW), Math.atan2((parallel[1][1] - parallel[0][1]) * FH, (parallel[1][0] - parallel[0][0]) * FW), "the side didn't turn");
console.log("quad: all passed");
