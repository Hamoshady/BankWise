import io

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError

from bankwise.core.config import VisionConfig
from bankwise.core.errors import InvalidImageError

JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def has_allowed_signature(data: bytes) -> bool:
    if data.startswith(JPEG_MAGIC) or data.startswith(PNG_MAGIC):
        return True
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def decode_image(data: bytes, cfg: VisionConfig) -> np.ndarray:
    if not data:
        raise InvalidImageError("empty file")
    if len(data) > cfg.max_image_bytes:
        raise InvalidImageError("file too large")
    if not has_allowed_signature(data):
        raise InvalidImageError("unsupported file type")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            width, height = probe.size
    except (UnidentifiedImageError, OSError) as exc:
        raise InvalidImageError("unreadable image") from exc
    if width * height > cfg.max_pixels:
        raise InvalidImageError("image dimensions too large")
    buffer = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise InvalidImageError("unreadable image")
    return limit_side(image, cfg.max_working_side)


def limit_side(image: np.ndarray, max_side: int) -> np.ndarray:
    height, width = image.shape[:2]
    longest = max(height, width)
    if longest <= max_side:
        return image
    scale = max_side / longest
    return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)