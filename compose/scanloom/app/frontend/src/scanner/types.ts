export type Point = [number, number]; // [x, y] in 0..1 normalized coordinates
export type Quad = [Point, Point, Point, Point]; // [TL, TR, BR, BL]

export type CaptureMode = "auto" | "manual";
export type DetectionProfile = "auto" | "card" | "document";

export type ScannerStatus =
  | "idle"
  | "requesting_camera"
  | "camera_error"
  | "searching"
  | "candidate_found"
  | "tracking"
  | "ready"
  | "auto_countdown"
  | "capturing"
  | "processing"
  | "adjust_crop"
  | "completed";

export interface QualityMetrics {
  blurScore: number; // 0 (very blurry) to 1 (sharp)
  glareScore: number; // 0 (no glare) to 1 (severe glare)
  exposureScore: number; // 0 (underexposed) .. 0.5 (optimal) .. 1 (overexposed)
  areaFraction: number; // fraction of frame area (0..1)
  isWithinBounds: boolean; // all corners within safe margin
  isGeometryValid: boolean; // convex, proper angles and aspect ratio
  isStable: boolean; // temporally consistent
  overallQuality: "good" | "warning" | "poor";
  warningMessage: string | null;
}

export interface DetectionResult {
  detected: boolean;
  quad: Quad | null;
  confidence: number;
  cornerConfidences: [number, number, number, number];
  quality?: QualityMetrics;
}

export interface CameraDevice {
  deviceId: string;
  label: string;
}

export interface PerspectiveWarpOptions {
  outputWidth?: number;
  outputHeight?: number;
  targetAspectRatio?: number; // e.g. 1.586 for cards, 1.414 for A4
}

export interface ProcessedScan {
  originalBlob: Blob;
  enhancedBlob: Blob;
  previewUrl: string;
  quad: Quad;
  width: number;
  height: number;
  format: "image/jpeg" | "image/png";
}
