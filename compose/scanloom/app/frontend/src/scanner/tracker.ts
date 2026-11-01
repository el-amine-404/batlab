/**
 * What the camera's detections mean over time, measured in milliseconds so a
 * slow phone behaves like a fast one. Pure: no browser in here (tests run it).
 */
import type { Point, Quad } from "./types";

const HOLD_MS = 500;             // a page stays on screen through this many ms of misses
const UNSTEADY_AFTER_MS = 200;   // a miss longer than this restarts the steadiness
const JUMP = 0.2;                // a corner moved this far at once is another thing, or a mistake
const STEADY_RADIUS = 0.022;     // corners within this of where they settled count as still

const distance = (a: Point, b: Point) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const farthest = (a: Quad, b: Quad) => Math.max(...a.map((point, index) => distance(point, b[index])));

export type Tracked = { quad: Quad | null; steadyMs: number };

export class Tracker {
  private shown: Quad | null = null;
  private anchor: Quad | null = null;
  private steadySince = 0;
  private lastSeen = 0;
  private suspect: Quad | null = null;

  update(found: Quad | null, now: number): Tracked {
    if (!found) {
      if (this.shown && now - this.lastSeen > HOLD_MS) this.reset();
      else if (this.shown && now - this.lastSeen > UNSTEADY_AFTER_MS) this.steadySince = now;
      return this.state(now);
    }
    if (!this.shown) {
      this.adopt(found, now);
      return this.state(now);
    }
    const move = farthest(found, this.shown);
    if (move > JUMP && !(this.suspect && farthest(found, this.suspect) < JUMP / 2)) {
      // One odd frame doesn't move the outline; a second that agrees does.
      this.suspect = found;
      return this.state(now);
    }
    this.suspect = null;
    if (move > JUMP) {
      this.adopt(found, now);
      return this.state(now);
    }
    // Follows quickly when moving, hardly at all when nearly still: no jitter, no lag.
    const alpha = Math.min(0.85, Math.max(0.2, move / 0.04));
    const previous = this.shown;
    this.shown = found.map((point, index) => [
      previous[index][0] + (point[0] - previous[index][0]) * alpha,
      previous[index][1] + (point[1] - previous[index][1]) * alpha,
    ]) as Quad;
    this.lastSeen = now;
    if (!this.anchor || farthest(this.shown, this.anchor) > STEADY_RADIUS) {
      this.anchor = this.shown;
      this.steadySince = now;
    }
    return this.state(now);
  }

  reset(): void {
    this.shown = this.anchor = this.suspect = null;
    this.steadySince = this.lastSeen = 0;
  }

  private adopt(found: Quad, now: number) {
    this.shown = this.anchor = found;
    this.steadySince = this.lastSeen = now;
    this.suspect = null;
  }

  private state(now: number): Tracked {
    return { quad: this.shown, steadyMs: this.shown ? now - this.steadySince : 0 };
  }
}

const HOLD_TO_CAPTURE_MS = 700;
const NEW_PAGE_MOVE = 0.15;

/** The shutter that presses itself: once per page, not again until the page changes. */
export class AutoShutter {
  private taken: Quad | null = null;

  /** progress 0..1 while steadying; fire: take the picture now. */
  update(tracked: Tracked, acceptable: boolean): { progress: number; fire: boolean; waitingForNewPage: boolean } {
    if (!tracked.quad) {
      this.taken = null;
      return { progress: 0, fire: false, waitingForNewPage: false };
    }
    if (this.taken) {
      if (farthest(tracked.quad, this.taken) < NEW_PAGE_MOVE) return { progress: 0, fire: false, waitingForNewPage: true };
      this.taken = null;
    }
    if (!acceptable) return { progress: 0, fire: false, waitingForNewPage: false };
    const progress = Math.min(1, tracked.steadyMs / HOLD_TO_CAPTURE_MS);
    if (progress < 1) return { progress, fire: false, waitingForNewPage: false };
    this.taken = tracked.quad;
    return { progress: 1, fire: true, waitingForNewPage: false };
  }

  /** The picture was taken by hand: this page is done too. */
  note(quad: Quad | null): void {
    this.taken = quad;
  }
}
