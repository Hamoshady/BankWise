"""Configuration for the EasyOCR-based Egyptian national ID pipeline.

Everything that may differ between deployments or card layouts lives here:
GPU usage, thresholds, region definitions (normalised 0..1 coordinates),
vocabularies, retry limits and debug output.

Regions are *defaults measured on a single sample card photo*. They are NOT
universal coordinates. Override them with ``OcrConfig.load_regions(json_path)``
or by passing ``crop_box`` to the pipeline when the card does not fill the image.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

Box = Tuple[float, float, float, float]  # (x0, y0, x1, y1), each in 0..1

# Field kinds understood by id_ocr.extract
KIND_DIGITS = "digits"          # 14-digit national id
KIND_DATE = "date"              # year-first date, full or year/month
KIND_TEXT_LINE = "text_line"    # one printed Arabic line
KIND_TOKENS = "tokens"          # free-text word(s), read with detection
KIND_SERIAL = "serial"          # latin alphanumeric serial
KIND_ATTRIBUTES = "attributes"  # row holding gender / religion / marital status


@dataclass
class FieldSpec:
    name: str
    side: str
    box: Box
    kind: str
    variants: List[str]
    critical: bool = False


def _default_specs() -> Dict[str, List[FieldSpec]]:
    front = [
        FieldSpec("national_id", "front", (0.40, 0.74, 0.97, 0.86), KIND_DIGITS, ["gray", "up2"], True),
        FieldSpec("birth_date", "front", (0.05, 0.72, 0.35, 0.85), KIND_DATE, ["gray", "up2"]),
        FieldSpec("first_name", "front", (0.76, 0.24, 0.97, 0.36), KIND_TOKENS, ["gray", "clahe"], True),
        FieldSpec("other_names", "front", (0.50, 0.33, 0.97, 0.45), KIND_TOKENS, ["gray", "clahe"], True),
        FieldSpec("address_line1", "front", (0.42, 0.455, 0.97, 0.55), KIND_TOKENS, ["gray", "clahe"]),
        FieldSpec("address_line2", "front", (0.42, 0.545, 0.97, 0.645), KIND_TOKENS, ["gray", "clahe"]),
        FieldSpec("serial", "front", (0.08, 0.87, 0.40, 0.98), KIND_SERIAL, ["gray", "original"]),
    ]
    back = [
        FieldSpec("national_id", "back", (0.45, 0.04, 0.80, 0.16), KIND_DIGITS, ["gray", "up2"], True),
        FieldSpec("issue_date", "back", (0.25, 0.04, 0.43, 0.16), KIND_DATE, ["gray", "up2"]),
        FieldSpec("expiry_date", "back", (0.30, 0.45, 0.55, 0.57), KIND_DATE, ["gray", "clahe"]),
        FieldSpec("job", "back", (0.60, 0.12, 0.85, 0.23), KIND_TOKENS, ["gray", "clahe"]),
        FieldSpec("attributes", "back", (0.30, 0.28, 0.85, 0.42), KIND_ATTRIBUTES, ["clahe", "gray"]),
    ]
    return {"front": front, "back": back}


# Closed vocabularies for the attribute row. UNVERIFIED defaults - edit to match
# the card wording your business actually sees. Matching is fuzzy and only used
# to map OCR tokens onto these strings; the raw OCR text is kept in the trace.
DEFAULT_VOCAB: Dict[str, List[str]] = {
    "gender": ["ذكر", "أنثى"],
    "religion": ["مسلم", "مسيحى", "مسيحي", "يهودى"],
    "marital_status": ["أعزب", "متزوج", "مطلق", "أرمل", "عزباء", "متزوجة", "مطلقة", "أرملة"],
}

# Governorate codes (digits 8-9 of the national id). Public, widely documented
# list used as a DEFAULT. Replace with the project's existing mapping if it differs.
DEFAULT_GOVERNORATES: Dict[str, str] = {
    "01": "Cairo", "02": "Alexandria", "03": "Port Said", "04": "Suez",
    "11": "Damietta", "12": "Dakahlia", "13": "Sharqia", "14": "Qalyubia",
    "15": "Kafr El Sheikh", "16": "Gharbia", "17": "Monufia", "18": "Beheira",
    "19": "Ismailia", "21": "Giza", "22": "Beni Suef", "23": "Fayoum",
    "24": "Minya", "25": "Assiut", "26": "Sohag", "27": "Qena", "28": "Aswan",
    "29": "Luxor", "31": "Red Sea", "32": "New Valley", "33": "Matrouh",
    "34": "North Sinai", "35": "South Sinai", "88": "Born abroad",
}


@dataclass
class OcrConfig:
    backend: str = "easyocr"  # Paddle is opt-in until benchmark acceptance.
    paddle_model_dir: Optional[str] = None
    cpu_threads: int = 4
    numeric_backend: Optional[str] = None  # experimental, never enabled by the API defaults
    numeric_weights: Optional[str] = None
    numeric_preprocess: str = 'original'
    numeric_crop_padding: float = 0.0  # horizontal fraction of numeric field width, experimental only
    # Trusted, custom-trained local detection weights only; absent means disabled.
    yolo_weights: Optional[str] = None
    yolo_always: bool = False  # experimental comparison; normal behavior remains alignment fallback
    yolo_stretch_square: bool = False  # match a stretched dataset export only when explicitly configured
    # Lightweight mode is automatically used with Paddle; opt-in for controlled comparisons.
    lightweight: bool = False
    crop_margin: float = 0.0  # fraction of card dimensions; configurable, default preserves existing crops

    # --- engine / GPU -----------------------------------------------------
    use_gpu: bool = True
    allow_cpu_fallback: bool = False      # explicit opt-in recovery option
    model_dir: Optional[str] = None       # EasyOCR model storage directory
    download_models: bool = True          # False => fully offline (weights must exist)

    # --- image handling ---------------------------------------------------
    allowed_extensions: Tuple[str, ...] = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff")
    max_file_bytes: int = 25 * 1024 * 1024
    hard_min_side_px: int = 300           # below -> REJECTED (cannot be read at all)
    soft_min_side_px: int = 800           # below -> quality issue (RETRY candidate)
    max_side_px: int = 3200               # larger inputs are downscaled (aspect kept)
    blur_threshold: float = 60.0          # variance of Laplacian on the card image
    dark_threshold: float = 70.0          # mean gray level
    bright_threshold: float = 215.0
    upscale_factor: float = 2.0           # used by the "up2" variant
    clahe_clip: float = 2.0
    clahe_grid: int = 8
    line_padding_px: int = 12             # replicate-border padding for line crops

    auto_align_card: bool = True          # conservative card-boundary rectification; manual crop takes precedence

    # --- OCR / confidence -------------------------------------------------
    decoder: str = "beamsearch"           # EasyOCR decoder: "greedy" | "beamsearch"
    min_text_conf: float = 0.30           # per-detection; below => low_confidence
    min_numeric_conf: float = 0.50
    vocab_min_ratio: float = 0.75         # fuzzy match threshold for closed vocabularies
    max_rereads: int = 2                  # extra bounded attempts per field
    full_image_pass: str = "fallback"     # "never" | "fallback" (only if national id unreadable)
    on_text_disagreement: str = "unresolved"   # or "prefer_highest_confidence"

    # --- field rules ------------------------------------------------------
    serial_pattern: str = r"^[A-Z]{2}\d{7}$"   # derived from ONE sample card; configurable
    null_on_serial_pattern_mismatch: bool = True   # a serial that breaks the pattern is returned as null (flagged)
    first_digit_centuries: Dict[str, int] = field(default_factory=lambda: {"2": 1900, "3": 2000})
    governorates: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_GOVERNORATES))
    vocab: Dict[str, List[str]] = field(default_factory=lambda: {k: list(v) for k, v in DEFAULT_VOCAB.items()})
    date_output_format: str = "iso"       # "iso" (YYYY-MM-DD) or "slash" (YYYY/MM/DD)

    # --- decision engine --------------------------------------------------
    required_fields: Dict[str, List[str]] = field(default_factory=lambda: {
        "front": ["national_id", "first_name", "other_names"],
        "back": ["national_id", "expiry_date"],
    })
    require_both_sides: bool = False
    expired_action: str = "MANUAL_REVIEW"  # or "REJECTED"; no universal validity period is assumed
    max_attempts: int = 3                  # caller-supplied attempt counter vs this limit

    # --- debug ------------------------------------------------------------
    debug_dir: Optional[str] = None       # if set, saves region crops (CONTAIN PII!)
    log_level: str = "INFO"

    # --- regions ----------------------------------------------------------
    regions: Dict[str, List[FieldSpec]] = field(default_factory=_default_specs)

    def load_regions(self, json_path: str) -> None:
        """Override region definitions from a JSON file.

        Format: {"front": [{"name": ..., "box": [x0,y0,x1,y1], "kind": ..., "variants": [...],
        "critical": false}], "back": [...]}
        """
        with open(json_path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        out: Dict[str, List[FieldSpec]] = {}
        for side, items in raw.items():
            out[side] = [
                FieldSpec(
                    name=i["name"], side=side, box=tuple(i["box"]),  # type: ignore[arg-type]
                    kind=i["kind"], variants=list(i["variants"]), critical=bool(i.get("critical", False)),
                )
                for i in items
            ]
        self.regions = out
