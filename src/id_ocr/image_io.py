"""Image loading, validation and basic quality assessment."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import numpy as np

from .config import Box, OcrConfig


class ImageError(Exception):
    """Raised for inputs that cannot be processed. ``code`` is safe to show end users."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class QualityReport:
    width: int
    height: int
    blur_score: float
    mean_brightness: float
    issues: List[str] = field(default_factory=list)

    def as_dict(self):
        return {"width": self.width, "height": self.height, "blur_score": round(self.blur_score, 1),
                "mean_brightness": round(self.mean_brightness, 1), "issues": list(self.issues)}


def load_image(path: str, cfg: Optional[OcrConfig] = None) -> np.ndarray:
    """Load a BGR image. Uses np.fromfile so non-ASCII Windows paths work."""
    cfg = cfg or OcrConfig()
    if not path or not os.path.isfile(path):
        raise ImageError("file_missing", "Image file was not found.")
    ext = os.path.splitext(path)[1].lower()
    if ext not in cfg.allowed_extensions:
        raise ImageError("unsupported_format", f"Unsupported image format '{ext}'.")
    size = os.path.getsize(path)
    if size == 0:
        raise ImageError("empty_file", "Image file is empty.")
    if size > cfg.max_file_bytes:
        raise ImageError("file_too_large", "Image file is too large.")
    try:
        buf = np.fromfile(path, dtype=np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    except Exception as exc:  # pragma: no cover - defensive
        raise ImageError("corrupted_image", "Image could not be decoded.") from exc
    if img is None:
        raise ImageError("corrupted_image", "Image could not be decoded.")
    return validate_array(img, cfg)


def validate_array(img: np.ndarray, cfg: Optional[OcrConfig] = None) -> np.ndarray:
    cfg = cfg or OcrConfig()
    if img is None or img.size == 0:
        raise ImageError("empty_image", "Image contains no pixels.")
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.ndim != 3 or img.shape[2] not in (3, 4):
        raise ImageError("invalid_dimensions", "Unexpected image shape.")
    if img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    h, w = img.shape[:2]
    if min(h, w) < cfg.hard_min_side_px:
        raise ImageError("resolution_too_low", "Image resolution is too low to read.")
    if max(h, w) > cfg.max_side_px:  # downscale keeping aspect ratio
        scale = cfg.max_side_px / float(max(h, w))
        img = cv2.resize(img, (int(round(w * scale)), int(round(h * scale))), interpolation=cv2.INTER_AREA)
    return img


def crop_normalized(img: np.ndarray, box: Box) -> np.ndarray:
    """Crop with a normalised (x0, y0, x1, y1) box. Returns a view (no copy)."""
    h, w = img.shape[:2]
    x0, y0, x1, y1 = box
    xa, xb = max(int(x0 * w), 0), min(int(round(x1 * w)), w)
    ya, yb = max(int(y0 * h), 0), min(int(round(y1 * h)), h)
    if xb - xa < 4 or yb - ya < 4:
        raise ImageError("invalid_crop", "Crop region is empty.")
    return img[ya:yb, xa:xb]


def rotate_image(img: np.ndarray, degrees: int) -> np.ndarray:
    """Manual rotation by multiples of 90 degrees (no automatic orientation detection)."""
    degrees %= 360
    if degrees == 0:
        return img
    code = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}.get(degrees)
    if code is None:
        raise ImageError("invalid_rotation", "Rotation must be a multiple of 90 degrees.")
    return cv2.rotate(img, code)


def assess_quality(img: np.ndarray, cfg: Optional[OcrConfig] = None) -> QualityReport:
    cfg = cfg or OcrConfig()
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    # Laplacian variance is resolution dependent; evaluate on a fixed-width copy.
    ref = cv2.resize(gray, (1000, max(int(1000 * h / w), 1)), interpolation=cv2.INTER_AREA)
    blur = float(cv2.Laplacian(ref, cv2.CV_64F).var())
    mean = float(gray.mean())
    rep = QualityReport(width=w, height=h, blur_score=blur, mean_brightness=mean)
    if min(h, w) < cfg.soft_min_side_px:
        rep.issues.append("low_resolution")
    if blur < cfg.blur_threshold:
        rep.issues.append("blurry")
    if mean < cfg.dark_threshold:
        rep.issues.append("too_dark")
    if mean > cfg.bright_threshold:
        rep.issues.append("overexposed")
    return rep
