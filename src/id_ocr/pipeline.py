"""Public entry point: image(s) -> structured JSON-ready result.

    from id_ocr import IdOcrPipeline, OcrConfig
    pipe = IdOcrPipeline(OcrConfig(model_dir="models"))
    result = pipe.process(front="front.jpg", back="back.jpg")
    result.public      # dict with the existing logical schema (+ validation/decision/diagnostics)
    result.trace()     # redacted intermediate details (variants, confidences, flags)

The pipeline is a plain deterministic function of its inputs and can be called
by the Banking Agent as a tool. It does not depend on any agent framework.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

from .config import Box, OcrConfig
from .decision import Decision, DecisionInput, decide
from .engine import EngineError, OcrEngine, get_engine
from .extract import PUBLIC_FIELDS, FieldResult, SideResult, extract_side
from .image_io import (ImageError, QualityReport, assess_quality, crop_normalized, load_image, rotate_image,
                       validate_array)
from .merge import merge_sides
from .normalize import mask_national_id
from .validation import (cross_check_birth_date, cross_check_gender, date_order_issues, decode_national_id,
                         expiry_state)

log = logging.getLogger("id_ocr")
ImageInput = Union[str, np.ndarray, None]

SCOPE_NOTE = ("Structural/consistency checks only. This result does not establish that the document is genuine "
              "or that it belongs to the person presenting it.")


@dataclass
class IdOcrResult:
    public: Dict[str, Any]
    decision: Decision
    sides: Dict[str, SideResult] = field(default_factory=dict)

    def to_json(self, redact: bool = False, indent: int = 2) -> str:
        return json.dumps(redact_public(self.public) if redact else self.public, ensure_ascii=False, indent=indent)

    def trace(self, include_text: bool = False) -> Dict[str, Any]:
        """Intermediate OCR details, kept out of the public JSON. Text is redacted unless asked for."""
        out: Dict[str, Any] = {}
        for side, sr in self.sides.items():
            out[side] = {}
            for name, fr in sr.fields.items():
                out[side][name] = {
                    "status": fr.status, "flags": fr.flags, "source": fr.source,
                    "min_read_confidence": None if fr.confidence is None else round(fr.confidence, 3),
                    "candidates": [{
                        "variant": c.variant, "reader": c.reader, "confidence": round(c.conf, 3), "notes": c.notes,
                        "bbox": c.bbox, "parsed": c.value is not None,
                        **({"raw": c.raw, "normalized": c.normalized, "value": c.value} if include_text
                           else {"raw_chars": len(c.raw)}),
                    } for c in fr.candidates],
                }
        return out


def redact_public(public: Dict[str, Any]) -> Dict[str, Any]:
    """Log/share-safe copy: masks the id and hides text values and dates of birth."""
    r = json.loads(json.dumps(public))
    if r.get("national_id"):
        r["national_id"] = mask_national_id(r["national_id"])
    for k in ("first_name", "other_names", "address_line1", "address_line2", "job"):
        if r.get(k):
            r[k] = f"<{len(r[k])} chars>"
    if r.get("birth_date"):
        r["birth_date"] = r["birth_date"][:4] + "-**-**"
    if r.get("serial"):
        r["serial"] = r["serial"][:2] + "*" * (len(r["serial"]) - 2)
    # religion, marital status and gender are personal attributes: report only that a value exists
    for k in ("gender", "religion", "marital_status"):
        if r.get(k):
            r[k] = "<set>"
    r["all_text_lines"] = [f"<{len(x)} chars>" for x in r.get("all_text_lines", [])]
    dec = r.get("validation", {}).get("national_id", {}).get("decoded")
    if dec:
        dec["birth_date"] = (dec.get("birth_date") or "")[:4] + "-**-**" if dec.get("birth_date") else None
        # place of birth and gender decoded from the id are as identifying as the fields above
        for k in ("governorate_code", "governorate", "gender"):
            if dec.get(k):
                dec[k] = "<hidden>"
    return r


class IdOcrPipeline:
    def __init__(self, cfg: Optional[OcrConfig] = None, engine: Optional[OcrEngine] = None):
        self.cfg = cfg or OcrConfig()
        logging.getLogger("id_ocr").setLevel(self.cfg.log_level)
        self.engine = engine or get_engine(self.cfg)

    # ------------------------------------------------------------------ setup
    def warmup(self) -> Dict[str, Any]:
        """Initialise all readers needed by the configured regions (once). May raise EngineError."""
        self.engine.reader("ar")
        if any(s.kind == "serial" for specs in self.cfg.regions.values() for s in specs):
            self.engine.reader("en")
        return self.engine.info()

    # ---------------------------------------------------------------- process
    def process(self, front: ImageInput = None, back: ImageInput = None, *,
                front_crop: Optional[Box] = None, back_crop: Optional[Box] = None,
                front_rotate: int = 0, back_rotate: int = 0,
                attempt: int = 1, today: Optional[date] = None) -> IdOcrResult:
        """Run the full pipeline. ``*_crop`` is an optional normalised (x0,y0,x1,y1) card crop."""
        today = today or date.today()
        self.warmup()                       # EngineError propagates: it is a deployment problem, not bad input
        t_start = time.perf_counter()
        base = (self.engine.calls, self.engine.inference_ms)

        input_errors: List[str] = []
        sides: Dict[str, SideResult] = {}
        quality: Dict[str, QualityReport] = {}
        try:
            for side, src, crop, rot in (("front", front, front_crop, front_rotate), ("back", back, back_crop, back_rotate)):
                if src is None:
                    continue
                try:
                    img = load_image(src, self.cfg) if isinstance(src, str) else validate_array(src, self.cfg)
                    img = rotate_image(img, rot)
                    if crop is not None:
                        img = crop_normalized(img, crop)
                    quality[side] = assess_quality(img, self.cfg)
                    sides[side] = extract_side(img, side, self.engine, self.cfg)
                    del img
                except ImageError as exc:
                    log.warning("side=%s rejected: %s", side, exc.code)
                    input_errors.append(exc.code)
            return self._finish(sides, quality, input_errors, attempt, today, t_start, base)
        except EngineError:
            raise
        except Exception as exc:  # unexpected: never leak details to the caller's end user
            log.error("internal error: %s", type(exc).__name__, exc_info=log.isEnabledFor(logging.DEBUG))
            dec = Decision("MANUAL_REVIEW", ["internal_error"], [], "poor", "incomplete")
            pub = _empty_public(list(sides) or ["front"])
            pub["decision"] = dec.as_dict()
            return IdOcrResult(public=pub, decision=dec, sides=sides)

    # ----------------------------------------------------------------- finish
    def _finish(self, sides: Dict[str, SideResult], quality: Dict[str, QualityReport], input_errors: List[str],
                attempt: int, today: date, t_start: float, base: Tuple[int, float]) -> IdOcrResult:
        cfg = self.cfg
        supplied = [s for s in ("front", "back") if s in sides]
        merged: Optional[SideResult]
        if len(supplied) == 2:
            merged = merge_sides(sides["front"], sides["back"])
        elif supplied:
            merged = sides[supplied[0]]
        else:
            merged = None

        fields: Dict[str, FieldResult] = merged.fields if merged else {}
        val = lambda n: fields[n].value if n in fields else None  # noqa: E731

        # ---- validation (OCR values stay distinct from decoded values) ----
        nid = val("national_id")
        dec_nid = decode_national_id(nid, cfg, today) if nid else None
        if nid:
            nid_state = "valid" if dec_nid.valid else "invalid"       # type: ignore[union-attr]
        elif "national_id" in fields and fields["national_id"].status == "conflict":
            nid_state = "unresolved"
        else:
            nid_state = "missing"

        id_sides = "unavailable"
        if len(supplied) == 2:
            fv, bv = sides["front"].fields.get("national_id"), sides["back"].fields.get("national_id")
            if fv and bv and fv.value and bv.value:
                id_sides = "match" if fv.value == bv.value else "mismatch"
        bd_check = cross_check_birth_date(val("birth_date"), dec_nid.birth_date if dec_nid else None)
        g_check = cross_check_gender(val("gender"), dec_nid.gender if dec_nid else None)
        exp_state = expiry_state(val("expiry_date"), today)
        date_issues = date_order_issues(val("issue_date"), val("expiry_date"), today)

        required = [f for s in supplied for f in cfg.required_fields.get(s, [])]
        critical = [sp.name for s in supplied for sp in cfg.regions.get(s, []) if sp.critical]
        dinp = DecisionInput(
            sides_supplied=supplied, input_errors=input_errors,
            quality_issues={s: q.issues for s, q in quality.items()},
            field_status={n: fr.status for n, fr in fields.items()},
            required_fields=list(dict.fromkeys(required)), critical_fields=list(dict.fromkeys(critical)),
            nid_structure=nid_state, birth_date_check=bd_check, gender_check=g_check, id_sides_check=id_sides,
            expiry_state=exp_state, date_issues=date_issues, attempt=attempt,
        )
        decision = decide(dinp, cfg)

        # ---- public JSON ----
        public = _empty_public(supplied or ["front"])
        for name in PUBLIC_FIELDS:
            public[name] = val(name)
        public["all_text_lines"] = list(merged.all_text_lines) if merged else []
        public["unreadable"] = [n for n in PUBLIC_FIELDS if n in fields and fields[n].value is None]
        serial = fields.get("serial")
        public["validation"] = {
            "national_id": {
                "status": {"valid": "valid_structure", "invalid": "invalid_structure"}.get(nid_state, nid_state),
                "errors": dec_nid.errors if dec_nid else [],
                "decoded": ({"birth_date": dec_nid.birth_date, "governorate_code": dec_nid.governorate_code,
                             "governorate": dec_nid.governorate, "gender": dec_nid.gender} if dec_nid else None),
            },
            "birth_date_vs_national_id": bd_check,
            "gender_vs_national_id": g_check,
            "national_id_front_vs_back": id_sides,
            "expiry_state": exp_state,
            "date_order_issues": date_issues,
            "serial_pattern_ok": None if not (serial and serial.value) else "serial_pattern_mismatch" not in serial.flags,
            "field_status": {n: fr.status for n, fr in fields.items()},
            "field_flags": {n: fr.flags for n, fr in fields.items() if fr.flags},
            "unresolved": sorted(n for n, fr in fields.items() if fr.status == "conflict"),
            "input_errors": input_errors,
            "note": SCOPE_NOTE,
        }
        public["decision"] = decision.as_dict()
        eng = self.engine.info()
        public["diagnostics"] = {
            "attempt": attempt,
            "image_quality": {s: q.as_dict() for s, q in quality.items()},
            "device": eng.get("readers"),
            "ocr_calls": self.engine.calls - base[0],
            "inference_ms": round(self.engine.inference_ms - base[1], 1),
            "processing_ms": round((time.perf_counter() - t_start) * 1000, 1),
        }
        log.info("done sides=%s decision=%s nid=%s", supplied, decision.status, mask_national_id(nid))
        return IdOcrResult(public=public, decision=decision, sides=sides)


def _empty_public(supplied: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"side": "both" if len(supplied) == 2 else (supplied[0] if supplied else "front")}
    out.update({n: None for n in PUBLIC_FIELDS})
    out["all_text_lines"] = []
    out["unreadable"] = []
    return out
