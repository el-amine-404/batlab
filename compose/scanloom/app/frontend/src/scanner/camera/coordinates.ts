import type { Point } from "../types";

/**
 * Transforms normalized [0..1] camera coordinates into SVG / screen percentages
 * accounting for object-fit: contain or cover if aspect ratios differ.
 */
export function normalizedToSvgPercent(
  point: Point,
  videoAspect?: number,
  containerAspect?: number,
  fit: "contain" | "cover" | "exact" = "exact"
): [number, number] {
  const [nx, ny] = point;

  if (fit === "exact" || !videoAspect || !containerAspect || Math.abs(videoAspect - containerAspect) < 1e-4) {
    return [nx * 100, ny * 100];
  }

  if (fit === "cover") {
    if (containerAspect > videoAspect) {
      // Container is wider: video height is cropped
      const scale = containerAspect / videoAspect;
      const offsetY = (1 - scale) / 2;
      return [nx * 100, (ny * scale + offsetY) * 100];
    } else {
      // Container is taller: video width is cropped
      const scale = videoAspect / containerAspect;
      const offsetX = (1 - scale) / 2;
      return [(nx * scale + offsetX) * 100, ny * 100];
    }
  }

  // contain
  if (containerAspect > videoAspect) {
    // Letterbox on left/right
    const scale = videoAspect / containerAspect;
    const offsetX = (1 - scale) / 2;
    return [(nx * scale + offsetX) * 100, ny * 100];
  } else {
    // Letterbox on top/bottom
    const scale = containerAspect / videoAspect;
    const offsetY = (1 - scale) / 2;
    return [nx * 100, (ny * scale + offsetY) * 100];
  }
}
