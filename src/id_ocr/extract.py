"""Region-based field extraction.

Flow per field: crop (normalised region) -> preprocessing variants -> OCR
-> normalisation -> parse/validate -> candidate resolution.

Resolution policy (deliberately conservative):
  * every candidate keeps its raw OCR text, normalised text, parsed value,
    per-detection confidence, variant and reader;
  * if independent attempts produce DIFFERENT valid values the field is
    ``conflict`` and its value is None. A first-name segmentation recovery may
    retain a supported value as ``low_confidence`` with the original conflict flag;
  * agreement of several variants of the same crop is reported as ``ok`` but
    is NOT proof of correctness (they share the same model and pixels);
  * ``confidence`` is the minimum per-detection confidence of the supporting
    reads. It is not a calibrated probability of the field being correct.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from .config import (KIND_ATTRIBUTES, KIND_DATE, KIND_DIGITS, KIND_SERIAL, KIND_TEXT_LINE, KIND_TOKENS,
                     FieldSpec, OcrConfig)
from .engine import OcrEngine, RawRead
from .image_io import crop_normalized
from .normalize import (clean_arabic_line, digits_only, has_arabic_letters, match_key, normalize_serial,
                        normalize_unicode, to_western_digits)
from .preprocess import make_variant
from .validation import parse_date, decode_national_id

log = logging.getLogger("id_ocr.extract")

DIGIT_ALLOWLIST = "٠١٢٣٤٥٦٧٨٩0123456789/-."
SERIAL_ALLOWLIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
EXTRA_VARIANT_ORDER = ["original", "gray", "clahe", "up2"]


# --------------------------------------------------------------------------- data
@dataclass
class Candidate:
    raw: str                      # original OCR text (never modified)
    normalized: str               # normalised text
    value: Optional[str]          # parsed/validated value, None if it failed validation
    conf: float                   # min per-detection confidence of this read
    variant: str
    reader: str
    bbox: Optional[Tuple[int, int, int, int]] = None   # region box in full-image pixels
    notes: List[str] = field(default_factory=list)


@dataclass
class FieldResult:
    name: str
    side: str
    value: Optional[str] = None
    status: str = "unreadable"    # ok | single_read | low_confidence | conflict | invalid | unreadable
    confidence: Optional[float] = None
    candidates: List[Candidate] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)
    source: str = "region"


@dataclass
class SideResult:
    side: str
    fields: Dict[str, FieldResult]
    all_text_lines: List[str] = field(default_factory=list)
    timings_ms: Dict[str, float] = field(default_factory=dict)


STATUS_RANK = {"ok": 4, "single_read": 3, "low_confidence": 2, "conflict": 1, "invalid": 0, "unreadable": 0}
PUBLIC_FIELDS = ["first_name", "other_names", "address_line1", "address_line2", "birth_date", "national_id",
                 "serial", "job", "gender", "religion", "marital_status", "issue_date", "expiry_date"]


# ------------------------------------------------------------------- resolution
def resolve(name: str, side: str, cands: List[Candidate], cfg: OcrConfig, numeric: bool, text: bool = False) -> FieldResult:
    """Combine candidates into one FieldResult following the policy in the module docstring."""
    fr = FieldResult(name=name, side=side, candidates=list(cands))
    for c in cands:
        for n in c.notes:
            if n not in fr.flags:
                fr.flags.append(n)
    valid = [c for c in cands if c.value]
    if not valid:
        fr.status = "invalid" if any(c.normalized for c in cands) else "unreadable"
        return fr

    distinct = list(dict.fromkeys(c.value for c in valid))
    threshold = cfg.min_numeric_conf if numeric else cfg.min_text_conf
    if len(distinct) > 1:
        if text and cfg.on_text_disagreement == "prefer_highest_confidence":
            best = max(valid, key=lambda c: c.conf)
            fr.value, fr.confidence, fr.status = best.value, best.conf, "low_confidence"
            fr.flags.append("variant_disagreement")
        else:
            fr.status = "conflict"
            fr.flags.append("candidate_conflict")
        return fr

    support = [c for c in valid if c.value == distinct[0]]
    fr.value = distinct[0]
    fr.confidence = min(c.conf for c in support)
    if fr.confidence < threshold:
        fr.status = "low_confidence"
    elif len(support) >= 2:
        fr.status = "ok"
    else:
        fr.status = "single_read"
    if len(support) < len(cands):
        fr.flags.append("some_attempts_unreadable")
    return fr


# ------------------------------------------------------------------ per-kind reads
def _region_px(img: np.ndarray, box) -> Tuple[int, int, int, int]:
    h, w = img.shape[:2]
    return (int(box[0] * w), int(box[1] * h), int(round(box[2] * w)), int(round(box[3] * h)))


def _min_conf(reads: List[RawRead]) -> float:
    return min(r.conf for r in reads) if reads else 0.0


def _read_candidate(spec: FieldSpec, crop: np.ndarray, variant: str, engine: OcrEngine, cfg: OcrConfig,
                    region_px) -> Candidate:
    v = make_variant(crop, variant, cfg)
    kind = spec.kind

    if kind in (KIND_DIGITS, KIND_DATE):
        reads = engine.read_line(v, "ar", DIGIT_ALLOWLIST)
        raw = " ".join(r.text for r in reads)
        norm = to_western_digits(normalize_unicode(raw))
        cand = Candidate(raw=raw, normalized=norm, value=None, conf=_min_conf(reads), variant=variant,
                         reader="ar", bbox=region_px)
        if kind == KIND_DIGITS:
            d = digits_only(raw)
            cand.normalized = d
            if len(d) == 14:
                cand.value = d
            elif d:
                cand.notes.append("national_id_length_%d" % len(d))
        else:
            pd = parse_date(norm)
            if pd.ok:
                cand.value = pd.format(cfg.date_output_format)
                cand.notes.extend(pd.flags)
            elif norm:
                cand.notes.append("date_" + (pd.error or "invalid"))
        return cand

    if kind == KIND_TEXT_LINE:
        reads = engine.read_line(v, "ar")
        raw = " ".join(r.text for r in reads)
        cleaned, changed = clean_arabic_line(raw)
        cand = Candidate(raw=raw, normalized=cleaned, value=cleaned if has_arabic_letters(cleaned) else None,
                         conf=_min_conf(reads), variant=variant, reader="ar", bbox=region_px)
        if changed and cand.value:
            cand.notes.append("ocr_junk_removed")
        return cand

    if kind == KIND_TOKENS:
        reads = sorted(engine.read_tokens(v, "ar"), key=lambda r: -r.bbox[2])  # right-to-left
        raw = " ".join(r.text for r in reads)
        cleaned, changed = clean_arabic_line(raw)
        cand = Candidate(raw=raw, normalized=cleaned, value=cleaned if has_arabic_letters(cleaned) else None,
                         conf=_min_conf(reads), variant=variant, reader="ar", bbox=region_px)
        if changed and cand.value:
            cand.notes.append("ocr_junk_removed")
        return cand

    if kind == KIND_SERIAL:
        # English reader WITH detection: measured to read the leading 'I' correctly
        # where the Arabic model returns '1'. Latin text reads left-to-right.
        reads = sorted(engine.read_tokens(v, "en", SERIAL_ALLOWLIST), key=lambda r: r.bbox[0])
        raw = " ".join(r.text for r in reads)
        norm = normalize_serial(raw)
        return Candidate(raw=raw, normalized=norm, value=norm or None, conf=_min_conf(reads), variant=variant,
                         reader="en", bbox=region_px)

    raise ValueError(f"Unsupported field kind '{kind}'")


def _extract_simple(spec: FieldSpec, img: np.ndarray, engine: OcrEngine, cfg: OcrConfig) -> FieldResult:
    if cfg.backend == 'paddle' or cfg.lightweight:
        return _extract_lightweight(spec, img, engine, cfg)
    crop = crop_normalized(img, spec.box)
    _debug_save(cfg, spec, crop)
    region_px = _region_px(img, spec.box)
    numeric = spec.kind in (KIND_DIGITS, KIND_DATE)
    is_text = spec.kind in (KIND_TEXT_LINE, KIND_TOKENS)

    cands = [_read_candidate(spec, crop, v, engine, cfg, region_px) for v in spec.variants]
    fr = resolve(spec.name, spec.side, cands, cfg, numeric, is_text)

    # Bounded targeted re-read: extra variants only when the field is not yet solid.
    extras = [v for v in EXTRA_VARIANT_ORDER if v not in spec.variants][: max(cfg.max_rereads, 0)]
    if fr.status in ("unreadable", "invalid", "single_read", "low_confidence") and extras:
        for v in extras:
            cands.append(_read_candidate(spec, crop, v, engine, cfg, region_px))
            fr = resolve(spec.name, spec.side, cands, cfg, numeric, is_text)
            fr.flags.append("reread_used")
            if fr.status in ("ok", "conflict"):
                break

    if spec.name == 'first_name' and spec.kind == KIND_TOKENS and fr.status in ('unreadable', 'invalid', 'conflict', 'low_confidence'):
        fr = _recover_name_line(spec, img, engine, cfg, fr)
    if spec.name == 'expiry_date' and fr.status in ('unreadable', 'invalid', 'single_read', 'low_confidence'):
        fr = _labelled_expiry(spec, img, engine, cfg, fr)
    if spec.kind == KIND_SERIAL and fr.value:
        _serial_checks(fr, crop, engine, cfg)
    return fr  # note: pattern-valid but wrong serials cannot be detected here




def _extract_lightweight(spec, img, engine, cfg):
    """Single minimal read, at most two selective retries; no full-image detection."""
    numeric = spec.kind in (KIND_DIGITS, KIND_DATE)
    threshold = max(cfg.min_numeric_conf if numeric else cfg.min_text_conf, .85 if numeric else .8)
    local_cfg = replace(cfg, min_numeric_conf=threshold, min_text_conf=threshold)
    candidates = []
    crop = crop_normalized(img, spec.box)
    for variant in ['original', 'clahe', 'up2'][:1 + min(max(cfg.max_rereads, 0), 2)]:
        cand = _read_candidate(spec, crop, variant, engine, cfg, _region_px(img, spec.box))
        if spec.kind == KIND_DIGITS:
            # Reject embedded letters rather than silently dropping uncertain characters.
            if re.search(r'[^0-9\s]', to_western_digits(normalize_unicode(cand.raw))):
                cand.value = None
                cand.notes.append('numeric_unexpected_characters')
            if cand.value and not decode_national_id(cand.value, cfg).valid:
                cand.value = None
                cand.notes.append('national_id_structure_invalid')
        if spec.name == 'expiry_date' and cand.value and parse_date(cand.value).precision != 'day':
            cand.value = None
            cand.notes.append('expiry_day_missing')
        candidates.append(cand)
        fr = resolve(spec.name, spec.side, candidates, local_cfg, numeric,
                     text=spec.kind in (KIND_TOKENS, KIND_TEXT_LINE))
        if fr.value and fr.confidence >= threshold and fr.status != 'conflict':
            fr.status = 'ok'
            fr.flags.append('single_pass_format_check' if len(candidates) == 1 else 'selective_retry')
            break
    # No universal serial pattern: preserve the read and flag it for review.
    if spec.kind == KIND_SERIAL and fr.value:
        fr.status = 'low_confidence'
        fr.flags.append('serial_format_unverified')
    return fr


def _recover_name_line(spec, img, engine, cfg, original):
    """A detector can split a short word into bogus tokens; retry whole-line recognition.

    Require agreement of both line reads and support from an existing candidate if
    the original was conflicted. Retain the disagreement and mark the output for review.
    """
    line_spec = replace(spec, kind=KIND_TEXT_LINE)
    crop = crop_normalized(img, spec.box)
    reads = [_read_candidate(line_spec, crop, variant, engine, cfg, _region_px(img, spec.box))
             for variant in ('gray', 'clahe')]
    recovered = resolve(spec.name, spec.side, reads, cfg, numeric=False, text=True)
    supported = {c.value for c in original.candidates if c.value}
    if recovered.status != 'ok' or (supported and recovered.value not in supported):
        return original
    recovered.candidates = original.candidates + reads
    recovered.flags = list(dict.fromkeys(original.flags + recovered.flags + ['line_segmentation_recovery']))
    recovered.source = 'line_segmentation_recovery'
    if original.status == 'conflict':
        recovered.status = 'low_confidence'  # do not conceal the detector/recognizer disagreement
    return recovered


def _labelled_expiry(spec, img, engine, cfg, original):
    """Locate the printed 'valid until' label, then read the digits to its left.

    Do not shorten/truncate digit strings to force a valid date, infer from issue
    date, or use any person's expected value. Only fully parsed day dates qualify.
    """
    if not hasattr(engine, 'read_layout'):
        return original
    search = (.18, .36, .88, .64)
    context = crop_normalized(img, search)
    h, w = img.shape[:2]
    xoffset, yoffset = int(search[0]*w), int(search[1]*h)
    boxes = []
    for variant in ('gray', 'clahe'):
        for read in engine.read_layout(make_variant(context, variant, cfg)):
            key = match_key(read.text)
            if digits_only(read.text) or not key.endswith(match_key('حتى')):
                continue
            if read.conf < cfg.min_text_conf:
                continue
            x0, y0, _, y1 = read.bbox
            # The left edge of this Arabic label is immediately after the date.
            right = xoffset+x0-2
            left = max(int(.18*w), right-int(.22*w))
            top, bottom = max(0,yoffset+y0-int(.03*h)), min(h,yoffset+y1+int(.02*h))
            if right-left < .12*w or bottom-top < 10:
                continue
            box = (left/w, top/h, right/w, bottom/h)
            if box not in boxes: boxes.append(box)
    cands = []
    for box in boxes[:2]:
        located = replace(spec, box=box)
        crop = crop_normalized(img, box)
        for variant in ('gray', 'clahe'):
            candidate = _read_candidate(located, crop, variant, engine, cfg, _region_px(img, box))
            if candidate.value and parse_date(candidate.value).precision != 'day':
                candidate.value = None
                candidate.notes.append('expiry_day_missing')
            candidate.notes.append('expiry_label_localized')
            cands.append(candidate)
    if not cands: return original
    original.candidates.extend(cands)
    recovered = resolve(spec.name, spec.side, original.candidates, cfg, numeric=True)
    # Never overwrite a different valid region result or ignore a date conflict.
    if recovered.status != 'ok':
        if recovered.status == 'conflict':
            original.value, original.status = None, 'conflict'
            original.flags.append('candidate_conflict')
        return original
    recovered.candidates = list(original.candidates)
    recovered.source = 'expiry_label_localized'
    recovered.flags.append('layout_recovery')
    return recovered


def _serial_checks(fr: FieldResult, crop: np.ndarray, engine: OcrEngine, cfg: OcrConfig) -> None:
    """Cross-check the serial with the Arabic reader and a configurable pattern."""
    g = make_variant(crop, "gray", cfg)
    other = normalize_serial(" ".join(r.text for r in engine.read_line(g, "ar", SERIAL_ALLOWLIST)))
    if other and other != fr.value:
        fr.flags.append("serial_reader_disagreement")
    if cfg.serial_pattern and not re.fullmatch(cfg.serial_pattern, fr.value or ""):
        fr.flags.append("serial_pattern_mismatch")
        if cfg.null_on_serial_pattern_mismatch:
            # Measured: under blur/low light the reader returned '1X…'/'1Y…' with confidence up to 0.94.
            # A wrong serial is worse than a missing one; the rejected read stays in the trace.
            fr.value, fr.status = None, "invalid"
        elif fr.status in ("ok", "single_read"):
            fr.status = "low_confidence"


# -------------------------------------------------------------- attributes row
def _vocab_match(token: str, cfg: OcrConfig) -> Optional[Tuple[str, str, float]]:
    """Best (field, canonical_value, ratio) for a token, or None. Matching uses a loose key only."""
    key = match_key(token)
    if not key:
        return None
    best: Optional[Tuple[str, str, float]] = None
    for fld, words in cfg.vocab.items():
        for w in words:
            r = SequenceMatcher(None, key, match_key(w)).ratio()
            if best is None or r > best[2]:
                best = (fld, w, r)
    return best if best and best[2] >= cfg.vocab_min_ratio else None


def _extract_attributes(spec: FieldSpec, img: np.ndarray, engine: OcrEngine, cfg: OcrConfig) -> Tuple[Dict[str, FieldResult], List[str]]:
    crop = crop_normalized(img, spec.box)
    _debug_save(cfg, spec, crop)
    region_px = _region_px(img, spec.box)
    per_field: Dict[str, List[Candidate]] = {f: [] for f in cfg.vocab}
    unmatched: List[str] = []
    lightweight = cfg.backend == 'paddle' or cfg.lightweight
    variants = ['original', 'clahe', 'up2'][:1 + min(max(cfg.max_rereads, 0), 2)] if lightweight else spec.variants
    for variant in variants:
        v = make_variant(crop, variant, cfg)
        reads = sorted(engine.read_tokens(v, "ar"), key=lambda r: -r.bbox[2])
        best_by_field: Dict[str, Candidate] = {}
        for r in reads:
            for tok in normalize_unicode(r.text).split(" "):
                m = _vocab_match(tok, cfg)
                if m is None:
                    if variant == spec.variants[0] and tok:
                        unmatched.append(tok)
                    continue
                fld, canon, ratio = m
                cand = Candidate(raw=r.text, normalized=normalize_unicode(tok), value=canon, conf=r.conf,
                                 variant=variant, reader="ar", bbox=region_px,
                                 notes=[] if ratio >= 0.999 else ["fuzzy_vocab_match"])
                if fld not in best_by_field or cand.conf > best_by_field[fld].conf:
                    best_by_field[fld] = cand
        for fld, c in best_by_field.items():
            per_field[fld].append(c)
        if lightweight and all(per_field[f] and per_field[f][-1].conf >= .8 for f in per_field):
            break
    out = {f: resolve(f, spec.side, c, replace(cfg, min_text_conf=max(.8, cfg.min_text_conf)) if lightweight else cfg,
                      numeric=False, text=False) for f, c in per_field.items()}
    if lightweight:
        for fr in out.values():
            if fr.status == 'single_read' and fr.confidence >= .8:
                fr.status = 'ok'
                fr.flags.append('single_pass_format_check')
    return out, unmatched


# ------------------------------------------------------------------ side driver
def _full_image_rows(img: np.ndarray, engine: OcrEngine, cfg: OcrConfig, allowlist: Optional[str] = None) -> List[List[RawRead]]:
    gray = make_variant(img, "gray", cfg)
    reads = engine.read_tokens(gray, "ar", allowlist)
    reads.sort(key=lambda r: (r.bbox[1] + r.bbox[3]) / 2)
    rows: List[List[RawRead]] = []
    for r in reads:
        cy, h = (r.bbox[1] + r.bbox[3]) / 2, max(r.bbox[3] - r.bbox[1], 1)
        if rows:
            last = rows[-1]
            ly = sum((x.bbox[1] + x.bbox[3]) / 2 for x in last) / len(last)
            if abs(cy - ly) < 0.6 * h:
                last.append(r)
                continue
        rows.append([r])
    return rows


def _national_id_fallback(img: np.ndarray, side: str, engine: OcrEngine, cfg: OcrConfig) -> FieldResult:
    """Full-image fallback: look for any row whose digit tokens concatenate to 14 digits."""
    cands: List[Candidate] = []
    for row in _full_image_rows(img, engine, cfg, DIGIT_ALLOWLIST):
        row.sort(key=lambda r: r.bbox[0])           # digits display left-to-right
        d = digits_only("".join(r.text for r in row))
        if len(d) == 14:
            cands.append(Candidate(raw=" ".join(r.text for r in row), normalized=d, value=d,
                                   conf=_min_conf(row), variant="gray", reader="ar", notes=["full_image_fallback"]))
    fr = resolve("national_id", side, cands, cfg, numeric=True)
    fr.source = "full_image_fallback"
    if fr.status == "ok":
        fr.status = "single_read"   # one pass over one image: never better than a single read
    return fr


def extract_side(img: np.ndarray, side: str, engine: OcrEngine, cfg: OcrConfig, numeric_recognizer=None) -> SideResult:
    import time
    t0 = time.perf_counter()
    fields: Dict[str, FieldResult] = {}
    lines: List[str] = []
    for spec in cfg.regions.get(side, []):
        if cfg.crop_margin:
            margin = max(0., min(cfg.crop_margin, .02))
            x0, y0, x1, y1 = spec.box
            spec = replace(spec, box=(max(0., x0-margin), max(0., y0-margin), min(1., x1+margin), min(1., y1+margin)))
        if spec.kind == KIND_ATTRIBUTES:
            sub, unmatched = _extract_attributes(spec, img, engine, cfg)
            fields.update(sub)
            row = [r.value for r in (sub.get(f) for f in ("gender", "religion", "marital_status")) if r and r.value]
            lines.append(" ".join(row + unmatched))
            continue
        if spec.kind == KIND_DIGITS and numeric_recognizer is not None:
            from .numeric import pad_field_box
            spec = replace(spec, box=pad_field_box(spec.box, cfg.numeric_crop_padding))
            reading = numeric_recognizer.recognize(crop_normalized(img, spec.box))
            candidate = Candidate(raw=reading['raw_text'], normalized=reading['normalized_text'],
                value=reading['value'] if reading['valid_structure'] else None,
                conf=reading['confidence'] or 0., variant=cfg.numeric_preprocess,
                reader=reading['backend'], bbox=_region_px(img,spec.box), notes=reading['flags'])
            fr = resolve(spec.name, side, [candidate], replace(cfg,min_numeric_conf=.85),numeric=True)
            if reading['requires_review'] and fr.value:
                fr.status = 'low_confidence'
            fr.flags.append('experimental_numeric_backend')
        else:
            fr = _extract_simple(spec, img, engine, cfg)
        fields[spec.name] = fr
        if fr.value and spec.kind != KIND_DIGITS:
            lines.append(fr.value)

    nid = fields.get("national_id")
    if (numeric_recognizer is None and getattr(engine, 'supports_detection', True) and not cfg.lightweight and nid is not None and cfg.full_image_pass in ("fallback", "always")
            and nid.status in ("unreadable", "invalid")):
        fb = _national_id_fallback(img, side, engine, cfg)
        if fb.value or fb.status == "conflict":
            fb.flags.append("fallback_used")
            fields["national_id"] = fb

    if getattr(engine, "supports_detection", True) and not cfg.lightweight and cfg.full_image_pass == "always":
        for row in _full_image_rows(img, engine, cfg):
            row.sort(key=lambda r: -r.bbox[2])
            txt, _ = clean_arabic_line(" ".join(r.text for r in row))
            if txt:
                lines.append(txt)

    return SideResult(side=side, fields=fields, all_text_lines=lines,
                      timings_ms={"extract": round((time.perf_counter() - t0) * 1000, 1)})


# -------------------------------------------------------------------- debugging
def _debug_save(cfg: OcrConfig, spec: FieldSpec, crop: np.ndarray) -> None:
    """Optional crop dump. Crops contain personal data: off unless cfg.debug_dir is set."""
    if not cfg.debug_dir:
        return
    os.makedirs(cfg.debug_dir, exist_ok=True)
    cv2.imwrite(os.path.join(cfg.debug_dir, f"{spec.side}_{spec.name}.png"), crop)
