import type { Quad } from "../types";
import { distSq, quadCentroid, smoothQuad } from "../geometry";

export interface TrackerState {
  currentQuad: Quad | null;
  smoothedQuad: Quad | null;
  stableFrameCount: number;
  missCount: number;
  isStable: boolean;
}

export class TemporalTracker {
  private state: TrackerState = {
    currentQuad: null,
    smoothedQuad: null,
    stableFrameCount: 0,
    missCount: 0,
    isStable: false,
  };

  private readonly minStableFrames: number;
  private readonly maxMissCount: number;
  private readonly jumpThresholdSq: number;
  private readonly stabilityThresholdSq: number;

  constructor(options?: {
    minStableFrames?: number;
    maxMissCount?: number;
    jumpThresholdSq?: number;
    stabilityThresholdSq?: number;
  }) {
    this.minStableFrames = options?.minStableFrames ?? 4;
    this.maxMissCount = options?.maxMissCount ?? 3;
    this.jumpThresholdSq = options?.jumpThresholdSq ?? 0.05; // ~22% screen shift
    this.stabilityThresholdSq = options?.stabilityThresholdSq ?? 0.003; // ~5% screen shift
  }

  public update(candidate: Quad | null): TrackerState {
    if (!candidate) {
      this.state.missCount++;
      if (this.state.missCount >= this.maxMissCount) {
        this.reset();
      } else {
        // Retain last known smoothed quad briefly to avoid momentary flicker
        this.state.isStable = false;
        this.state.stableFrameCount = Math.max(0, this.state.stableFrameCount - 1);
      }
      return { ...this.state };
    }

    const prev = this.state.smoothedQuad;
    if (!prev) {
      // First detection
      this.state.currentQuad = candidate;
      this.state.smoothedQuad = candidate;
      this.state.missCount = 0;
      this.state.stableFrameCount = 1;
      this.state.isStable = false;
      return { ...this.state };
    }

    // Check centroid movement
    const prevC = quadCentroid(prev);
    const candC = quadCentroid(candidate);
    const moveDistSq = distSq(prevC, candC);

    if (moveDistSq > this.jumpThresholdSq) {
      // Sudden large jump: could be false transient detection
      if (this.state.missCount > 0) {
        // Adopt new candidate if we were missing
        this.state.currentQuad = candidate;
        this.state.smoothedQuad = candidate;
        this.state.stableFrameCount = 1;
      } else {
        // Potential outlier, smooth aggressively
        this.state.smoothedQuad = smoothQuad(prev, candidate, 0.85);
        this.state.stableFrameCount = 0;
      }
    } else {
      // Normal motion: apply adaptive smoothing
      // If moving slowly, smooth more (0.75) for rock-solid stability; if moving fast, follow promptly (0.4)
      const alpha = moveDistSq < 0.0005 ? 0.75 : 0.45;
      this.state.smoothedQuad = smoothQuad(prev, candidate, alpha);

      if (moveDistSq < this.stabilityThresholdSq) {
        this.state.stableFrameCount++;
      } else {
        this.state.stableFrameCount = Math.max(1, this.state.stableFrameCount - 1);
      }
    }

    this.state.currentQuad = candidate;
    this.state.missCount = 0;
    this.state.isStable = this.state.stableFrameCount >= this.minStableFrames;

    return { ...this.state };
  }

  public reset(): void {
    this.state = {
      currentQuad: null,
      smoothedQuad: null,
      stableFrameCount: 0,
      missCount: 0,
      isStable: false,
    };
  }

  public getState(): TrackerState {
    return { ...this.state };
  }
}
