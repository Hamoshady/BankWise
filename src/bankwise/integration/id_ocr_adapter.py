"""Adapter that connects `IdentityProcessingService` to the real `id_ocr.IdOcrPipeline`.

Facts about the pipeline this relies on (read from src/id_ocr/pipeline.py):
* `IdOcrPipeline.process(front=..., back=..., ...)` accepts a path string OR a numpy array per side, and returns an
  `IdOcrResult` whose `.public` is the JSON-ready dict. Arrays are checked by the pipeline's own `validate_array`.
* So this adapter decodes the already-validated image BYTES in memory and passes arrays. No temporary file is ever
  written and no path is ever passed, so there is nothing to clean up and no path to inject.
* `.public` contains personal data (national_id, names, addresses, all_text_lines, ...). It is returned as `raw`
  (the service wraps it in `Sensitive`), and a separate codes-only `summary` is built here by WHITELIST: only
  decision codes, check states, per-field status/flags, quality numbers and timings are copied, and every string must
  look like a code (ASCII letters, digits and `_:.- `). Anything else (Arabic text, values, dates) becomes
  "<non_code>" or is dropped, so a new field added to the pipeline can never leak into the summary by default.

Assumptions to verify with the opt-in real-OCR test (tests/integration/test_real_ocr_pipeline.py): that an array
decoded by `cv2.imdecode(..., IMREAD_COLOR)` (BGR, like `cv2.imread`) gives the same OCR result as the same file passed
by path. This adapter does not apply a timeout; a hung OCR run keeps its concurrency slot."""
from __future__ import annotations

import dataclasses
import logging
import re
from typing import Any, Dict, List, Mapping, Optional

from bankwise.core.images import ImageRejected
from bankwise.core.logging import LoggingSettings, configure_logging
from bankwise.integration.identity_processing import ProcessorOutput

OCR_LOGGER = "id_ocr"
MAX_ITEMS = 50
_CODE_RE = re.compile(r"^[A-Za-z0-9_:.\- ]{1,80}$")
_STRUCTURAL_KEYS = frozenset({"validation", "decision", "diagnostics", "side", "all_text_lines", "unreadable"})
NON_CODE = "<non_code>"


def _code(value: Any) -> Any:
    """A short ASCII code, a number/bool/None, or the placeholder. Never free text."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str) and value.isascii() and _CODE_RE.match(value):
        return value
    return NON_CODE


def _codes_only(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return NON_CODE
    if isinstance(value, Mapping):
        return {key: _codes_only(item, depth + 1) for key, item in list(value.items())[:MAX_ITEMS]
                if isinstance(key, str) and key.isascii() and _CODE_RE.match(key)}
    if isinstance(value, (list, tuple)):
        return [_codes_only(item, depth + 1) for item in list(value)[:MAX_ITEMS]]
    return _code(value)


def _pairs(mapping: Any, flat: bool) -> List[str]:
    """{'national_id': 'ok'} -> ['national_id:ok'] so field NAMES never become keys (a key like `national_id`
    would be hidden by the generic redactor) and only codes appear."""
    out: List[str] = []
    if not isinstance(mapping, Mapping):
        return out
    for name, state in list(mapping.items())[:MAX_ITEMS]:
        if not (isinstance(name, str) and _CODE_RE.match(name)):
            continue
        for item in ([state] if flat else (state if isinstance(state, (list, tuple)) else [])):
            code = _code(item)
            if isinstance(code, str):
                out.append(f"{name}:{code}")
    return out


def build_summary(public: Mapping[str, Any]) -> Dict[str, Any]:
    """Codes-only projection of the pipeline's public JSON. See the module docstring."""
    validation = public.get("validation") if isinstance(public.get("validation"), Mapping) else {}
    id_state = validation.get("national_id") if isinstance(validation.get("national_id"), Mapping) else {}
    diagnostics = public.get("diagnostics") if isinstance(public.get("diagnostics"), Mapping) else {}
    return {
        "side": _code(public.get("side")),
        "decision": _codes_only(public.get("decision")),
        "checks": {
            "id_structure": _code(id_state.get("status")),
            "dob_vs_id": _code(validation.get("birth_date_vs_national_id")),
            "gender_vs_id": _code(validation.get("gender_vs_national_id")),
            "id_front_vs_back": _code(validation.get("national_id_front_vs_back")),
            "expiry": _code(validation.get("expiry_state")),
            "serial_pattern_ok": _code(validation.get("serial_pattern_ok")),
        },
        "field_status": _pairs(validation.get("field_status"), flat=True),
        "field_flags": _pairs(validation.get("field_flags"), flat=False),
        "unresolved": _codes_only(validation.get("unresolved")),
        "date_order_issues": _codes_only(validation.get("date_order_issues")),
        "input_errors": _codes_only(validation.get("input_errors")),
        "fields_present": sorted(str(name) for name, value in public.items()
                                 if name not in _STRUCTURAL_KEYS and isinstance(name, str) and _CODE_RE.match(name)
                                 and value not in (None, "", [], {})),
        "unreadable": _codes_only(public.get("unreadable")),
        "diagnostics": {
            "attempt": _code(diagnostics.get("attempt")),
            "image_quality": _codes_only(diagnostics.get("image_quality")),
            "device": _codes_only(diagnostics.get("device")),
            "ocr_calls": _code(diagnostics.get("ocr_calls")),
            "inference_ms": _code(diagnostics.get("inference_ms")),
            "processing_ms": _code(diagnostics.get("processing_ms")),
        },
    }


def decode_image(data: bytes) -> Any:
    """Decode validated image bytes to a BGR array in memory (same colour order as cv2.imread)."""
    import cv2
    import numpy as np

    try:
        array = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    except cv2.error:
        raise ImageRejected("undecodable") from None
    if array is None or array.ndim != 3 or array.size == 0:
        raise ImageRejected("undecodable")
    return array


class IdOcrAdapter:
    """Implements `IdentityProcessor` on top of an `IdOcrPipeline`-like object (anything with
    `process(front=..., back=...)` returning an object with a `.public` mapping)."""

    def __init__(self, pipeline: Any) -> None:
        self._pipeline = pipeline

    def warmup(self) -> None:
        """Load the OCR readers now (EngineError here is a deployment problem and should stop start-up)."""
        warm = getattr(self._pipeline, "warmup", None)
        if callable(warm):
            warm()

    def close(self) -> None:
        """Release the locally owned OCR readers on application shutdown."""
        engine = getattr(self._pipeline, "engine", None)
        cleanup = getattr(engine, "cleanup", None)
        if callable(cleanup):
            cleanup()

    def process(self, front_image: bytes, back_image: bytes) -> ProcessorOutput:
        front, back = decode_image(front_image), decode_image(back_image)
        try:
            result = self._pipeline.process(front=front, back=back)
        finally:
            del front, back
        public = getattr(result, "public", None)
        if not isinstance(public, Mapping):
            raise TypeError("the OCR result has no public mapping")
        return ProcessorOutput(raw=public, summary=build_summary(public))


def protect_ocr_logging(settings: Optional[LoggingSettings] = None, stream: Any = None) -> logging.Logger:
    """Put the redacting handler on the `id_ocr` logger and keep it at WARNING or above. The pipeline's INFO line
    ("done ... nid=<masked>") would otherwise reach the root logger unredacted, and an already-masked id is still partial
    information. Call it AFTER constructing the pipeline: the pipeline sets its own logger level in its constructor
    (the handler level stays at WARNING either way)."""
    settings = settings or LoggingSettings(level="WARNING")
    if settings.level not in ("WARNING", "ERROR", "CRITICAL"):
        settings = dataclasses.replace(settings, level="WARNING")
    return configure_logging(settings, stream, logger_name=OCR_LOGGER)


def create_id_ocr_adapter(model_dir: Optional[str] = None, use_gpu: bool = True, allow_cpu_fallback: bool = False,
                          warm_up: bool = True, log_settings: Optional[LoggingSettings] = None,
                          backend: str = "easyocr", paddle_model_dir: Optional[str] = None, cpu_threads: int = 4, yolo_weights: Optional[str] = None) -> IdOcrAdapter:
    """Build the real pipeline with application-safe settings: models are NEVER downloaded while serving
    (`download_models=False`: install them beforehand), no debug crops are saved (`debug_dir=None`), and OCR logging is
    redacted. Mirrors the OcrConfig arguments the project's own CLI uses."""
    from id_ocr import IdOcrPipeline, OcrConfig            # imported here: heavy, and not needed to import this module

    cfg = OcrConfig(use_gpu=use_gpu, allow_cpu_fallback=allow_cpu_fallback, model_dir=model_dir,
                    download_models=False, debug_dir=None, backend=backend, paddle_model_dir=paddle_model_dir, cpu_threads=cpu_threads, yolo_weights=yolo_weights)
    pipeline = IdOcrPipeline(cfg)
    protect_ocr_logging(log_settings)
    adapter = IdOcrAdapter(pipeline)
    if warm_up:
        adapter.warmup()
    return adapter
