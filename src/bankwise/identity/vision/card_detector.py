from dataclasses import dataclass
from typing import List, Optional

import cv2
import numpy as np

from bankwise.core.config import VisionConfig
from bankwise.identity.vision.perspective import order_points, quad_dimensions
from bankwise.identity.vision.schemas import CardDetection

PAD = 0
FULL_FRAME_RATIO = 0.97
FULL_FRAME_PENALTY = 0.7
MIN_LINE_SIDES = 3
APPROX_FACTORS = (0.02, 0.03, 0.05)
MAX_CONTOURS = 8


@dataclass
class Candidate:
    corners: np.ndarray
    confidence: float
    method: str
    area_ratio: float
    aspect_ratio: float


def prepare(image: np.ndarray, cfg: VisionConfig) -> tuple[np.ndarray, float]:
    height, width = image.shape[:2]
    scale = cfg.detection_side / max(height, width)
    small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    padded = cv2.copyMakeBorder(
        small, PAD, PAD, PAD, PAD, cv2.BORDER_CONSTANT, value=(0, 0, 0)
    )
    return padded, scale


def edge_mask(gray: np.ndarray) -> np.ndarray:
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    median = float(np.median(blurred))
    lower = int(max(0, 0.66 * median))
    upper = int(min(255, 1.33 * median))
    edges = cv2.Canny(blurred, lower, upper)
    kernel = np.ones((5, 5), np.uint8)
    edges = cv2.dilate(edges, kernel, iterations=1)
    return cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)


def threshold_masks(gray: np.ndarray) -> List[np.ndarray]:
    blurred = cv2.GaussianBlur(gray, (7, 7), 0)
    _, mask = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = np.ones((9, 9), np.uint8)
    masks = []
    for variant in (mask, 255 - mask):
        cleaned = cv2.morphologyEx(variant, cv2.MORPH_CLOSE, kernel, iterations=2)
        cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_OPEN, kernel, iterations=1)
        masks.append(cleaned)
    return masks


def grabcut_mask(small: np.ndarray) -> np.ndarray:
    height, width = small.shape[:2]
    mask = np.zeros((height, width), np.uint8)
    margin_x = int(width * 0.1)
    margin_y = int(height * 0.1)
    rect = (margin_x, margin_y, width - 2 * margin_x, height - 2 * margin_y)
    background = np.zeros((1, 65), np.float64)
    foreground = np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(small, mask, rect, background, foreground, 4, cv2.GC_INIT_WITH_RECT)
    except cv2.error:
        return np.zeros((height, width), np.uint8)
    result = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    kernel = np.ones((7, 7), np.uint8)
    result = cv2.morphologyEx(result, cv2.MORPH_CLOSE, kernel, iterations=2)
    return cv2.morphologyEx(result, cv2.MORPH_OPEN, kernel, iterations=1)


def edge_support(quad: np.ndarray, edges: np.ndarray) -> float:
    height, width = edges.shape[:2]
    ordered = order_points(quad)
    supports = []
    for index in range(4):
        start = ordered[index]
        end = ordered[(index + 1) % 4]
        hits = 0.0
        samples = 40
        for step in range(samples):
            point = start + (end - start) * (step + 0.5) / samples
            x = int(round(point[0]))
            y = int(round(point[1]))
            if x <= 2 or y <= 2 or x >= width - 3 or y >= height - 3:
                hits += 0.25
                continue
            if edges[y, x] > 0:
                hits += 1.0
        supports.append(hits / samples)
    return float(np.mean(supports))


def line_intersection(first: np.ndarray, second: np.ndarray) -> Optional[np.ndarray]:
    x1, y1, x2, y2 = first
    x3, y3, x4, y4 = second
    denominator = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denominator) < 1e-6:
        return None
    determinant_a = x1 * y2 - y1 * x2
    determinant_b = x3 * y4 - y3 * x4
    x = (determinant_a * (x3 - x4) - (x1 - x2) * determinant_b) / denominator
    y = (determinant_a * (y3 - y4) - (y1 - y2) * determinant_b) / denominator
    return np.array([x, y], dtype=np.float32)


def band_lines(gray: np.ndarray) -> dict:
    height, width = gray.shape[:2]
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 40, 120)
    raw = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 360,
        threshold=60,
        minLineLength=int(min(height, width) * 0.35),
        maxLineGap=int(min(height, width) * 0.05),
    )
    grouped = {"left": [], "right": [], "top": [], "bottom": []}
    if raw is None:
        return {key: None for key in grouped}
    for line in raw.reshape(-1, 4):
        x1, y1, x2, y2 = [float(v) for v in line]
        length = float(np.hypot(x2 - x1, y2 - y1))
        angle = abs(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
        mid_x = (x1 + x2) / 2.0
        mid_y = (y1 + y2) / 2.0
        vertical = abs(angle - 90) < 12 and length >= 0.4 * height
        horizontal = (angle < 8 or angle > 172) and length >= 0.4 * width
        side = None
        if vertical and mid_x < width * 0.3:
            side = "left"
        elif vertical and mid_x > width * 0.7:
            side = "right"
        elif horizontal and mid_y < height * 0.3:
            side = "top"
        elif horizontal and mid_y > height * 0.7:
            side = "bottom"
        if side:
            grouped[side].append((length, mid_x, mid_y, np.array([x1, y1, x2, y2], dtype=np.float32)))
    chosen = {}
    for side, items in grouped.items():
        if not items:
            chosen[side] = None
            continue
        longest = max(item[0] for item in items)
        strong = [item for item in items if item[0] >= 0.6 * longest]
        if side == "left":
            pick = min(strong, key=lambda item: item[1])
        elif side == "right":
            pick = max(strong, key=lambda item: item[1])
        elif side == "top":
            pick = min(strong, key=lambda item: item[2])
        else:
            pick = max(strong, key=lambda item: item[2])
        chosen[side] = pick[3]
    return chosen


def line_quad(gray: np.ndarray) -> Optional[np.ndarray]:
    height, width = gray.shape[:2]
    found = band_lines(gray)
    if sum(1 for value in found.values() if value is not None) < MIN_LINE_SIDES:
        return None
    defaults = {
        "left": np.array([0, 0, 0, height - 1], dtype=np.float32),
        "right": np.array([width - 1, 0, width - 1, height - 1], dtype=np.float32),
        "top": np.array([0, 0, width - 1, 0], dtype=np.float32),
        "bottom": np.array([0, height - 1, width - 1, height - 1], dtype=np.float32),
    }
    lines = {key: (found[key] if found[key] is not None else defaults[key]) for key in defaults}
    corners = [
        line_intersection(lines["top"], lines["left"]),
        line_intersection(lines["top"], lines["right"]),
        line_intersection(lines["bottom"], lines["right"]),
        line_intersection(lines["bottom"], lines["left"]),
    ]
    if any(corner is None for corner in corners):
        return None
    return np.array(corners, dtype=np.float32)


def quads_from_contour(contour: np.ndarray) -> List[np.ndarray]:
    hull = cv2.convexHull(contour)
    perimeter = cv2.arcLength(hull, True)
    quads = []
    for factor in APPROX_FACTORS:
        approx = cv2.approxPolyDP(hull, factor * perimeter, True)
        if len(approx) == 4:
            quads.append(approx.reshape(4, 2).astype(np.float32))
    rect = cv2.minAreaRect(hull)
    quads.append(cv2.boxPoints(rect).astype(np.float32))
    return quads


def score_quad(
    quad: np.ndarray,
    contour_area: float,
    frame_area: float,
    cfg: VisionConfig,
    edges: np.ndarray,
) -> Optional[tuple[float, float, float]]:
    ordered = order_points(quad)
    width, height = quad_dimensions(ordered)
    if min(width, height) <= 1:
        return None
    aspect = max(width, height) / min(width, height)
    quad_area = float(cv2.contourArea(ordered))
    if quad_area <= 0:
        return None
    area_ratio = quad_area / frame_area
    if area_ratio < cfg.min_card_area_ratio or area_ratio > 1.2:
        return None
    aspect_error = abs(aspect - cfg.card_aspect_ratio) / cfg.card_aspect_ratio
    if aspect_error > cfg.aspect_tolerance:
        return None
    fill = min(contour_area / quad_area, 1.0)
    support = edge_support(quad, edges)
    confidence = (
        0.25 * min(area_ratio / 0.5, 1.0)
        + 0.25 * (1.0 - aspect_error / cfg.aspect_tolerance)
        + 0.10 * fill
        + 0.40 * support
    )
    if area_ratio > FULL_FRAME_RATIO:
        confidence *= FULL_FRAME_PENALTY
    return confidence, area_ratio, aspect


def collect_candidates(
    mask: np.ndarray,
    method: str,
    retrieval: int,
    frame_area: float,
    cfg: VisionConfig,
    edges: np.ndarray,
) -> List[Candidate]:
    contours, _ = cv2.findContours(mask, retrieval, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:MAX_CONTOURS]
    found = []
    for contour in contours:
        contour_area = float(cv2.contourArea(contour))
        if contour_area < cfg.min_card_area_ratio * frame_area * 0.5:
            continue
        for quad in quads_from_contour(contour):
            scored = score_quad(quad, contour_area, frame_area, cfg, edges)
            if scored is None:
                continue
            confidence, area_ratio, aspect = scored
            found.append(Candidate(order_points(quad), confidence, method, area_ratio, aspect))
    return found


def detect_card(
    image: np.ndarray, cfg: VisionConfig
) -> tuple[CardDetection, Optional[np.ndarray]]:
    height, width = image.shape[:2]
    padded, scale = prepare(image, cfg)
    gray = cv2.cvtColor(padded, cv2.COLOR_BGR2GRAY)
    frame_area = float((padded.shape[0] - 2 * PAD) * (padded.shape[1] - 2 * PAD))
    closed_edges = edge_mask(gray)
    thin_edges = cv2.dilate(cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 40, 120), np.ones((5, 5), np.uint8))
    candidates = collect_candidates(
        closed_edges, "edges", cv2.RETR_LIST, frame_area, cfg, thin_edges
    )
    for mask in threshold_masks(gray):
        candidates += collect_candidates(
            mask, "threshold", cv2.RETR_EXTERNAL, frame_area, cfg, thin_edges
        )
    candidates += collect_candidates(
        grabcut_mask(padded), "grabcut", cv2.RETR_EXTERNAL, frame_area, cfg, thin_edges
    )
    lines_corners = line_quad(gray)
    if lines_corners is not None:
        scored = score_quad(
            lines_corners, frame_area, frame_area, cfg, thin_edges
        )
        if scored is not None:
            confidence, area_ratio, aspect = scored
            candidates.append(
                Candidate(order_points(lines_corners), confidence, "lines", area_ratio, aspect)
            )
    if not candidates:
        return (
            CardDetection(found=False, confidence=0.0, method="none"),
            None,
        )
    best = max(candidates, key=lambda item: item.confidence)
    corners = (best.corners - PAD) / scale
    corners[:, 0] = np.clip(corners[:, 0], 0, width - 1)
    corners[:, 1] = np.clip(corners[:, 1], 0, height - 1)
    detection = CardDetection(
        found=best.confidence >= cfg.min_detection_confidence,
        confidence=round(float(best.confidence), 3),
        method=best.method,
        corners=[[round(float(x), 1), round(float(y), 1)] for x, y in corners],
        area_ratio=round(float(best.area_ratio), 3),
        aspect_ratio=round(float(best.aspect_ratio), 3),
    )
    return detection, corners.astype(np.float32)