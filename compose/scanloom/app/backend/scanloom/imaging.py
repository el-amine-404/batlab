from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
import pyvips


class ImageProblem(ValueError):
    pass


def normalize(source: Path, destination: Path, maximum_pixels: int) -> tuple[int, int]:
    """Decode through libvips and write a fresh metadata-free, single-frame PNG."""
    image = pyvips.Image.new_from_file(str(source), access="sequential", fail_on="error")
    pixels = image.width * image.height
    if image.width < 64 or image.height < 64:
        raise ImageProblem("Image is too small to contain a document")
    if pixels > maximum_pixels:
        raise ImageProblem(f"Image has {pixels:,} pixels; the configured limit is {maximum_pixels:,}")
    if image.bands > 4:
        raise ImageProblem("Image has an unsupported number of color channels")
    image = image.autorot()
    if image.hasalpha():
        image = image.flatten(background=[255, 255, 255])
    if image.bands == 1:
        image = image.colourspace("srgb")
    elif image.bands != 3:
        image = image[:3]
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.write_to_file(str(destination), strip=True, compression=6, interlace=False)
    return image.width, image.height


def order_corners(points: np.ndarray) -> np.ndarray:
    points = np.asarray(points, dtype=np.float32).reshape(4, 2)
    ordered = np.zeros((4, 2), dtype=np.float32)
    total = points.sum(axis=1)
    difference = np.diff(points, axis=1).ravel()
    ordered[0] = points[np.argmin(total)]       # top-left
    ordered[2] = points[np.argmax(total)]       # bottom-right
    ordered[1] = points[np.argmin(difference)]  # top-right
    ordered[3] = points[np.argmax(difference)]  # bottom-left
    return ordered


def _score_quad(quad: np.ndarray, width: int, height: int) -> float:
    area = abs(cv2.contourArea(quad.astype(np.float32)))
    ratio = min(1.0, area / max(1.0, width * height))
    sides = [np.linalg.norm(quad[(index + 1) % 4] - quad[index]) for index in range(4)]
    if min(sides) < 20:
        return 0.0
    opposite = min(sides[0], sides[2]) / max(sides[0], sides[2])
    opposite *= min(sides[1], sides[3]) / max(sides[1], sides[3])
    return float(min(0.99, 0.35 + ratio * 0.55 + opposite * 0.18))


def _intersection(horizontal: np.ndarray, vertical: np.ndarray) -> list[float] | None:
    """Intersect y=ax+b with x=cy+d, returning None for parallel fits."""
    a, b = horizontal
    c, d = vertical
    denominator = 1 - a * c
    if abs(denominator) < 1e-6:
        return None
    x = (c * b + d) / denominator
    return [float(x), float(a * x + b)]


def _robust_fit(points: np.ndarray, direction: str) -> np.ndarray:
    """Iteratively down-weight rounded corners, shadows, and isolated specks."""
    independent, dependent = ((points[:, 0], points[:, 1]) if direction == "horizontal"
                              else (points[:, 1], points[:, 0]))
    weights = np.ones(independent.shape, np.float64)
    coefficients = np.zeros(2, np.float64)
    for _ in range(9):
        coefficients = np.polyfit(independent, dependent, 1, w=np.sqrt(weights))
        errors = dependent - np.polyval(coefficients, independent)
        scale = max(1.0, 1.4826 * np.median(np.abs(errors - np.median(errors))))
        residual = np.abs(errors) / (2.5 * scale)
        weights = np.ones_like(residual)
        np.divide(1.0, residual, out=weights, where=residual > 1)
    return coefficients


def _foreground_quad(gray: np.ndarray, robust: bool = False) -> np.ndarray | None:
    """Fit the outer sides of a bright document on a darker background.

    Fitting the straight middle of each side avoids mistaking a card's rounded
    corners or a strong internal color boundary for the document boundary.
    """
    height, width = gray.shape
    mask = cv2.inRange(gray, 115, 255)
    kernel = max(5, (min(height, width) // 60) | 1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((kernel, kernel), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    contours = [item for item in contours if cv2.contourArea(item) >= 0.04 * height * width]
    if not contours:
        return None

    contour = max(contours, key=cv2.contourArea)
    x, y, box_width, box_height = cv2.boundingRect(contour)
    if box_width < 60 or box_height < 60:
        return None
    points = contour[:, 0, :].astype(np.float64)
    regions = {
        "top": ((points[:, 0] > x + .08 * box_width) & (points[:, 0] < x + .88 * box_width)
                & (points[:, 1] < y + .27 * box_height)),
        "bottom": ((points[:, 0] > x + .12 * box_width) & (points[:, 0] < x + .88 * box_width)
                   & (points[:, 1] > y + .82 * box_height)),
        "left": ((points[:, 0] < x + .07 * box_width)
                 & (points[:, 1] > y + .22 * box_height) & (points[:, 1] < y + .78 * box_height)),
        "right": ((points[:, 0] > x + .96 * box_width)
                  & (points[:, 1] > y + .20 * box_height) & (points[:, 1] < y + .82 * box_height)),
    }
    minimum_points = max(12, min(height, width) // 45)
    if any(np.count_nonzero(selection) < minimum_points for selection in regions.values()):
        return None

    fit = _robust_fit if robust else lambda selected, direction: np.polyfit(
        selected[:, 0] if direction == "horizontal" else selected[:, 1],
        selected[:, 1] if direction == "horizontal" else selected[:, 0], 1)
    top = fit(points[regions["top"]], "horizontal")
    bottom = fit(points[regions["bottom"]], "horizontal")
    left = fit(points[regions["left"]], "vertical")
    right = fit(points[regions["right"]], "vertical")
    corners = [_intersection(top, left), _intersection(top, right),
               _intersection(bottom, right), _intersection(bottom, left)]
    if any(point is None for point in corners):
        return None
    quad = np.asarray(corners, dtype=np.float32)
    margin_x, margin_y = width * .05, height * .05
    if ((quad[:, 0] < -margin_x).any() or (quad[:, 0] > width + margin_x).any()
            or (quad[:, 1] < -margin_y).any() or (quad[:, 1] > height + margin_y).any()):
        return None
    return order_corners(quad)


def detect(image: np.ndarray) -> tuple[np.ndarray, float, str]:
    height, width = image.shape[:2]
    full_gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # Fit clean high-contrast boundaries at source resolution. This preserves
    # thin text and matches the final warp more precisely than a reduced copy.
    foreground = _foreground_quad(full_gray)
    foreground_method = "full-resolution foreground"
    if foreground is None:
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        green = cv2.inRange(hsv, (35, 60, 60), (95, 255, 255))
        combined = cv2.bitwise_or(full_gray, cv2.inRange(green, 1, 255))
        foreground = _foreground_quad(combined, robust=True)
        foreground_method = "robust color foreground"
    scale = min(1.0, 1400 / max(width, height))
    small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else image
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 45, 140)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[tuple[float, np.ndarray, str]] = []
    if foreground is not None:
        # A complete external silhouette is stronger evidence than internal
        # printed lines, while the capped bonus keeps cluttered scenes safe.
        candidates.append((min(.99, _score_quad(foreground, width, height) + .16),
                           foreground, foreground_method))
    small_area = small.shape[0] * small.shape[1]
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:40]:
        if cv2.contourArea(contour) < small_area * 0.08:
            break
        perimeter = cv2.arcLength(contour, True)
        for epsilon in (0.015, 0.025, 0.04):
            polygon = cv2.approxPolyDP(contour, epsilon * perimeter, True)
            if len(polygon) == 4 and cv2.isContourConvex(polygon):
                quad = order_corners(polygon.reshape(4, 2))
                candidates.append((_score_quad(quad, small.shape[1], small.shape[0]), quad / scale, "contour"))
                break
    if candidates:
        confidence, quad, method = max(candidates, key=lambda item: item[0])
    elif contours:
        rectangle = cv2.minAreaRect(max(contours, key=cv2.contourArea))
        quad = order_corners(cv2.boxPoints(rectangle))
        confidence = min(0.7, _score_quad(quad, small.shape[1], small.shape[0]))
        quad = quad / scale
        method = "minimum-area fallback"
    else:
        inset = max(2, round(min(small.shape[:2]) * 0.01))
        quad = np.array([[inset, inset], [small.shape[1] - inset, inset],
                         [small.shape[1] - inset, small.shape[0] - inset],
                         [inset, small.shape[0] - inset]], np.float32) / scale
        confidence = 0.35
        method = "full-image fallback"
    return quad, float(confidence), method


def normalized_corners(corners: np.ndarray, width: int, height: int) -> list[list[float]]:
    return [[round(float(x / width), 6), round(float(y / height), 6)] for x, y in corners]


def pixel_corners(corners: list[list[float]], width: int, height: int) -> np.ndarray:
    if len(corners) != 4 or any(len(point) != 2 for point in corners):
        raise ImageProblem("Exactly four corner points are required")
    values = np.asarray([[float(x) * width, float(y) * height] for x, y in corners], dtype=np.float32)
    if (not np.isfinite(values).all() or (values[:, 0] < -width * .05).any()
            or (values[:, 1] < -height * .05).any() or (values[:, 0] > width * 1.05).any()
            or (values[:, 1] > height * 1.05).any()):
        raise ImageProblem("Corner coordinates may extend at most 5% beyond the image")
    ordered = order_corners(values)
    if abs(cv2.contourArea(ordered)) < width * height * 0.01:
        raise ImageProblem("Crop is too small")
    return ordered


def _appearance(image: np.ndarray, mode: str) -> np.ndarray:
    if mode in {"auto", "color"}:
        return image
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if mode == "gray":
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    mono = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                 cv2.THRESH_BINARY, 31, 15)
    return cv2.cvtColor(mono, cv2.COLOR_GRAY2BGR)


def warp(image: np.ndarray, corners: np.ndarray, rotation: int, appearance: str,
         corner_radius: float = 0) -> np.ndarray:
    top = np.linalg.norm(corners[1] - corners[0])
    bottom = np.linalg.norm(corners[2] - corners[3])
    left = np.linalg.norm(corners[3] - corners[0])
    right = np.linalg.norm(corners[2] - corners[1])
    width, height = int(round(max(top, bottom))), int(round(max(left, right)))
    if width < 64 or height < 64:
        raise ImageProblem("Corrected page would be too small")
    destination = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], np.float32)
    result = cv2.warpPerspective(image, cv2.getPerspectiveTransform(corners, destination),
                                 (width, height), flags=cv2.INTER_CUBIC,
                                 borderMode=cv2.BORDER_REPLICATE)
    if rotation == 90:
        result = cv2.rotate(result, cv2.ROTATE_90_CLOCKWISE)
    elif rotation == 180:
        result = cv2.rotate(result, cv2.ROTATE_180)
    elif rotation == 270:
        result = cv2.rotate(result, cv2.ROTATE_90_COUNTERCLOCKWISE)
    result = _appearance(result, appearance)
    if corner_radius > 0:
        radius = int(round(min(result.shape[:2]) * min(.25, max(0, corner_radius))))
        alpha = np.zeros(result.shape[:2], np.uint8)
        cv2.rectangle(alpha, (radius, 0), (result.shape[1] - radius - 1, result.shape[0] - 1), 255, -1)
        cv2.rectangle(alpha, (0, radius), (result.shape[1] - 1, result.shape[0] - radius - 1), 255, -1)
        for center in ((radius, radius), (result.shape[1] - radius - 1, radius),
                       (result.shape[1] - radius - 1, result.shape[0] - radius - 1),
                       (radius, result.shape[0] - radius - 1)):
            cv2.circle(alpha, center, radius, 255, -1)
        result = cv2.cvtColor(result, cv2.COLOR_BGR2BGRA)
        result[:, :, 3] = alpha
    return result


def quality(image: np.ndarray, confidence: float) -> dict:
    color = image[:, :, :3]
    gray = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    hsv = cv2.cvtColor(color, cv2.COLOR_BGR2HSV)
    glare = float(np.mean((hsv[:, :, 2] > 248) & (hsv[:, :, 1] < 35)))
    warnings = []
    if blur < 45:
        warnings.append("The photo is blurry; small text may not be recoverable.")
    if glare > 0.025:
        warnings.append("Strong glare may hide part of the document.")
    if min(image.shape[:2]) < 700:
        warnings.append("The corrected page has limited resolution.")
    if confidence < 0.72:
        warnings.append("Edge detection was uncertain; review the crop if needed.")
    return {"blur_score": round(blur, 2), "glare_fraction": round(glare, 5),
            "width": image.shape[1], "height": image.shape[0], "warnings": warnings}


def write_png(image: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 6]):
        raise ImageProblem("Could not write corrected image")
