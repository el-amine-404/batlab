import assert from "node:assert";
import { cornerField, isValidQuad, snapToCorner, thirds } from "../components/scan/quad.ts";

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
assert.equal(thirds([[0, 0], [1, 0], [1, 1], [0, 1]]).length, 4);
console.log("quad: all passed");
