"""Preprocessing variants.

Only variants that were measured to help on the sample cards are kept:

  original - untouched BGR crop
  gray     - grayscale
  clahe    - grayscale + CLAHE (local contrast)
  up2      - grayscale + 2x cubic upscale (small/dotted digits)

Binarisation (Otsu / adaptive) was tried and REMOVED: on the sample cards it
returned empty reads for the national id and dates. Variants are cheap, but
each extra one is another OCR call, so the set is kept deliberately small.
Aspect ratio is never altered.
"""
from __future__ import annotations

import cv2
import numpy as np

from .config import OcrConfig


def _gray(img: np.ndarray) -> np.ndarray:
    return img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def make_variant(img: np.ndarray, name: str, cfg: OcrConfig) -> np.ndarray:
    if name == "original":
        return img
    g = _gray(img)
    if name == "gray":
        return g
    if name == "clahe":
        return cv2.createCLAHE(clipLimit=cfg.clahe_clip, tileGridSize=(cfg.clahe_grid, cfg.clahe_grid)).apply(g)
    if name == "up2":
        f = cfg.upscale_factor
        return cv2.resize(g, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    raise ValueError(f"Unknown preprocessing variant '{name}'")


VARIANTS = ("original", "gray", "clahe", "up2")


def pad_line(img: np.ndarray, px: int) -> np.ndarray:
    """Replicate-border padding so glyphs at the crop edge are not clipped."""
    return cv2.copyMakeBorder(img, px, px, px, px, cv2.BORDER_REPLICATE)
