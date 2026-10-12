from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class CardSide(str, Enum):
    FRONT = "front"
    BACK = "back"


class QualityIssue(str, Enum):
    LOW_RESOLUTION = "low_resolution"
    BLURRY = "blurry"
    TOO_DARK = "too_dark"
    TOO_BRIGHT = "too_bright"
    GLARE = "glare"


class VisionStatus(str, Enum):
    OK = "ok"
    RETRY = "retry"
    FAILED = "failed"


class QualityMetrics(BaseModel):
    width: int
    height: int
    sharpness: float
    brightness: float
    dark_ratio: float
    bright_ratio: float
    glare_ratio: float


class QualityReport(BaseModel):
    metrics: QualityMetrics
    issues: List[QualityIssue] = Field(default_factory=list)
    passed: bool


class CardDetection(BaseModel):
    found: bool
    confidence: float
    method: str
    corners: Optional[List[List[float]]] = None
    area_ratio: float = 0.0
    aspect_ratio: float = 0.0


class VisionResult(BaseModel):
    side: CardSide
    status: VisionStatus
    reasons: List[str] = Field(default_factory=list)
    input_quality: QualityReport
    detection: CardDetection
    card_quality: Optional[QualityReport] = None