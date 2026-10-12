from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from bankwise.core.config import VisionConfig
from bankwise.identity.vision.card_detector import detect_card
from bankwise.identity.vision.image_io import decode_image
from bankwise.identity.vision.perspective import warp_card
from bankwise.identity.vision.preprocess import prepare_for_ocr
from bankwise.identity.vision.quality import assess_quality
from bankwise.identity.vision.schemas import (
    CardSide,
    QualityIssue,
    VisionResult,
    VisionStatus,
)


@dataclass
class VisionOutput:
    result: VisionResult
    card_image: Optional[np.ndarray]
    ocr_image: Optional[np.ndarray]


def process_card(data: bytes, side: CardSide, cfg: VisionConfig) -> VisionOutput:
    image = decode_image(data, cfg)
    input_quality = assess_quality(image, cfg, check_resolution=True)
    detection, corners = detect_card(image, cfg)
    if not detection.found or corners is None:
        result = VisionResult(
            side=side,
            status=VisionStatus.FAILED,
            reasons=["card_not_found"],
            input_quality=input_quality,
            detection=detection,
        )
        return VisionOutput(result=result, card_image=None, ocr_image=None)
    card = warp_card(image, corners, cfg.canonical_width, cfg.canonical_height)
    card_quality = assess_quality(card, cfg)
    reasons: List[str] = [issue.value for issue in card_quality.issues]
    if QualityIssue.LOW_RESOLUTION in input_quality.issues:
        reasons.append(QualityIssue.LOW_RESOLUTION.value)
    status = VisionStatus.OK if not reasons else VisionStatus.RETRY
    result = VisionResult(
        side=side,
        status=status,
        reasons=reasons,
        input_quality=input_quality,
        detection=detection,
        card_quality=card_quality,
    )
    ocr_image = prepare_for_ocr(card) if status == VisionStatus.OK else None
    return VisionOutput(result=result, card_image=card, ocr_image=ocr_image)