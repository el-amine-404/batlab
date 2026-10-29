from __future__ import annotations

import math
import os
import subprocess
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
import pyvips


class ImageProblem(ValueError):
    pass


_BOUNDARY_SESSION: ort.InferenceSession | None = None


def _boundary_session() -> ort.InferenceSession | None:
    """Load the pinned DocAligner ONNX model once per worker process."""
    global _BOUNDARY_SESSION
    if _BOUNDARY_SESSION is not None:
        return _BOUNDARY_SESSION
    model = Path(os.environ.get("SCANLOOM_BOUNDARY_MODEL",
                                "/opt/scanloom/models/docaligner.onnx"))
    if not model.is_file():
        return None
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    _BOUNDARY_SESSION = ort.InferenceSession(
        str(model), sess_options=options, providers=["CPUExecutionProvider"])
    return _BOUNDARY_SESSION


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


def _background_edge_penalty(quad: np.ndarray, width: int, height: int) -> float:
    """Penalize a background region that owns one frame edge but not the photo.

    Bright walls behind dark cards commonly form a large three-corner polygon
    along the left/right frame. A real full-page scan may touch every frame
    edge, so complete near-full-image quadrilaterals remain valid.
    """
    span_x, span_y = np.ptp(quad[:, 0]), np.ptp(quad[:, 1])
    if span_x > width * .9 and span_y > height * .9:
        return 0.0
    near_left = np.count_nonzero(quad[:, 0] < width * .015)
    near_right = np.count_nonzero(quad[:, 0] > width * .985)
    near_top = np.count_nonzero(quad[:, 1] < height * .015)
    near_bottom = np.count_nonzero(quad[:, 1] > height * .985)
    return .35 if max(near_left, near_right, near_top, near_bottom) >= 2 else 0.0


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


def _neural_boundary(image: np.ndarray) -> tuple[np.ndarray, float] | None:
    """Predict four physical document corners with DocAligner's heatmap model.

    A small black pad lets the model estimate corners at, or just beyond, the
    photograph boundary. The model is fixed, local and never receives network
    access at runtime.
    """
    session = _boundary_session()
    if session is None:
        return None
    height, width = image.shape[:2]

    def infer(crop: np.ndarray, offset: tuple[int, int]):
        crop_height, crop_width = crop.shape[:2]
        pad = round(max(crop_height, crop_width) * .07)
        padded = cv2.copyMakeBorder(crop, pad, pad, pad, pad, cv2.BORDER_CONSTANT,
                                    value=(0, 0, 0))
        reduced = cv2.resize(padded, (256, 256), interpolation=cv2.INTER_AREA)
        tensor = reduced.transpose(2, 0, 1).astype(np.float32)[None] / 255.0
        try:
            heatmaps = session.run(["heatmap"], {"img": tensor})[0][0]
        except Exception:
            return None
        corners, strengths = [], []
        target_size = (crop_width + 2 * pad, crop_height + 2 * pad)
        for heatmap in heatmaps:
            expanded = cv2.resize(heatmap, target_size, interpolation=cv2.INTER_LINEAR)
            mask = np.uint8(expanded >= .30)
            count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
            if count <= 1:
                return None
            component = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
            corners.append(centroids[component] - pad + offset)
            strengths.append(float(expanded[labels == component].max()))
        quad = order_corners(np.asarray(corners, np.float32))
        area = abs(cv2.contourArea(quad)) / max(1, width * height)
        if area < .015 or not np.isfinite(quad).all():
            return None
        confidence = min(min(strengths), .72 + min(.24, area * .35))
        return quad, float(confidence)

    variants = [infer(image, (0, 0))]
    if height != width:
        side = min(height, width)
        left, top = (width - side) // 2, (height - side) // 2
        variants.append(infer(image[top:top+side, left:left+side], (left, top)))
    valid = [item for item in variants if item is not None]
    return max(valid, key=lambda item: item[1]) if valid else None


def _grabcut_boundary(image: np.ndarray) -> tuple[np.ndarray, float] | None:
    """Segment a centered foreground object when the neural model abstains."""
    height, width = image.shape[:2]
    scale = min(1.0, 1000 / max(height, width))
    small = (cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
             if scale < 1 else image)
    sh, sw = small.shape[:2]
    mask = np.full((sh, sw), cv2.GC_PR_BGD, np.uint8)
    border_y, border_x = max(3, sh // 50), max(3, sw // 50)
    mask[:border_y] = mask[-border_y:] = cv2.GC_BGD
    mask[:, :border_x] = mask[:, -border_x:] = cv2.GC_BGD
    cx, cy = sw // 2, sh // 2
    seed_x, seed_y = max(8, sw // 18), max(8, sh // 18)
    mask[cy-seed_y:cy+seed_y, cx-seed_x:cx+seed_x] = cv2.GC_FGD
    background, foreground = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.grabCut(small, mask, None, background, foreground, 4, cv2.GC_INIT_WITH_MASK)
    binary = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD),
                      255, 0).astype(np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE,
                              np.ones((11, 11), np.uint8), iterations=2)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    containing = [item for item in contours
                  if cv2.pointPolygonTest(item, (cx, cy), False) >= 0]
    if not (containing or contours):
        return None
    contour = max(containing or contours, key=cv2.contourArea)
    area = cv2.contourArea(contour) / max(1, sh * sw)
    if not .025 <= area <= .82:
        return None
    hull = cv2.convexHull(contour)
    perimeter = cv2.arcLength(hull, True)
    quad = None
    for epsilon in (.01, .02, .03, .04, .06, .08):
        polygon = cv2.approxPolyDP(hull, epsilon * perimeter, True)
        if len(polygon) == 4 and cv2.isContourConvex(polygon):
            quad = polygon[:, 0, :]
            break
    if quad is None:
        quad = cv2.boxPoints(cv2.minAreaRect(hull))
    return order_corners(quad) / scale, float(min(.84, .58 + area * .45))


def _edge_support(image: np.ndarray, quad: np.ndarray) -> float:
    scale = min(1.0, 1200 / max(image.shape[:2]))
    small = (cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
             if scale < 1 else image)
    edges = cv2.Canny(cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY),
                                       (5, 5), 0), 35, 120)
    distance = cv2.distanceTransform(cv2.bitwise_not(edges), cv2.DIST_L2, 3)
    samples = []
    for start, end in zip(quad * scale, np.roll(quad * scale, -1, axis=0)):
        for ratio in np.linspace(.04, .96, 80):
            point = start * (1 - ratio) + end * ratio
            x = int(np.clip(point[0], 0, edges.shape[1] - 1))
            y = int(np.clip(point[1], 0, edges.shape[0] - 1))
            samples.append(distance[y, x] < 3.5)
    return float(np.mean(samples))


def _edge_boundary(image: np.ndarray) -> tuple[np.ndarray, float] | None:
    """Find a small, sharply bounded face when segmentation spans its support."""
    scale = min(1.0, 1200 / max(image.shape[:2]))
    small = (cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
             if scale < 1 else image)
    height, width = small.shape[:2]
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    raw_edges = cv2.Canny(gray, 35, 120)
    candidates = []
    masks = [cv2.morphologyEx(raw_edges, cv2.MORPH_CLOSE,
                              np.ones((size, size), np.uint8), iterations=2)
             for size in (3, 7, 13, 21)]
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    masks.extend((otsu, cv2.bitwise_not(otsu)))
    for mask in masks:
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:80]:
            if cv2.contourArea(contour) < height * width * .008:
                break
            hull = cv2.convexHull(contour)
            perimeter = cv2.arcLength(hull, True)
            for epsilon in (.008, .012, .018, .025, .04, .06):
                polygon = cv2.approxPolyDP(hull, epsilon * perimeter, True)
                if len(polygon) != 4 or not cv2.isContourConvex(polygon):
                    continue
                quad = order_corners(polygon[:, 0, :])
                area = abs(cv2.contourArea(quad)) / max(1, width * height)
                if not .012 <= area <= .75:
                    break
                support = _edge_support(small, quad)
                center = np.mean(quad, axis=0)
                centrality = 1 - min(1, np.linalg.norm(
                    (center - [width / 2, height / 2]) / [width, height]) * 2)
                score = support * 4 + math.sqrt(area) * 1.5 + centrality * .5
                candidates.append((score, quad, support))
                break
    if not candidates:
        return None
    _, quad, support = max(candidates, key=lambda item: item[0])
    if support < .62:
        return None
    return quad / scale, float(min(.86, .58 + support * .28))


def _detect_classical(image: np.ndarray) -> tuple[np.ndarray, float, str]:
    height, width = image.shape[:2]
    full_gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    # Fit clean high-contrast boundaries at source resolution. This preserves
    # thin text and matches the final warp more precisely than a reduced copy.
    foreground = _foreground_quad(full_gray)
    foreground_method = "full-resolution foreground"
    if foreground is None:
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
        candidates.append((min(.99, _score_quad(foreground, width, height) + .16)
                           - _background_edge_penalty(foreground, width, height),
                           foreground, foreground_method))
    # A saturated blue/green card can be darker than the wall or hand behind
    # it. Its connected color surface is better evidence than the bright
    # background and remains a deterministic OpenCV fallback (no AI content).
    color_mask = cv2.inRange(hsv, (45, 70, 15), (145, 255, 255))
    color_foreground = _foreground_quad(color_mask, robust=True)
    if color_foreground is not None:
        candidates.append((min(.99, _score_quad(color_foreground, width, height) + .18),
                           color_foreground, "color foreground"))
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


def detect(image: np.ndarray) -> tuple[np.ndarray, float, str]:
    """Detect the page with neural corners, then deterministic local fallbacks."""
    neural = _neural_boundary(image)
    if neural is not None:
        quad, confidence = neural
        return quad, confidence, "DocAligner neural boundary"
    classical = _detect_classical(image)
    classical_area = abs(cv2.contourArea(classical[0])) / max(1, image.shape[0] * image.shape[1])
    # A clean full-resolution light foreground is already a strong result.
    if (classical[2] == "full-resolution foreground" and classical[1] >= .78
            and .03 <= classical_area <= .88):
        return classical
    # Small, saturated package faces can be more precise than a broad
    # foreground segmentation (for example a barcode face sitting on a bag).
    if (classical[2] == "color foreground" and classical[1] >= .85
            and .01 <= classical_area <= .12):
        return classical
    segmented = _grabcut_boundary(image)
    if segmented is not None:
        quad, confidence = segmented
        if _edge_support(image, quad) < .40:
            edged = _edge_boundary(image)
            if edged is not None:
                edge_quad, edge_confidence = edged
                return edge_quad, edge_confidence, "quadrilateral edge fallback"
        return quad, confidence, "GrabCut foreground fallback"
    return classical


def detect_orientation(image: np.ndarray) -> tuple[int, float]:
    """Return a conservative clockwise correction inferred from recognized text.

    Tesseract is invoked with fixed arguments and a hard timeout. Ambiguous
    photos remain unchanged; a wrong automatic rotation is worse than a review
    badge and the existing one-click rotation control.
    """
    maximum = 1400
    scale = min(1.0, maximum / max(image.shape[:2]))
    base = (cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            if scale < 1 else image)
    variants = [base, cv2.rotate(base, cv2.ROTATE_90_COUNTERCLOCKWISE),
                cv2.rotate(base, cv2.ROTATE_180), cv2.rotate(base, cv2.ROTATE_90_CLOCKWISE)]
    scores = []
    for variant in variants:
        ok, encoded = cv2.imencode(".png", variant)
        if not ok:
            return 0, 0.0
        try:
            result = subprocess.run(
                ["tesseract", "stdin", "stdout", "-l", "eng", "--psm", "11", "tsv"],
                input=encoded.tobytes(), capture_output=True, check=False, timeout=12)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return 0, 0.0
        score = 0.0
        for line in result.stdout.decode("utf-8", "ignore").splitlines()[1:]:
            fields = line.split("\t")
            if len(fields) < 12:
                continue
            text = "".join(character for character in fields[11] if character.isalnum())
            try:
                confidence = float(fields[10])
            except ValueError:
                continue
            if confidence >= 45 and len(text) >= 2:
                score += confidence * min(12, len(text))
        scores.append(score)
    ranking = sorted(range(4), key=lambda index: scores[index], reverse=True)
    best, runner_up = ranking[:2]
    ratio = scores[best] / max(1, scores[runner_up])
    if (best == 0 or scores[best] < 1200 or ratio < 1.55
            or (ratio < 1.75 and scores[best] < 5000)):
        return 0, 0.0
    return (0, 270, 180, 90)[best], round(ratio, 3)


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
