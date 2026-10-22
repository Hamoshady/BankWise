"""Deterministic decision engine.

Pure function of a plain ``DecisionInput``; it knows nothing about EasyOCR.
Three concepts are kept separate in the output:

  ocr_quality            how well the card could be READ
  data_consistency       whether what was read is internally consistent
  document_authenticity  ALWAYS "not_assessed" - OCR cannot establish it

Status rules, evaluated in this order
-------------------------------------
REJECTED       - input violates requirements: no input, unsupported/missing/too-large
                 file, a required side not supplied (cfg.require_both_sides), or an
                 expired card when cfg.expired_action == "REJECTED".
RETRY          - another capture/attempt is justified and attempts remain:
                 recoverable image problems (corrupted, empty, too low resolution), or
                 something is wrong AND the image has quality issues, or required fields
                 are unreadable without any data conflict.
                 Once ``attempt >= cfg.max_attempts`` RETRY becomes MANUAL_REVIEW.
MANUAL_REVIEW  - something is ambiguous or inconsistent on an otherwise acceptable image:
                 conflicting candidates, invalid national-id structure, birth-date /
                 gender / front-back mismatch, date-order problems, expired card
                 (default), or a required/critical field that is only low-confidence or
                 single-read.
VERIFIED       - ALL of: required fields present with status "ok"; national id
                 structurally valid; no conflicts anywhere; no consistency mismatch;
                 card not expired. Quality warnings alone only add a review flag.
                 This means "read consistently", not "genuine" and not "belongs to the
                 presenter".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from .config import OcrConfig

REJECT_CODES = {"file_missing", "unsupported_format", "file_too_large", "no_input", "invalid_rotation"}
RETRYABLE_INPUT_CODES = {"corrupted_image", "empty_file", "empty_image", "resolution_too_low",
                         "invalid_dimensions", "invalid_crop"}


@dataclass
class DecisionInput:
    sides_supplied: List[str]
    input_errors: List[str] = field(default_factory=list)
    quality_issues: Dict[str, List[str]] = field(default_factory=dict)
    field_status: Dict[str, str] = field(default_factory=dict)      # merged field -> status
    required_fields: List[str] = field(default_factory=list)
    critical_fields: List[str] = field(default_factory=list)
    nid_structure: str = "missing"        # valid | invalid | missing | unresolved
    birth_date_check: str = "unavailable"  # match | mismatch | unavailable
    gender_check: str = "unavailable"
    id_sides_check: str = "unavailable"
    expiry_state: str = "unknown"          # valid_until_future | expired | unknown
    date_issues: List[str] = field(default_factory=list)
    attempt: int = 1


@dataclass
class Decision:
    status: str
    reasons: List[str]
    review_flags: List[str]
    ocr_quality: str
    data_consistency: str
    document_authenticity: str = "not_assessed"

    def as_dict(self):
        return {"status": self.status, "reasons": self.reasons, "review_flags": self.review_flags,
                "ocr_quality": self.ocr_quality, "data_consistency": self.data_consistency,
                "document_authenticity": self.document_authenticity,
                "note": "OCR and structural checks only; not proof of genuineness or of who is presenting the card."}


def decide(inp: DecisionInput, cfg: OcrConfig) -> Decision:
    reasons: List[str] = []
    flags: List[str] = []
    can_retry = inp.attempt < cfg.max_attempts

    all_quality = sorted({i for v in inp.quality_issues.values() for i in v})
    fs = inp.field_status

    # ---- REJECTED ---------------------------------------------------------
    rej = [f"input:{c}" for c in inp.input_errors if c in REJECT_CODES]
    if not inp.sides_supplied and not inp.input_errors:
        rej.append("input:no_input")
    if cfg.require_both_sides and {"front", "back"} - set(inp.sides_supplied):
        rej.append("input:missing_required_side")
    if inp.expiry_state == "expired" and cfg.expired_action == "REJECTED":
        rej.append("card_expired")
    if rej:
        return Decision("REJECTED", rej, flags, "poor", "incomplete")

    # ---- classify problems -----------------------------------------------
    retryable_input = [f"input:{c}" for c in inp.input_errors if c in RETRYABLE_INPUT_CODES]
    inconsistent: List[str] = []
    if inp.nid_structure == "invalid":
        inconsistent.append("national_id_structure_invalid")
    if inp.birth_date_check == "mismatch":
        inconsistent.append("birth_date_mismatch_with_national_id")
    if inp.gender_check == "mismatch":
        inconsistent.append("gender_mismatch_with_national_id")
    if inp.id_sides_check == "mismatch":
        inconsistent.append("national_id_front_back_mismatch")
    inconsistent += list(inp.date_issues)
    if inp.expiry_state == "expired":
        inconsistent.append("card_expired")
    conflicts = sorted(f for f, s in fs.items() if s == "conflict")
    inconsistent += [f"conflict:{f}" for f in conflicts]
    if inp.nid_structure == "unresolved" and "conflict:national_id" not in inconsistent:
        inconsistent.append("conflict:national_id")

    missing_required = sorted(f for f in inp.required_fields if fs.get(f) in (None, "unreadable", "invalid"))
    weak_critical = sorted(f for f in inp.critical_fields if fs.get(f) in ("single_read", "low_confidence"))
    weak_required = sorted(f for f in inp.required_fields if fs.get(f) in ("single_read", "low_confidence"))
    weak = sorted(set(weak_critical + weak_required))

    flags += [f"weak_read:{f}" for f in weak]
    flags += [f"quality:{q}" for q in all_quality]
    for f, s in fs.items():
        if s in ("single_read", "low_confidence") and f not in weak:
            flags.append(f"weak_read:{f}")

    ocr_quality = "poor" if (retryable_input or missing_required) else ("degraded" if (all_quality or weak) else "good")
    data_consistency = "inconsistent" if inconsistent else ("incomplete" if missing_required else "consistent")

    # ---- RETRY / MANUAL_REVIEW -------------------------------------------
    if retryable_input:
        status = "RETRY" if can_retry else "MANUAL_REVIEW"
        return Decision(status, retryable_input, flags, ocr_quality, data_consistency)

    if inconsistent or missing_required or weak:
        reasons = inconsistent + [f"missing:{f}" for f in missing_required] + [f"weak_read:{f}" for f in weak] \
            + [f"quality:{q}" for q in all_quality]
        # A better capture can plausibly fix problems on a poor-quality image, or fields that
        # simply could not be read. Inconsistencies / weak reads on a good-quality image go to a human.
        retry_helps = bool(all_quality) or (bool(missing_required) and not inconsistent)
        if retry_helps and can_retry:
            return Decision("RETRY", reasons, flags, ocr_quality, data_consistency)
        return Decision("MANUAL_REVIEW", reasons, flags, ocr_quality, data_consistency)

    # ---- VERIFIED --------------------------------------------------------
    not_ok = [f for f in inp.required_fields if fs.get(f) != "ok"]
    if inp.nid_structure != "valid" or not_ok:
        reasons = (["national_id_not_structurally_valid"] if inp.nid_structure != "valid" else []) + \
                  [f"not_ok:{f}" for f in not_ok]
        return Decision("MANUAL_REVIEW", reasons, flags, ocr_quality, data_consistency)
    return Decision("VERIFIED", ["required_fields_ok", "national_id_structure_valid", "no_conflicts"], flags,
                    ocr_quality, data_consistency)
