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


def detect(image: np.ndarray) -> tuple[np.ndarray, float, str]:
    height, width = image.shape[:2]
    scale = min(1.0, 1400 / max(width, height))
    small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else image
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 45, 140)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[tuple[float, np.ndarray]] = []
    small_area = small.shape[0] * small.shape[1]
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:40]:
        if cv2.contourArea(contour) < small_area * 0.08:
            break
        perimeter = cv2.arcLength(contour, True)
        for epsilon in (0.015, 0.025, 0.04):
            polygon = cv2.approxPolyDP(contour, epsilon * perimeter, True)
            if len(polygon) == 4 and cv2.isContourConvex(polygon):
                quad = order_corners(polygon.reshape(4, 2))
                candidates.append((_score_quad(quad, small.shape[1], small.shape[0]), quad))
                break
    method = "contour"
    if candidates:
        confidence, quad = max(candidates, key=lambda item: item[0])
    elif contours:
        rectangle = cv2.minAreaRect(max(contours, key=cv2.contourArea))
        quad = order_corners(cv2.boxPoints(rectangle))
        confidence = min(0.7, _score_quad(quad, small.shape[1], small.shape[0]))
        method = "minimum-area fallback"
    else:
        inset = max(2, round(min(small.shape[:2]) * 0.01))
        quad = np.array([[inset, inset], [small.shape[1] - inset, inset],
                         [small.shape[1] - inset, small.shape[0] - inset],
                         [inset, small.shape[0] - inset]], np.float32)
        confidence = 0.35
        method = "full-image fallback"
    return quad / scale, float(confidence), method


def normalized_corners(corners: np.ndarray, width: int, height: int) -> list[list[float]]:
    return [[round(float(x / width), 6), round(float(y / height), 6)] for x, y in corners]


def pixel_corners(corners: list[list[float]], width: int, height: int) -> np.ndarray:
    if len(corners) != 4 or any(len(point) != 2 for point in corners):
        raise ImageProblem("Exactly four corner points are required")
    values = np.asarray([[float(x) * width, float(y) * height] for x, y in corners], dtype=np.float32)
    if not np.isfinite(values).all() or (values < 0).any() or (values[:, 0] > width).any() or (values[:, 1] > height).any():
        raise ImageProblem("Corner coordinates must be within the image")
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


def warp(image: np.ndarray, corners: np.ndarray, rotation: int, appearance: str) -> np.ndarray:
    top = np.linalg.norm(corners[1] - corners[0])
    bottom = np.linalg.norm(corners[2] - corners[3])
    left = np.linalg.norm(corners[3] - corners[0])
    right = np.linalg.norm(corners[2] - corners[1])
    width, height = int(round(max(top, bottom))), int(round(max(left, right)))
    if width < 64 or height < 64:
        raise ImageProblem("Corrected page would be too small")
    destination = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], np.float32)
    result = cv2.warpPerspective(image, cv2.getPerspectiveTransform(corners, destination),
                                 (width, height), flags=cv2.INTER_LANCZOS4,
                                 borderMode=cv2.BORDER_REPLICATE)
    if rotation == 90:
        result = cv2.rotate(result, cv2.ROTATE_90_CLOCKWISE)
    elif rotation == 180:
        result = cv2.rotate(result, cv2.ROTATE_180)
    elif rotation == 270:
        result = cv2.rotate(result, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return _appearance(result, appearance)


def quality(image: np.ndarray, confidence: float) -> dict:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
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


def write_jpeg(image: np.ndarray, path: Path, quality_value: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, quality_value,
                                          cv2.IMWRITE_JPEG_OPTIMIZE, 1]):
        raise ImageProblem("Could not write corrected image")
