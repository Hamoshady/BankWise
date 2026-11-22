"""Engine, pipeline and error-handling tests.

Most tests use a scripted FakeEngine (no model, no GPU, deterministic) fed with SYNTHETIC images and
identities. The last class runs the real EasyOCR pipeline on ``test_data/`` and is skipped unless
EasyOCR is importable and ``BANKWISE_RUN_OCR=1`` is set (it needs model weights; ``BANKWISE_CPU=1``
forces CPU).
"""
import glob
import json
import logging
import os
import re
import sys
import types
from datetime import date

import cv2
import numpy as np
import pytest

import id_ocr.extract as extract_mod
from id_ocr import IdOcrPipeline, OcrConfig, redact_public
from id_ocr.engine import EngineError, OcrEngine, RawRead, get_engine
from id_ocr.extract import DIGIT_ALLOWLIST

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TODAY = date(2026, 10, 20)

# ---- synthetic identity (not a real person) ---------------------------------------------------------
NID = "30001010100011"          # 2000-01-01, governorate 01, male
FIRST, OTHERS = "أحمد", "علي محمود حسن"
ADDR1, ADDR2 = "شارع النيل", "مدينة المنصورة"
SERIAL = "AB1234567"


def ar(s: str) -> str:
    return s.translate({ord(str(i)): c for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")})


# ================================================================================ fake engine
class Seq:
    """Successive responses for repeated reads of the same region (last one repeats)."""
    def __init__(self, *responses):
        self.responses = list(responses)

    def next(self):
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def R(text, conf=0.9, bbox=(0, 0, 10, 10)):
    return RawRead(text=text, conf=conf, bbox=bbox)


class FakeEngine:
    """Looks up the scripted answer by the grey level painted into the requested region."""
    def __init__(self, lines, tokens=None):
        self.lines, self.tokens = lines, tokens or {}
        self.calls, self.inference_ms = 0, 0.0

    @staticmethod
    def _level(img):
        return int(round(float(np.median(img))))

    def _lookup(self, table, img):
        lvl = self._level(img)
        key = min(table, key=lambda k: abs(k - lvl)) if table else None
        if key is None or abs(key - lvl) > 4:
            return []
        v = table[key]
        return v.next() if isinstance(v, Seq) else v

    def reader(self, key):
        return object()

    def info(self):
        return {"readers": {"ar": "fake"}}

    def read_line(self, img, reader_key="ar", allowlist=None):
        self.calls += 1
        return list(self._lookup(self.lines, img))

    def read_tokens(self, img, reader_key="ar", allowlist=None):
        self.calls += 1
        return list(self._lookup(self.tokens, img))


@pytest.fixture(autouse=True)
def plain_variants(monkeypatch):
    """CLAHE would shift the uniform test patches; the fake needs stable grey levels."""
    def gray_only(img, name, cfg):
        return img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    monkeypatch.setattr(extract_mod, "make_variant", gray_only)


def make_card(side, cfg, levels, w=1200, h=800):
    """Textured grey card with each configured region painted a distinct level (so quality checks pass)."""
    img = np.full((h, w), 150, np.float32)
    for spec in cfg.regions[side]:
        x0, y0, x1, y1 = spec.box
        img[int(y0 * h):int(round(y1 * h)), int(x0 * w):int(round(x1 * w))] = levels[spec.name]
    rng = np.random.default_rng(0)
    img = np.clip(img + rng.normal(0, 3, img.shape), 0, 255).astype(np.uint8)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


FRONT_LEVELS = {"national_id": 100, "birth_date": 112, "first_name": 124, "other_names": 136,
                "address_line1": 172, "address_line2": 184, "serial": 196}
BACK_LEVELS = {"national_id": 208, "issue_date": 220, "expiry_date": 88, "job": 76, "attributes": 64}   # distinct from FRONT_LEVELS


def front_script(nid=NID, birth="2000/01/01", **over):
    lines = {
        FRONT_LEVELS["national_id"]: [R(ar(nid), 0.95)],
        FRONT_LEVELS["birth_date"]: [R(ar(birth), 0.9)],
        FRONT_LEVELS["first_name"]: [R(FIRST, 0.9)],
        FRONT_LEVELS["other_names"]: [R(OTHERS, 0.9)],
        FRONT_LEVELS["address_line1"]: [R(ADDR1, 0.9)],
        FRONT_LEVELS["address_line2"]: [R(ADDR2, 0.9)],
        FRONT_LEVELS["serial"]: [R(SERIAL, 0.9)],        # arabic-reader cross-check read
    }
    tokens = {FRONT_LEVELS["serial"]: [R(SERIAL, 0.9, (0, 0, 50, 10))]}
    lines.update({FRONT_LEVELS[k]: v for k, v in over.items() if k in FRONT_LEVELS})
    # Printed Arabic lines now use detection before recognition, like the job field.
    tokens.update({FRONT_LEVELS[k]: lines[FRONT_LEVELS[k]] for k in
                   ("first_name", "other_names", "address_line1", "address_line2")})
    return FakeEngine(lines, tokens)


def back_script(nid=NID, expiry="2028/07/05"):
    lines = {
        BACK_LEVELS["national_id"]: [R(ar(nid), 0.95)],
        BACK_LEVELS["issue_date"]: [R(ar("2021/07"), 0.9)],
        BACK_LEVELS["expiry_date"]: [R(ar(expiry), 0.9)],
    }
    tokens = {
        BACK_LEVELS["job"]: [R("طالب", 0.9, (300, 5, 400, 20))],
        BACK_LEVELS["attributes"]: [R("ذكر", 0.9, (300, 5, 340, 20)), R("مسلم", 0.9, (200, 5, 260, 20)),
                                    R("أعزب", 0.9, (60, 5, 120, 20))],
    }
    return FakeEngine(lines, tokens)


def run(engine, cfg=None, **kw):
    cfg = cfg or OcrConfig(log_level="WARNING")
    pipe = IdOcrPipeline(cfg, engine=engine)
    return pipe.process(today=TODAY, **kw), cfg


# ================================================================================== pipeline
class TestPipelineWithFakeEngine:
    def test_clean_front_is_verified_and_schema_is_preserved(self):
        cfg = OcrConfig(log_level="WARNING")
        res, _ = run(front_script(), cfg, front=make_card("front", cfg, FRONT_LEVELS))
        p = res.public
        assert res.decision.status == "VERIFIED", res.decision.reasons
        for key in ("side", "first_name", "other_names", "address_line1", "address_line2", "birth_date", "national_id",
                    "serial", "job", "gender", "religion", "marital_status", "issue_date", "expiry_date",
                    "all_text_lines", "unreadable"):
            assert key in p, f"schema key {key} missing"
        assert p["side"] == "front" and p["national_id"] == NID and p["first_name"] == FIRST
        assert p["birth_date"] == "2000-01-01" and p["serial"] == SERIAL
        assert p["validation"]["national_id"]["status"] == "valid_structure"
        assert p["validation"]["birth_date_vs_national_id"] == "match"
        assert p["decision"]["document_authenticity"] == "not_assessed"
        assert {"back-only"} .isdisjoint(p["unreadable"])

    def test_both_sides_are_extracted_separately_and_merged(self):
        cfg = OcrConfig(log_level="WARNING")
        f_img, b_img = make_card("front", cfg, FRONT_LEVELS), make_card("back", cfg, BACK_LEVELS)
        engine = FakeEngine({**front_script().lines, **back_script().lines}, {**front_script().tokens, **back_script().tokens})
        res, _ = run(engine, cfg, front=f_img, back=b_img)
        p = res.public
        assert p["side"] == "both" and set(res.sides) == {"front", "back"}
        assert p["job"] == "طالب" and p["gender"] == "ذكر" and p["religion"] == "مسلم" and p["marital_status"] == "أعزب"
        assert p["issue_date"] == "2021-07" and p["expiry_date"] == "2028-07-05"
        assert p["validation"]["national_id_front_vs_back"] == "match"
        assert p["validation"]["gender_vs_national_id"] == "match"
        assert res.decision.status == "VERIFIED", res.decision.reasons

    def test_front_back_id_mismatch_is_flagged_not_resolved(self):
        cfg = OcrConfig(log_level="WARNING")
        other = "30001010100021"
        engine = FakeEngine({**front_script().lines, **back_script(nid=other).lines},
                            {**front_script().tokens, **back_script().tokens})
        res, _ = run(engine, cfg, front=make_card("front", cfg, FRONT_LEVELS), back=make_card("back", cfg, BACK_LEVELS))
        assert res.public["national_id"] is None
        assert res.public["validation"]["national_id_front_vs_back"] == "mismatch"
        assert res.decision.status == "MANUAL_REVIEW"

    def test_conflicting_reads_are_not_resolved_by_picking_the_valid_looking_one(self):
        cfg = OcrConfig(log_level="WARNING", full_image_pass="never")
        other = "30001010100022"
        script = front_script(national_id=NID)
        script.lines[FRONT_LEVELS["national_id"]] = Seq([R(ar(NID), 0.6)], [R(ar(other), 0.99)])
        res, _ = run(script, cfg, front=make_card("front", cfg, FRONT_LEVELS))
        assert res.public["national_id"] is None
        assert res.public["validation"]["field_status"]["national_id"] == "conflict"
        assert "national_id" in res.public["validation"]["unresolved"]
        assert res.decision.status == "MANUAL_REVIEW"
        assert res.public["validation"]["national_id"]["status"] == "unresolved"

    def test_ocr_birth_date_is_never_overwritten_by_the_decoded_date(self):
        cfg = OcrConfig(log_level="WARNING")
        res, _ = run(front_script(birth="2000/02/02"), cfg, front=make_card("front", cfg, FRONT_LEVELS))
        v = res.public["validation"]
        assert res.public["birth_date"] == "2000-02-02"                       # OCR value kept
        assert v["national_id"]["decoded"]["birth_date"] == "2000-01-01"     # decoded value separate
        assert v["birth_date_vs_national_id"] == "mismatch"
        assert res.decision.status == "MANUAL_REVIEW"

    def test_structurally_invalid_id_is_never_verified(self):
        cfg = OcrConfig(log_level="WARNING")
        bad = "30013010100011"            # month 13 but 14 digits and read confidently
        res, _ = run(front_script(nid=bad), cfg, front=make_card("front", cfg, FRONT_LEVELS))
        assert res.public["national_id"] == bad                               # recorded as read ...
        assert res.public["validation"]["national_id"]["status"] == "invalid_structure"
        assert res.decision.status == "MANUAL_REVIEW"                          # ... but never VERIFIED

    def test_wrong_length_id_yields_null_not_a_guess(self):
        cfg = OcrConfig(log_level="WARNING", full_image_pass="never")
        res, _ = run(front_script(national_id=[R(ar(NID[:-1]), 0.9)]), cfg, front=make_card("front", cfg, FRONT_LEVELS))
        assert res.public["national_id"] is None and "national_id" in res.public["unreadable"]
        assert res.decision.status in ("RETRY", "MANUAL_REVIEW")

    def test_serial_breaking_the_pattern_is_nulled_and_flagged(self):
        cfg = OcrConfig(log_level="WARNING")
        script = front_script()
        script.tokens[FRONT_LEVELS["serial"]] = [R("1B1234567", 0.94, (0, 0, 50, 10))]
        script.lines[FRONT_LEVELS["serial"]] = [R("1B1234567", 0.94)]
        res, _ = run(script, cfg, front=make_card("front", cfg, FRONT_LEVELS))
        assert res.public["serial"] is None and "serial" in res.public["unreadable"]
        assert "serial_pattern_mismatch" in res.public["validation"]["field_flags"]["serial"]

    def test_unreadable_region_becomes_null_listed_as_unreadable(self):
        cfg = OcrConfig(log_level="WARNING")
        script = front_script(address_line2=[])
        res, _ = run(script, cfg, front=make_card("front", cfg, FRONT_LEVELS))
        assert res.public["address_line2"] is None and "address_line2" in res.public["unreadable"]

    def test_bounded_rereads(self):
        cfg = OcrConfig(log_level="WARNING", max_rereads=2, full_image_pass="never")
        script = front_script(first_name=[])                        # never readable
        res, _ = run(script, cfg, front=make_card("front", cfg, FRONT_LEVELS))
        n_first = [c for c in res.sides["front"].fields["first_name"].candidates]
        assert len(n_first) == len(next(s for s in cfg.regions["front"] if s.name == "first_name").variants) + 2

    def test_nothing_read_at_all(self):
        cfg = OcrConfig(log_level="WARNING", full_image_pass="never")
        res, _ = run(FakeEngine({}, {}), cfg, front=make_card("front", cfg, FRONT_LEVELS))
        assert res.decision.status in ("RETRY", "MANUAL_REVIEW") and res.public["national_id"] is None

    def test_diagnostics_report_timing_and_calls(self):
        cfg = OcrConfig(log_level="WARNING")
        res, _ = run(front_script(), cfg, front=make_card("front", cfg, FRONT_LEVELS))
        d = res.public["diagnostics"]
        assert d["ocr_calls"] > 0 and d["processing_ms"] >= 0 and "front" in d["image_quality"]

    def test_trace_is_redacted_by_default_and_separate_from_public_json(self):
        cfg = OcrConfig(log_level="WARNING")
        res, _ = run(front_script(), cfg, front=make_card("front", cfg, FRONT_LEVELS))
        trace = json.dumps(res.trace(), ensure_ascii=False)
        assert NID not in trace and FIRST not in trace
        assert "candidates" not in res.public
        full = json.dumps(res.trace(include_text=True), ensure_ascii=False)
        assert NID in full                      # raw/normalised/value exist for auditing when explicitly requested


# ===================================================================== input / error handling
class TestInputErrors:
    cfg = OcrConfig(log_level="WARNING")

    def _go(self, **kw):
        return run(FakeEngine({}, {}), self.cfg, **kw)[0]

    def test_no_input(self):
        r = self._go()
        assert r.decision.status == "REJECTED" and "input:no_input" in r.decision.reasons

    def test_missing_file(self, tmp_path):
        r = self._go(front=str(tmp_path / "nope.jpg"))
        assert r.decision.status == "REJECTED" and "input:file_missing" in r.decision.reasons

    def test_unsupported_format(self, tmp_path):
        p = tmp_path / "card.gif"
        p.write_bytes(b"GIF89a")
        assert self._go(front=str(p)).decision.status == "REJECTED"

    def test_corrupted_image_is_retryable(self, tmp_path):
        p = tmp_path / "bad.jpg"
        p.write_bytes(b"this is not a jpeg" * 50)
        r = self._go(front=str(p))
        assert r.decision.status == "RETRY" and "input:corrupted_image" in r.decision.reasons

    def test_corrupted_image_escalates_after_max_attempts(self, tmp_path):
        p = tmp_path / "bad.jpg"
        p.write_bytes(b"this is not a jpeg" * 50)
        r = self._go(front=str(p), attempt=self.cfg.max_attempts)
        assert r.decision.status == "MANUAL_REVIEW"

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty.jpg"
        p.write_bytes(b"")
        assert self._go(front=str(p)).decision.status == "RETRY"

    def test_empty_array(self):
        assert self._go(front=np.zeros((0, 0, 3), np.uint8)).decision.status == "RETRY"

    def test_tiny_resolution(self):
        r = self._go(front=np.full((100, 150, 3), 128, np.uint8))
        assert r.decision.status == "RETRY" and "input:resolution_too_low" in r.decision.reasons

    def test_invalid_rotation_is_rejected(self):
        r = self._go(front=np.full((630, 1000, 3), 128, np.uint8), front_rotate=45)
        assert r.decision.status == "REJECTED"

    def test_oversized_image_is_downscaled_with_aspect_kept(self):
        from id_ocr.image_io import validate_array
        big = np.zeros((2000, 4000, 3), np.uint8)
        out = validate_array(big, OcrConfig(max_side_px=1600))
        assert out.shape[1] == 1600 and out.shape[0] == 800

    def test_unexpected_internal_error_does_not_leak_details(self, caplog):
        class Boom(FakeEngine):
            def read_line(self, *a, **k):
                raise RuntimeError("secret-internal-detail 30001010100011")
        cfg = OcrConfig(log_level="WARNING")
        r = run(Boom({}, {}), cfg, front=make_card("front", cfg, FRONT_LEVELS))[0]
        assert r.decision.status == "MANUAL_REVIEW" and "internal_error" in r.decision.reasons
        assert "secret-internal-detail" not in json.dumps(r.public) and "secret-internal-detail" not in caplog.text

    def test_engine_failure_propagates_as_deployment_error(self):
        class Dead(FakeEngine):
            def reader(self, key):
                raise EngineError("gpu_unavailable", "no gpu")
        with pytest.raises(EngineError):
            run(Dead({}, {}), front=np.full((630, 1000, 3), 128, np.uint8))


# ========================================================================= privacy / logging
class TestPrivacy:
    def test_default_logging_never_contains_identity_values(self, caplog):
        cfg = OcrConfig(log_level="DEBUG")
        caplog.set_level(logging.DEBUG)
        res, _ = run(front_script(), cfg, front=make_card("front", cfg, FRONT_LEVELS))
        text = caplog.text
        for secret in (NID, FIRST, OTHERS, ADDR1, ADDR2, SERIAL):
            assert secret not in text, "identity value leaked into logs"

    def test_redacted_json_hides_personal_values(self):
        cfg = OcrConfig(log_level="WARNING")
        res, _ = run(front_script(), cfg, front=make_card("front", cfg, FRONT_LEVELS))
        red = res.to_json(redact=True)
        for secret in (NID, FIRST, OTHERS, ADDR1, ADDR2, SERIAL, "2000-01-01"):
            assert secret not in red
        assert NID[-4:] in red                                           # only the last 4 digits survive
        assert res.public["national_id"] == NID                          # redaction works on a copy

    def test_redacted_json_also_hides_sensitive_attributes_and_decoded_birthplace(self):
        cfg = OcrConfig(log_level="WARNING")
        f_img, b_img = make_card("front", cfg, FRONT_LEVELS), make_card("back", cfg, BACK_LEVELS)
        engine = FakeEngine({**front_script().lines, **back_script().lines}, {**front_script().tokens, **back_script().tokens})
        res, _ = run(engine, cfg, front=f_img, back=b_img)
        assert res.public["religion"] == "مسلم"                           # public (non-redacted) value is intact
        red = res.to_json(redact=True)
        for secret in ("مسلم", "أعزب", "ذكر", "طالب", "Cairo"):
            assert secret not in red, f"{secret!r} leaked in redacted output"
        assert redact_public(res.public)["validation"]["birth_date_vs_national_id"] == "match"   # checks stay visible

    def test_debug_crops_only_written_when_enabled(self, tmp_path):
        cfg = OcrConfig(log_level="WARNING")
        run(front_script(), cfg, front=make_card("front", cfg, FRONT_LEVELS))
        assert not list(tmp_path.iterdir())
        cfg2 = OcrConfig(log_level="WARNING", debug_dir=str(tmp_path / "dbg"))
        run(front_script(), cfg2, front=make_card("front", cfg2, FRONT_LEVELS))
        assert len(list((tmp_path / "dbg").glob("*.png"))) > 0

    def test_no_cloud_vlm_or_network_client_is_imported_by_the_package(self):
        forbidden = re.compile(r"^\s*(?:from|import)\s+(anthropic|openai|requests|httpx|urllib3|google\.generativeai|"
                               r"langchain|crewai)\b", re.M)
        for path in glob.glob(os.path.join(ROOT, "src", "id_ocr", "*.py")):
            with open(path, encoding="utf-8") as fh:
                assert not forbidden.search(fh.read()), f"{os.path.basename(path)} imports a cloud/agent client"

    def test_no_detector_or_agent_framework_dependency(self):
        req = os.path.join(ROOT, "requirements.txt")
        if os.path.isfile(req):
            text = open(req, encoding="utf-8").read().lower()
            for bad in ("ultralytics", "yolo", "langchain", "crewai", "anthropic", "openai", "face_recognition"):
                assert bad not in text


# ============================================================================== engine layer
def _fake_torch(monkeypatch, cuda_available, cuda_build):
    t = types.ModuleType("torch")
    t.__version__ = "0.0-test"
    t.cuda = types.SimpleNamespace(is_available=lambda: cuda_available)
    t.version = types.SimpleNamespace(cuda=cuda_build)
    monkeypatch.setitem(sys.modules, "torch", t)


def _fake_easyocr(monkeypatch, raises=None):
    created = []

    class Reader:
        def __init__(self, langs, gpu, model_storage_directory, download_enabled, verbose):
            if raises:
                raise raises
            created.append({"langs": langs, "gpu": gpu})
            self.device = "cuda" if gpu else "cpu"

    mod = types.ModuleType("easyocr")
    mod.Reader = Reader
    monkeypatch.setitem(sys.modules, "easyocr", mod)
    return created


class TestEngine:
    def test_cpu_only_torch_gives_actionable_error_and_no_silent_fallback(self, monkeypatch):
        _fake_torch(monkeypatch, False, None)
        created = _fake_easyocr(monkeypatch)
        with pytest.raises(EngineError) as e:
            OcrEngine(OcrConfig()).reader("ar")
        assert e.value.code == "gpu_unavailable" and "CPU-only" in str(e.value) and not created

    def test_cuda_build_without_visible_gpu_points_at_driver(self, monkeypatch):
        _fake_torch(monkeypatch, False, "12.1")
        _fake_easyocr(monkeypatch)
        with pytest.raises(EngineError) as e:
            OcrEngine(OcrConfig()).reader("ar")
        assert "nvidia-smi" in str(e.value)

    def test_explicit_cpu_fallback(self, monkeypatch):
        _fake_torch(monkeypatch, False, "12.1")
        created = _fake_easyocr(monkeypatch)
        eng = OcrEngine(OcrConfig(allow_cpu_fallback=True))
        eng.reader("ar")
        assert created[0]["gpu"] is False and eng.fallback_reason

    def test_gpu_is_requested_when_available(self, monkeypatch):
        _fake_torch(monkeypatch, True, "12.1")
        created = _fake_easyocr(monkeypatch)
        OcrEngine(OcrConfig()).reader("ar")
        assert created[0]["gpu"] is True and created[0]["langs"] == ["ar", "en"]

    def test_gpu_disabled_by_config_does_not_need_torch(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "torch", None)       # import torch -> ImportError
        created = _fake_easyocr(monkeypatch)
        OcrEngine(OcrConfig(use_gpu=False)).reader("en")
        assert created[0]["gpu"] is False and created[0]["langs"] == ["en"]

    def test_reader_is_created_once_and_reused(self, monkeypatch):
        _fake_torch(monkeypatch, True, "12.1")
        created = _fake_easyocr(monkeypatch)
        eng = OcrEngine(OcrConfig())
        a, b = eng.reader("ar"), eng.reader("ar")
        assert a is b and len(created) == 1

    def test_engine_cache_is_shared_for_identical_config(self):
        assert get_engine(OcrConfig(use_gpu=False)) is get_engine(OcrConfig(use_gpu=False))

    def test_missing_easyocr(self, monkeypatch):
        _fake_torch(monkeypatch, True, "12.1")
        monkeypatch.setitem(sys.modules, "easyocr", None)
        with pytest.raises(EngineError) as e:
            OcrEngine(OcrConfig()).reader("ar")
        assert e.value.code == "easyocr_missing"

    def test_missing_language_data(self, monkeypatch):
        _fake_torch(monkeypatch, True, "12.1")
        _fake_easyocr(monkeypatch, raises=FileNotFoundError("arabic.pth"))
        with pytest.raises(EngineError) as e:
            OcrEngine(OcrConfig()).reader("ar")
        assert e.value.code == "language_data_missing"

    def test_model_init_failure_message_has_no_internal_details(self, monkeypatch):
        _fake_torch(monkeypatch, True, "12.1")
        _fake_easyocr(monkeypatch, raises=RuntimeError("/home/secret/path"))
        with pytest.raises(EngineError) as e:
            OcrEngine(OcrConfig()).reader("ar")
        assert e.value.code == "model_init_failed" and "/home/secret/path" not in str(e.value)


# ============================================================= real EasyOCR (opt-in, local)
easyocr_available = bool(__import__("importlib").util.find_spec("easyocr"))


@pytest.mark.skipif(not (easyocr_available and os.environ.get("BANKWISE_RUN_OCR") == "1"),
                    reason="set BANKWISE_RUN_OCR=1 with EasyOCR installed to run the real-model smoke test")
class TestRealEasyOcr:
    """Smoke test on ``test_data``. Asserts behaviour/invariants; accuracy lives in id_ocr.evaluate."""

    @pytest.fixture(scope="class")
    def pipe(self):
        cpu = os.environ.get("BANKWISE_CPU") == "1"
        return IdOcrPipeline(OcrConfig(use_gpu=not cpu, allow_cpu_fallback=cpu, log_level="WARNING"))

    def test_samples_run_end_to_end(self, pipe):
        front = os.path.join(ROOT, "test_data", "front_sample.jpg")
        back = os.path.join(ROOT, "test_data", "back_sample.jpg")
        if not (os.path.isfile(front) and os.path.isfile(back)):
            pytest.skip("no sample images in test_data/")
        res = pipe.process(front=front, back=back)
        assert res.decision.status in {"VERIFIED", "RETRY", "MANUAL_REVIEW", "REJECTED"}
        assert res.public["decision"]["document_authenticity"] == "not_assessed"
        nid = res.public["national_id"]
        assert nid is None or re.fullmatch(r"\d{14}", nid)
        assert res.public["diagnostics"]["ocr_calls"] > 0

    def test_reader_loaded_once_across_calls(self, pipe):
        pipe.warmup()
        before = dict(pipe.engine._readers)
        pipe.warmup()
        assert all(pipe.engine._readers[k] is v for k, v in before.items())
