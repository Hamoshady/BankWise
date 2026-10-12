import cv2
import numpy as np


def normalize_illumination(card: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(card, cv2.COLOR_BGR2LAB)
    lightness, channel_a, channel_b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    merged = cv2.merge((clahe.apply(lightness), channel_a, channel_b))
    return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)


def prepare_for_ocr(card: np.ndarray) -> np.ndarray:
    enhanced = normalize_illumination(card)
    return cv2.fastNlMeansDenoisingColored(enhanced, None, 3, 3, 7, 21)