export type Point = [number, number]; // 0..1 of the frame
export type Quad = [Point, Point, Point, Point]; // top left, top right, bottom right, bottom left

export interface QualityMetrics {
  blurScore: number;
  glareScore: number;
  exposureScore: number;
  areaFraction: number;
  isWithinBounds: boolean;
  isGeometryValid: boolean;
  isStable: boolean;
  overallQuality: "good" | "warning" | "poor";
  warningMessage: string | null;
}

export interface DetectionResult {
  detected: boolean;
  quad: Quad | null;
  confidence: number;
  quality?: QualityMetrics;
  ms: number;
}
