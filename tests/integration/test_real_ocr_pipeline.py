"""REAL OCR integration test (opt-in). It loads the actual id_ocr.IdOcrPipeline (EasyOCR + PyTorch), so it is slow and
needs the models. Nothing here is a mock of the OCR. The card image is SYNTHETIC (text drawn with OpenCV): no real
person's data is used. The test checks the integration contract, not OCR accuracy: gates, decoding, result shape,
no personal data in logs/audit/summary, no files written, and bytes-vs-path equivalence.

    $env:RUN_REAL_OCR = "1"
    $env:ID_OCR_MODEL_DIR = "C:\\path\\to\\models"      # optional; the EasyOCR models must already be downloaded
    $env:ID_OCR_GPU = "1"                                 # optional; default is CPU
    python -m pytest tests/integration -q -s
"""
import io
import json
import logging
import os
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_REAL_OCR") != "1",
    reason="opt-in: set RUN_REAL_OCR=1 (needs easyocr, torch and the downloaded OCR models; slow)")

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")
pytest.importorskip("easyocr")
pytest.importorskip("id_ocr")

from bankwise.core.audit import AuditOutcome, InMemoryAuditSink                    # noqa: E402
from bankwise.core.auth import AuthSettings, TokenService                          # noqa: E402
from bankwise.core.config import IdentityProcessingSettings, load_rbac_policy      # noqa: E402
from bankwise.core.errors import InvalidInputError                                 # noqa: E402
from bankwise.core.guard import SecurityService                                    # noqa: E402
from bankwise.core.logging import LoggingSettings, configure_logging               # noqa: E402
from bankwise.core.pii import Sensitive                                            # noqa: E402
from bankwise.core.rate_limit import InMemoryRateLimiter, RateLimit                # noqa: E402
from bankwise.core.security import Authorizer, Principal                           # noqa: E402
from bankwise.integration.id_ocr_adapter import create_id_ocr_adapter, protect_ocr_logging   # noqa: E402
from bankwise.integration.identity_processing import IdentityProcessingService     # noqa: E402

SYNTHETIC_ID = "29901010101010"                     # invented digits drawn on the synthetic card
SYNTHETIC_NAME = "TESTPERSON"
SECRET = "k3Jx9Qw2Lm8Zr5Tn7Vb4Hc6Yd1Pf0Gs-AaBbCc"


def synthetic_card(label):
    image = np.full((638, 1012, 3), 235, np.uint8)
    cv2.rectangle(image, (40, 140), (300, 560), (150, 150, 150), -1)
    cv2.putText(image, SYNTHETIC_NAME, (420, 250), cv2.FONT_HERSHEY_DUPLEX, 1.6, (20, 20, 20), 3, cv2.LINE_AA)
    cv2.putText(image, label, (420, 330), cv2.FONT_HERSHEY_DUPLEX, 1.2, (20, 20, 20), 2, cv2.LINE_AA)
    cv2.putText(image, SYNTHETIC_ID, (420, 560), cv2.FONT_HERSHEY_DUPLEX, 1.8, (20, 20, 20), 4, cv2.LINE_AA)
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    return buffer.tobytes()


FRONT, BACK = synthetic_card("FRONT SIDE"), synthetic_card("BACK SIDE")


@pytest.fixture(scope="module")
def adapter():
    use_gpu = os.environ.get("ID_OCR_GPU") == "1"
    return create_id_ocr_adapter(model_dir=os.environ.get("ID_OCR_MODEL_DIR"), use_gpu=use_gpu,
                                 allow_cpu_fallback=not use_gpu, warm_up=True)


class Stack:
    def __init__(self, adapter):
        self.sink = InMemoryAuditSink()
        self.tokens = TokenService(AuthSettings(issuer="bankwise", audience="bankwise-api"), Sensitive(SECRET),
                                   clock=lambda: datetime.now(timezone.utc))
        self.security = SecurityService(self.tokens, Authorizer(load_rbac_policy()),
                                        InMemoryRateLimiter({"auth_attempt": RateLimit(5, 60),
                                                             "identity_process": RateLimit(50, 60)}), self.sink)
        self.service = IdentityProcessingService(self.security, self.sink, IdentityProcessingSettings(), adapter)

    def header(self, roles=("identity_processor",)):
        return "Bearer " + self.tokens.issue(Principal("user-1", frozenset(roles)))


def test_real_pipeline_behind_the_security_stack(adapter, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    logs, ocr_logs = io.StringIO(), io.StringIO()
    configure_logging(LoggingSettings(), logs)
    protect_ocr_logging(stream=ocr_logs)
    stack = Stack(adapter)
    result = stack.service.process(stack.header(), "ip:127.0.0.1", FRONT, BACK)

    status = result.summary["decision"]["status"]
    assert isinstance(status, str) and status
    assert result.summary["diagnostics"]["ocr_calls"] > 0
    assert "not proof" in result.notice
    raw = result.sensitive.reveal()
    assert isinstance(raw, dict) and "decision" in raw and "validation" in raw
    assert stack.sink.events[-1].outcome is AuditOutcome.SUCCESS
    everything = logs.getvalue() + ocr_logs.getvalue() + json.dumps(result.summary, ensure_ascii=False) \
        + repr(result) + repr([e.to_dict() for e in stack.sink.events])
    for forbidden in (SYNTHETIC_ID, SYNTHETIC_NAME, "0101"):
        assert forbidden not in everything, forbidden
    assert list(tmp_path.iterdir()) == []                                      # no files written anywhere
    for name in ("id_ocr", "bankwise"):
        logging.getLogger(name).handlers.clear()


def test_bytes_and_path_give_the_same_ocr_result(adapter, tmp_path):
    """Detects a colour-order or decoding mismatch between the in-memory array and the pipeline's own loader."""
    front_path, back_path = tmp_path / "front.jpg", tmp_path / "back.jpg"
    front_path.write_bytes(FRONT)
    back_path.write_bytes(BACK)
    by_path = adapter._pipeline.process(front=str(front_path), back=str(back_path)).public
    by_bytes = adapter.process(FRONT, BACK).raw

    def stable(public):
        return {key: value for key, value in public.items() if key != "diagnostics"}

    assert stable(by_bytes) == stable(by_path)


def test_unsupported_images_never_reach_the_real_pipeline(adapter, monkeypatch):
    calls = []
    real = adapter._pipeline.process
    monkeypatch.setattr(adapter._pipeline, "process", lambda **kw: (calls.append(1), real(**kw))[1])
    stack = Stack(adapter)
    for bad in (b"GIF89a" + b"\x00" * 200, b"%PDF-1.4" + b"\x00" * 200, "front.jpg", b""):
        with pytest.raises(InvalidInputError):
            stack.service.process(stack.header(), "ip:1", bad, BACK)
    assert calls == []
    with pytest.raises(Exception):
        stack.service.process(stack.header(("operator",)), "ip:1", FRONT, BACK)       # no permission
    assert calls == []
