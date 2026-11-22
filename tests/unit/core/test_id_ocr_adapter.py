"""Adapter tests. The pipeline here is a STAND-IN with the same call signature as id_ocr.IdOcrPipeline.process()
(front/back as ndarray|path, returns an object with .public): the real id_ocr package and EasyOCR are not imported.
The real-OCR check is tests/integration/test_real_ocr_pipeline.py (opt-in)."""
import io
import json
import logging
import sys
import types

import pytest

cv2 = pytest.importorskip("cv2", reason="OpenCV is needed to decode real JPEG/PNG bytes (it ships with easyocr)")
np = pytest.importorskip("numpy")

from bankwise.core.audit import AuditOutcome
from bankwise.core.errors import BankWiseError, InvalidInputError
from bankwise.core.images import ImageRejected
from bankwise.core.logging import LoggingSettings
from bankwise.core.pii import redact_text
from bankwise.core.security import Principal, reveal_sensitive
from bankwise.integration import id_ocr_adapter as adapter_module
from bankwise.integration.id_ocr_adapter import (
    NON_CODE, IdOcrAdapter, build_summary, create_id_ocr_adapter, decode_image, protect_ocr_logging,
)
from bankwise.integration.identity_processing import ProcessorOutput
from core_test_data import FAKE_NID
from test_identity_processing import ADDRESS_AR, NAME_AR, Rig

SCOPE = "Structural/consistency checks only. This result does not establish that the document is genuine."


def card_bytes(ext=".jpg", width=640, height=400, seed=0):
    rng = np.random.default_rng(seed)
    image = np.clip(160 + rng.normal(0, 25, (height, width, 3)), 0, 255).astype(np.uint8)
    ok, buffer = cv2.imencode(ext, image)
    assert ok
    return buffer.tobytes()


FRONT, BACK = card_bytes(".jpg"), card_bytes(".png", seed=1)


def realistic_public():
    """Same SHAPE as IdOcrPipeline's public JSON, with synthetic values only."""
    return {
        "side": "both", "national_id": FAKE_NID, "first_name": NAME_AR, "other_names": NAME_AR + " x",
        "address_line1": ADDRESS_AR, "address_line2": ADDRESS_AR, "job": "job-title-xyz", "birth_date": "1990-01-01",
        "serial": "AB1234567", "gender": "male", "religion": "RELIGION_X", "marital_status": "MARITAL_X",
        "issue_date": "2022-05", "expiry_date": "2029-05-14", "all_text_lines": [NAME_AR, ADDRESS_AR, FAKE_NID],
        "unreadable": ["job"],
        "validation": {
            "national_id": {"status": "valid_structure", "errors": ["invalid birth date 1990-02-30"],
                            "decoded": {"birth_date": "1990-01-01", "governorate_code": "88", "governorate": "GOV_X",
                                        "gender": "male"}},
            "birth_date_vs_national_id": "match", "gender_vs_national_id": "match", "national_id_front_vs_back": "match",
            "expiry_state": "valid", "date_order_issues": [], "serial_pattern_ok": True,
            "field_status": {"national_id": "ok", "first_name": "weak_read", "address_line2": "weak_read"},
            "field_flags": {"first_name": ["low_confidence"], "address_line2": ["spacing_differs"]},
            "unresolved": [], "input_errors": [], "note": SCOPE,
        },
        "decision": {"status": "MANUAL_REVIEW", "reasons": ["weak_read:first_name"], "flags": ["weak_read:address_line2"],
                     "image_quality": "good", "completeness": "complete"},
        "diagnostics": {"attempt": 1, "image_quality": {"front": {"ok": True, "blur_var": 229.6, "issues": []}},
                        "device": {"ar": "cuda", "en": "cuda"}, "ocr_calls": 29, "inference_ms": 1976.0,
                        "processing_ms": 2075.2},
    }


FORBIDDEN = [FAKE_NID, "010101", NAME_AR, ADDRESS_AR, "1990", "job-title-xyz", "AB1234567", "RELIGION_X", "MARITAL_X",
             "GOV_X", "2029-05-14", "2022-05", "1990-02-30", "male"]


class StandInPipeline:
    """Same signature as IdOcrPipeline.process; records what it receives."""

    def __init__(self, public=None, error=None):
        self.calls, self.warmups = [], 0
        self.public, self.error = public if public is not None else realistic_public(), error

    def warmup(self):
        self.warmups += 1

    def process(self, front=None, back=None, *, front_crop=None, back_crop=None, front_rotate=0, back_rotate=0,
                attempt=1, today=None):
        self.calls.append({"front": front, "back": back, "front_crop": front_crop, "attempt": attempt})
        if self.error:
            raise self.error
        return types.SimpleNamespace(public=self.public, decision=None, sides={})


# ================================================================== summary whitelist
def assert_no_pii(text):
    for forbidden in FORBIDDEN:
        assert forbidden not in text, forbidden
        assert json.dumps(forbidden)[1:-1] not in text, forbidden


def test_the_summary_carries_codes_and_states_but_no_personal_values():
    summary = build_summary(realistic_public())
    text = json.dumps(summary, ensure_ascii=False)
    assert_no_pii(text)
    assert summary["decision"]["status"] == "MANUAL_REVIEW" and summary["decision"]["reasons"] == ["weak_read:first_name"]
    assert summary["checks"] == {"id_structure": "valid_structure", "dob_vs_id": "match", "gender_vs_id": "match",
                                 "id_front_vs_back": "match", "expiry": "valid", "serial_pattern_ok": True}
    assert "national_id:ok" in summary["field_status"] and "first_name:weak_read" in summary["field_status"]
    assert summary["field_flags"] == ["first_name:low_confidence", "address_line2:spacing_differs"]
    assert {"national_id", "first_name", "address_line1", "birth_date"} <= set(summary["fields_present"])
    assert "all_text_lines" not in summary["fields_present"] and "validation" not in summary["fields_present"]
    assert summary["diagnostics"]["device"] == {"ar": "cuda", "en": "cuda"} and summary["diagnostics"]["ocr_calls"] == 29
    assert summary["diagnostics"]["image_quality"]["front"]["blur_var"] == 229.6


def test_the_summary_survives_the_generic_redactor_unchanged():
    from bankwise.core.pii import redact
    summary = build_summary(realistic_public())
    assert redact(summary) == summary                         # nothing in it looks sensitive, so nothing is lost


def test_unknown_new_fields_and_free_text_never_reach_the_summary():
    public = realistic_public()
    public["brand_new_field"] = "SECRET-NEW-VALUE"
    public["decision"]["reasons"] = ["weak_read:first_name", NAME_AR, "x" * 200, "has\nnewline"]
    public["decision"][NAME_AR] = "key-with-name"
    public["diagnostics"]["notes"] = [NAME_AR]
    text = json.dumps(build_summary(public), ensure_ascii=False)
    assert "SECRET-NEW-VALUE" not in text and NAME_AR not in text and "newline" not in text
    assert text.count(NON_CODE) >= 3


@pytest.mark.parametrize("broken", [{}, {"validation": "nope", "decision": 5, "diagnostics": [1]},
                                    {"validation": {"national_id": "x", "field_status": [1, 2]}}, {"decision": None}])
def test_malformed_or_partial_public_dicts_do_not_crash_the_projection(broken):
    summary = build_summary(broken)
    assert set(summary) >= {"decision", "checks", "field_status", "diagnostics"}


def test_deeply_nested_structures_are_cut_off():
    deep = current = {}
    for _ in range(30):
        current["a"] = {}
        current = current["a"]
    assert NON_CODE in json.dumps(build_summary({"decision": deep}))


# ================================================================== decoding
def test_real_jpeg_and_png_bytes_decode_in_memory_to_bgr_arrays():
    for data in (FRONT, BACK):
        array = decode_image(data)
        assert isinstance(array, np.ndarray) and array.dtype == np.uint8 and array.shape == (400, 640, 3)


@pytest.mark.parametrize("data", [FRONT[:200], b"\xff\xd8\xff\xe0" + b"\x00" * 300, BACK[:60]])
def test_bytes_that_pass_the_header_check_but_cannot_be_decoded_are_rejected(data):
    with pytest.raises(ImageRejected) as info:
        decode_image(data)
    assert info.value.reason == "undecodable"


def test_a_decoder_exception_is_mapped_to_a_validation_error_with_no_details(monkeypatch):
    def explode(*args, **kwargs):
        raise cv2.error("internal decoder failure at /home/user/secret.jpg")

    monkeypatch.setattr(cv2, "imdecode", explode)
    with pytest.raises(ImageRejected) as info:
        decode_image(FRONT)
    assert info.value.reason == "undecodable" and "/home/user" not in repr(info.value.to_public()) + info.value.detail
    assert info.value.__suppress_context__ is True


def test_the_pipeline_receives_arrays_never_bytes_or_paths():
    pipeline = StandInPipeline()
    output = IdOcrAdapter(pipeline).process(FRONT, BACK)
    (call,) = pipeline.calls
    for side in ("front", "back"):
        assert isinstance(call[side], np.ndarray) and not isinstance(call[side], (bytes, str))
    assert call["front_crop"] is None and call["attempt"] == 1                  # nothing else is passed through
    assert isinstance(output, ProcessorOutput) and output.raw["national_id"] == FAKE_NID


def test_the_adapter_writes_no_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    IdOcrAdapter(StandInPipeline()).process(FRONT, BACK)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("result", [None, types.SimpleNamespace(), types.SimpleNamespace(public="text"),
                                    types.SimpleNamespace(public=[1])])
def test_results_without_a_public_mapping_are_a_failure(result):
    pipeline = StandInPipeline()
    pipeline.process = lambda **k: result
    with pytest.raises(TypeError):
        IdOcrAdapter(pipeline).process(FRONT, BACK)


def test_warmup_is_delegated_and_optional():
    pipeline = StandInPipeline()
    IdOcrAdapter(pipeline).warmup()
    assert pipeline.warmups == 1
    IdOcrAdapter(types.SimpleNamespace()).warmup()                                # no warmup attribute: fine


# ================================================================== through the full security stack
@pytest.fixture
def rig():
    pipeline = StandInPipeline()
    stack = Rig(processor=IdOcrAdapter(pipeline), process_limit=20)
    stack.pipeline = pipeline
    return stack


def test_end_to_end_gates_then_decode_then_ocr_then_audit(rig):
    stream = io.StringIO()
    from bankwise.core.logging import configure_logging
    configure_logging(LoggingSettings(), stream)
    result = rig.run(front=FRONT, back=BACK)
    assert len(rig.pipeline.calls) == 1 and result.summary["decision"]["status"] == "MANUAL_REVIEW"
    everything = stream.getvalue() + json.dumps(result.summary, ensure_ascii=False) + repr(result) \
        + repr([e.to_dict() for e in rig.sink.events])
    assert_no_pii(everything)
    assert rig.sink.events[-1].outcome is AuditOutcome.SUCCESS and rig.sink.events[-1].event_type == "identity.process"
    reviewer = Principal("rev-1", frozenset({"reviewer"}))
    raw = reveal_sensitive(result.sensitive, reviewer, rig.authorizer, "manual review", rig.sink)
    assert raw["national_id"] == FAKE_NID and raw["all_text_lines"][0] == NAME_AR


@pytest.mark.parametrize("roles,exc", [((), "PermissionDenied"), (("operator",), "PermissionDenied")])
def test_no_permission_means_the_pipeline_is_never_called(rig, roles, exc):
    from bankwise.core.errors import PermissionDenied
    with pytest.raises(PermissionDenied):
        rig.run(rig.header(roles), front=FRONT, back=BACK)
    assert rig.pipeline.calls == []


def test_a_header_valid_but_undecodable_image_is_a_validation_error_and_never_reaches_ocr(rig):
    broken = FRONT[:300]                                                       # JPEG signature, SOF kept, data cut
    with pytest.raises(InvalidInputError) as info:
        rig.run(front=broken, back=BACK)
    assert info.value.to_public()["code"] == "validation_failed" and rig.pipeline.calls == []
    event = rig.sink.events[-1]
    assert (event.outcome, event.error_code) == (AuditOutcome.FAILURE, "validation_failed")
    assert dict(event.metadata) == {"stage": "decode", "reason": "undecodable"}


def test_pipeline_failures_are_generic_and_leak_nothing(rig):
    class EngineError(Exception):
        pass

    stream = io.StringIO()
    from bankwise.core.logging import configure_logging
    configure_logging(LoggingSettings(), stream)
    rig.pipeline.error = EngineError(f"models missing for {FAKE_NID} {NAME_AR} at /home/user/models")
    with pytest.raises(BankWiseError) as info:
        rig.run(front=FRONT, back=BACK)
    assert info.value.to_public() == {"code": "internal_error", "message": "An internal error occurred."}
    assert_no_pii(stream.getvalue() + info.value.detail + repr([e.to_dict() for e in rig.sink.events]))
    assert "/home/user" not in info.value.detail and rig.sink.events[-1].metadata["error_type"] == "EngineError"


def test_images_at_the_limits_are_handled():
    ok, buffer = cv2.imencode(".png", np.full((3000, 4000, 3), 120, np.uint8))     # 12 MP, compresses to a few KB
    assert ok
    big = buffer.tobytes()
    stack = Rig(processor=IdOcrAdapter(StandInPipeline()), process_limit=20)
    assert stack.run(front=big, back=BACK).request_id
    tiny_limit = Rig(processor=IdOcrAdapter(StandInPipeline()), process_limit=20)
    from bankwise.core.config import IdentityProcessingSettings
    tiny_limit.service._settings = IdentityProcessingSettings(max_pixels=100_000)
    with pytest.raises(ImageRejected) as info:
        tiny_limit.run(front=big, back=BACK)
    assert info.value.reason == "too_many_pixels"


# ================================================================== OCR logger protection
def test_ocr_logging_is_redacted_and_the_pipelines_info_line_is_dropped():
    stream = io.StringIO()
    logger = protect_ocr_logging(stream=stream)
    logging.getLogger("id_ocr").setLevel(logging.INFO)                         # what IdOcrPipeline.__init__ does
    pipeline_log = logging.getLogger("id_ocr")
    pipeline_log.info("done sides=%s decision=%s nid=%s", ["front"], "MANUAL_REVIEW", "2*********67")
    pipeline_log.warning("side=%s rejected: %s", "front", "image_too_small")
    pipeline_log.error("internal error: %s", f"bad {FAKE_NID} password=hunter2hunter2")
    out = stream.getvalue()
    assert "2*********67" not in out and "MANUAL_REVIEW" not in out            # the INFO line never appears
    assert "image_too_small" in out and FAKE_NID not in out and "hunter2" not in out
    assert logger.propagate is False and len([h for h in logger.handlers if getattr(h, "_bankwise_managed", False)]) == 1
    protect_ocr_logging(stream=stream)
    assert len([h for h in logger.handlers if getattr(h, "_bankwise_managed", False)]) == 1       # idempotent
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    logger.propagate = True
    logger.setLevel(logging.NOTSET)


def test_a_lower_requested_log_level_is_raised_to_warning():
    logger = protect_ocr_logging(LoggingSettings(level="DEBUG"), io.StringIO())
    assert all(h.level >= logging.WARNING for h in logger.handlers if getattr(h, "_bankwise_managed", False))
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    logger.propagate = True


def test_masked_ids_are_redacted_in_free_text():
    for masked in ("2*********67", "2***********", "id=2xxxxxxxxxx67", "\u2022\u2022\u2022\u2022\u2022\u2022\u202267"):
        assert "***" not in redact_text(masked) and "xxx" not in redact_text(masked) and "\u2022" not in redact_text(masked)
    assert redact_text("score 5*3 and 2 * 4") == "score 5*3 and 2 * 4"


# ================================================================== factory (stand-in id_ocr module)
def test_the_factory_builds_the_pipeline_with_safe_settings(monkeypatch):
    created = {}

    class OcrConfig:
        def __init__(self, **kwargs):
            created["cfg"] = kwargs

    class IdOcrPipeline:
        def __init__(self, cfg):
            created["pipeline"] = self
            logging.getLogger("id_ocr").setLevel(logging.INFO)

        def warmup(self):
            created["warmed"] = True

    module = types.ModuleType("id_ocr")
    module.IdOcrPipeline, module.OcrConfig = IdOcrPipeline, OcrConfig
    monkeypatch.setitem(sys.modules, "id_ocr", module)
    adapter = create_id_ocr_adapter(model_dir="models", use_gpu=False, allow_cpu_fallback=True)
    assert created["cfg"] == {"use_gpu": False, "allow_cpu_fallback": True, "model_dir": "models",
                              "download_models": False, "debug_dir": None, "backend": "easyocr",
                              "paddle_model_dir": None, "cpu_threads": 4, "yolo_weights": None}
    assert created["warmed"] is True and isinstance(adapter, IdOcrAdapter)
    created.clear()
    create_id_ocr_adapter(warm_up=False)
    assert "warmed" not in created and created["cfg"]["download_models"] is False and created["cfg"]["debug_dir"] is None
    logger = logging.getLogger("id_ocr")
    assert any(getattr(h, "_bankwise_managed", False) for h in logger.handlers)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    logger.propagate = True
    logger.setLevel(logging.NOTSET)
