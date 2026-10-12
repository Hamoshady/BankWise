import cv2
import numpy as np

from bankwise.core.config import VisionConfig
from bankwise.identity.vision.schemas import QualityIssue, QualityMetrics, QualityReport


def compute_metrics(image: np.ndarray, cfg: VisionConfig) -> QualityMetrics:
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    scale = cfg.sharpness_reference_width / width
    interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    reference = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=interpolation)
    sharpness = float(cv2.Laplacian(reference, cv2.CV_64F).var())
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    glare_mask = (hsv[:, :, 2] >= 250) & (hsv[:, :, 1] < 40)
    return QualityMetrics(
        width=int(width),
        height=int(height),
        sharpness=round(sharpness, 2),
        brightness=round(float(gray.mean()), 2),
        dark_ratio=round(float((gray < 40).mean()), 4),
        bright_ratio=round(float((gray > 215).mean()), 4),
        glare_ratio=round(float(glare_mask.mean()), 4),
    )


def assess_quality(
    image: np.ndarray, cfg: VisionConfig, check_resolution: bool = False
) -> QualityReport:
    metrics = compute_metrics(image, cfg)
    issues = []
    if check_resolution:
        long_side = max(metrics.width, metrics.height)
        short_side = min(metrics.width, metrics.height)
        if long_side < cfg.min_width or short_side < cfg.min_height:
            issues.append(QualityIssue.LOW_RESOLUTION)
    if metrics.sharpness < cfg.min_sharpness:
        issues.append(QualityIssue.BLURRY)
    if metrics.brightness < cfg.min_brightness or metrics.dark_ratio > cfg.max_dark_ratio:
        issues.append(QualityIssue.TOO_DARK)
    if metrics.brightness > cfg.max_brightness or metrics.bright_ratio > cfg.max_bright_ratio:
        issues.append(QualityIssue.TOO_BRIGHT)
    if metrics.glare_ratio > cfg.max_glare_ratio:
        issues.append(QualityIssue.GLARE)
    return QualityReport(metrics=metrics, issues=issues, passed=not issues)