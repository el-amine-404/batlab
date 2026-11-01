import assert from "node:assert";
import { AutoShutter, Tracker } from "../scanner/tracker.ts";

const page = (x = 0.2, y = 0.2) => [[x, y], [x + 0.5, y], [x + 0.5, y + 0.6], [x, y + 0.6]];
const steps = (tracker, shutter, frames, source, stepMs = 100, start = 0, ok = true) => {
  const fired = [];
  let last;
  for (let i = 0; i < frames; i += 1) {
    const now = start + i * stepMs;
    last = tracker.update(source(i), now);
    const answer = shutter.update(last, ok);
    if (answer.fire) fired.push(now);
    last.answer = answer;
  }
  return { last, fired };
};

{ // Held still: fires once, after about 700 ms, whatever the frame rate.
  for (const stepMs of [50, 100, 250]) {
    const { fired } = steps(new Tracker(), new AutoShutter(), Math.ceil(3000 / stepMs), () => page(), stepMs);
    assert.equal(fired.length, 1, `one capture at ${stepMs} ms a frame`);
    assert.ok(fired[0] >= 700 && fired[0] <= 950, `after about 700 ms, was ${fired[0]}`);
  }
}
{ // Moved to a new page: a second capture, not before.
  const tracker = new Tracker(), shutter = new AutoShutter();
  const { fired } = steps(tracker, shutter, 60, (i) => (i < 30 ? page() : page(0.42, 0.3)));
  assert.equal(fired.length, 2);
}
{ // Lost and found again (the page taken away): captures again.
  const tracker = new Tracker(), shutter = new AutoShutter();
  const { fired } = steps(tracker, shutter, 60, (i) => (i < 20 ? page() : i < 35 ? null : page()));
  assert.equal(fired.length, 2);
}
{ // Short drop-outs: the outline stays; a blip of under 200 ms doesn't restart the hold.
  const tracker = new Tracker();
  tracker.update(page(), 0);
  tracker.update(page(), 100);
  assert.ok(tracker.update(null, 300).quad, "held through a miss");
  assert.equal(tracker.update(null, 300).steadyMs, 300);
  assert.equal(new Tracker().update(null, 0).quad, null);
  const gone = new Tracker();
  gone.update(page(), 0);
  assert.equal(gone.update(null, 700).quad, null, "gone after 500 ms");
}
{ // One wild frame doesn't move the outline; two in agreement do.
  const tracker = new Tracker();
  tracker.update(page(), 0);
  tracker.update(page(), 100);
  assert.ok(Math.abs(tracker.update(page(0.45, 0.35), 200).quad[0][0] - 0.2) < 1e-6);
  assert.ok(Math.abs(tracker.update(page(), 300).quad[0][0] - 0.2) < 1e-6);
  tracker.update(page(0.45, 0.35), 400);
  assert.ok(Math.abs(tracker.update(page(0.45, 0.35), 500).quad[0][0] - 0.45) < 1e-6);
}
{ // Sensor noise doesn't reset steadiness or shake the outline.
  const tracker = new Tracker(), shutter = new AutoShutter();
  const noisy = (i) => page(0.2 + (i % 2 ? 0.004 : -0.004), 0.2 + (i % 3 ? 0.003 : -0.003));
  const { fired, last } = steps(tracker, shutter, 15, noisy);
  assert.equal(fired.length, 1);
  assert.ok(Math.abs(last.quad[0][0] - 0.2) < 0.006);
}
{ // Hand moving: no capture.
  const { fired } = steps(new Tracker(), new AutoShutter(), 40, (i) => page(0.1 + i * 0.008));
  assert.equal(fired.length, 0);
}
{ // Bad conditions hold the shutter back.
  const { fired } = steps(new Tracker(), new AutoShutter(), 30, () => page(), 100, 0, false);
  assert.equal(fired.length, 0);
}
{ // Taken by hand: the same page isn't taken again by itself.
  const tracker = new Tracker(), shutter = new AutoShutter();
  shutter.note(page());
  const { fired } = steps(tracker, shutter, 30, () => page());
  assert.equal(fired.length, 0);
}
console.log("tracker: all passed");
