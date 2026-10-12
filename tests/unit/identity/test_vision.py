import cv2
import numpy as np
import pytest

from bankwise.core.config import load_identity_config
from bankwise.core.errors import InvalidImageError
from bankwise.identity.vision.card_detector import detect_card
from bankwise.identity.vision.image_io import decode_image
from bankwise.identity.vision.perspective import order_points, warp_card
from bankwise.identity.vision.processor import process_card
from bankwise.identity.vision.quality import assess_quality
from bankwise.identity.vision.schemas import CardSide, QualityIssue, VisionStatus


@pytest.fixture(scope="module")
def cfg():
    return load_identity_config().vision


def make_card_scene(width=1600, height=1100, angle=0.0):
    scene = np.full((height, width, 3), 35, dtype=np.uint8)
    card_w, card_h = 1100, 693
    card = np.full((card_h, card_w, 3), 185, dtype=np.uint8)
    rng = np.random.default_rng(7)
    for _ in range(120):
        x = int(rng.integers(20, card_w - 200))
        y = int(rng.integers(20, card_h - 40))
        cv2.putText(card, "TEXT123", (x, y), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (40, 40, 40), 2)
    top = (height - card_h) // 2
    left = (width - card_w) // 2
    scene[top : top + card_h, left : left + card_w] = card
    if angle:
        matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
        scene = cv2.warpAffine(scene, matrix, (width, height), borderValue=(35, 35, 35))
    return scene


def encode(image, extension=".jpg"):
    ok, buffer = cv2.imencode(extension, image)
    assert ok
    return buffer.tobytes()


def test_decode_rejects_empty(cfg):
    with pytest.raises(InvalidImageError):
        decode_image(b"", cfg)


def test_decode_rejects_wrong_signature(cfg):
    with pytest.raises(InvalidImageError):
        decode_image(b"%PDF-1.4 not an image", cfg)


def test_decode_rejects_oversized_file(cfg):
    big = b"\xff\xd8\xff" + b"0" * (cfg.max_image_bytes + 1)
    with pytest.raises(InvalidImageError):
        decode_image(big, cfg)


def test_decode_rejects_truncated_jpeg(cfg):
    with pytest.raises(InvalidImageError):
        decode_image(b"\xff\xd8\xff\xe0garbage", cfg)


def test_order_points_is_consistent():
    points = np.array([[100, 300], [400, 280], [10, 20], [390, 40]], dtype=np.float32)
    ordered = order_points(points)
    assert ordered[0].tolist() == [10.0, 20.0]
    assert ordered[2].tolist() == [400.0, 280.0]


def test_detects_straight_card(cfg):
    detection, corners = detect_card(make_card_scene(), cfg)
    assert detection.found
    assert corners is not None
    assert abs(detection.aspect_ratio - cfg.card_aspect_ratio) < 0.2


def test_detects_rotated_card(cfg):
    detection, corners = detect_card(make_card_scene(angle=8.0), cfg)
    assert detection.found


def test_warp_has_canonical_size(cfg):
    scene = make_card_scene()
    _, corners = detect_card(scene, cfg)
    warped = warp_card(scene, corners, cfg.canonical_width, cfg.canonical_height)
    assert warped.shape[:2] == (cfg.canonical_height, cfg.canonical_width)


def test_blurry_image_is_flagged(cfg):
    blurred = cv2.GaussianBlur(make_card_scene(), (0, 0), 12)
    report = assess_quality(blurred, cfg)
    assert QualityIssue.BLURRY in report.issues
    assert not report.passed


def test_dark_image_is_flagged(cfg):
    dark = (make_card_scene() * 0.2).astype(np.uint8)
    report = assess_quality(dark, cfg)
    assert QualityIssue.TOO_DARK in report.issues


def test_low_resolution_is_flagged(cfg):
    small = cv2.resize(make_card_scene(), (500, 330))
    report = assess_quality(small, cfg, check_resolution=True)
    assert QualityIssue.LOW_RESOLUTION in report.issues


def test_process_card_ok_on_clean_scene(cfg):
    output = process_card(encode(make_card_scene()), CardSide.FRONT, cfg)
    assert output.result.status == VisionStatus.OK
    assert output.card_image is not None
    assert output.ocr_image is not None


def test_process_card_retry_on_blur(cfg):
    blurred = cv2.GaussianBlur(make_card_scene(), (0, 0), 10)
    output = process_card(encode(blurred), CardSide.BACK, cfg)
    assert output.result.status in (VisionStatus.RETRY, VisionStatus.FAILED)
    assert output.ocr_image is None


def test_process_card_fails_without_card(cfg):
    empty = np.full((900, 1400, 3), 120, dtype=np.uint8)
    output = process_card(encode(empty), CardSide.FRONT, cfg)
    assert output.result.status == VisionStatus.FAILED
    assert output.result.reasons == ["card_not_found"]