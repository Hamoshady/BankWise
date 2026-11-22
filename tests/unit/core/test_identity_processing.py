"""Integration-layer tests: the OCR processor is a SIMULATED stand-in (the real IdOcrPipeline/EasyOCR is not
loaded here). What is real: tokens, RBAC from configs/rbac.yaml, rate limiting, audit events, logging and redaction."""
import io
import json
import shutil
import struct
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bankwise.core.audit import AuditOutcome, InMemoryAuditSink
from bankwise.core.auth import AuthSettings, TokenService
from bankwise.core.config import IdentityProcessingSettings, load_app_config, load_rbac_policy
from bankwise.core.errors import (
    AuthenticationError, BankWiseError, ConfigError, InvalidInputError, PermissionDenied, RateLimitExceeded,
    to_public_error,
)
from bankwise.core.guard import SecurityService
from bankwise.core.images import ImageRejected, inspect_image
from bankwise.core.logging import LoggingSettings, configure_logging, correlation_scope
from bankwise.core.pii import Sensitive
from bankwise.core.rate_limit import InMemoryRateLimiter, RateLimit
from bankwise.core.security import Authorizer, Principal, reveal_sensitive
from bankwise.integration.identity_processing import (
    NOTICE, PROCESS_PERMISSION, IdentityProcessingService, create_identity_service,
)
from core_test_data import FAKE_NID, STRONG_SECRET

REPO_CONFIGS = Path(__file__).resolve().parents[3] / "configs"
NAME_AR = "\u0633\u0644\u064a\u0645\u0627\u0646 \u0645\u062d\u0645\u062f"
ADDRESS_AR = "\u0627\u0644\u0645\u0633\u0627\u0643\u0646 \u0627\u0644\u0627\u0642\u062a\u0635\u0627\u062f\u064a\u0629"
SECRET_IN_ERROR = "token=abcdef0123456789abcdef"


def make_png(width=640, height=400, extra=b""):
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr + b"\x00\x00\x00\x00" + extra


def make_jpeg(width=640, height=400, progressive=False):
    app0 = b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof = (b"\xff\xc2" if progressive else b"\xff\xc0") + b"\x00\x11\x08" + struct.pack(">HH", height, width) \
        + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    return b"\xff\xd8" + app0 + sof + b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00" + b"\x00" * 8 + b"\xff\xd9"


FRONT, BACK = make_jpeg(), make_png()


class Processor:
    """SIMULATED OCR: records calls and returns a payload full of (synthetic) personal data."""

    def __init__(self, result=None, error=None):
        self.calls = []
        self.result = result if result is not None else {
            "status": "MANUAL_REVIEW", "national_id": FAKE_NID, "full_name": NAME_AR, "address": ADDRESS_AR,
            "decoded": {"birth_date": "1990-01-01"}, "text_rows": [NAME_AR], "ocr_calls": 29,
        }
        self.error = error

    def process(self, front_image, back_image):
        self.calls.append((front_image, back_image))
        if self.error:
            raise self.error
        return self.result


class Rig:
    def __init__(self, processor=None, sink=None, settings=None, auth_limit=3, process_limit=2):
        self.sink = sink or InMemoryAuditSink()
        self.tokens = TokenService(AuthSettings(issuer="bankwise", audience="bankwise-api"), Sensitive(STRONG_SECRET),
                                   clock=lambda: datetime.now(timezone.utc))
        self.authorizer = Authorizer(load_rbac_policy())
        limiter = InMemoryRateLimiter({"auth_attempt": RateLimit(auth_limit, 60),
                                       "identity_process": RateLimit(process_limit, 60)})
        self.security = SecurityService(self.tokens, self.authorizer, limiter, self.sink)
        self.processor = processor or Processor()
        self.service = IdentityProcessingService(self.security, self.sink, settings or IdentityProcessingSettings(),
                                                 self.processor)

    def header(self, roles=("identity_processor",), subject="user-1"):
        return "Bearer " + self.tokens.issue(Principal(subject, frozenset(roles)))

    def run(self, header=None, front=FRONT, back=BACK, client="ip:1", **kwargs):
        return self.service.process(header if header is not None else self.header(), client, front, back, **kwargs)


@pytest.fixture
def rig():
    return Rig()


# ================================================================== RBAC definition
def test_the_process_permission_exists_and_only_the_dedicated_role_has_it():
    policy = load_rbac_policy()
    authorizer = Authorizer(policy)
    assert PROCESS_PERMISSION in policy.permissions
    for role in policy.roles:
        allowed = authorizer.is_allowed(Principal("u", frozenset({role})), PROCESS_PERMISSION)
        assert allowed == (role == "identity_processor"), role
    assert policy.roles["identity_processor"] == frozenset({PROCESS_PERMISSION})           # and nothing else
    assert not authorizer.is_allowed(Principal("u", frozenset({"identity_processor"})), "identity:view_sensitive")
    assert not authorizer.is_allowed(Principal("u", frozenset()), PROCESS_PERMISSION)


# ================================================================== gates run before OCR
def test_authorized_request_runs_ocr_once_and_returns_the_wrapped_result(rig):
    result = rig.run()
    assert len(rig.processor.calls) == 1 and rig.processor.calls[0] == (FRONT, BACK)
    assert result.notice == NOTICE and "not proof" in result.notice
    assert len(result.request_id) >= 8


@pytest.mark.parametrize("header", [None, "", "Basic abc", "Bearer", "Bearer not.a.token", "Bearer " + "a" * 9000])
def test_no_valid_authentication_means_no_ocr(rig, header):
    with pytest.raises(AuthenticationError):
        rig.service.process(header, "ip:1", FRONT, BACK)
    assert rig.processor.calls == []
    assert rig.sink.events[-1].event_type == "auth.failed"


def test_expired_and_revoked_tokens_never_reach_ocr(rig):
    header = rig.header()
    rig.security.revoke(header)
    with pytest.raises(AuthenticationError):
        rig.run(header)
    assert rig.processor.calls == []


@pytest.mark.parametrize("roles", [(), ("operator",), ("reviewer",), ("auditor",), ("admin",), ("unknown_role",),
                                   ("operator", "reviewer", "auditor", "admin")])
def test_authenticated_users_without_the_permission_never_reach_ocr(rig, roles):
    with pytest.raises(PermissionDenied):
        rig.run(rig.header(roles))
    assert rig.processor.calls == []
    denial = rig.sink.events[-1]
    assert (denial.event_type, denial.outcome) == ("authz.denied", AuditOutcome.DENIED)
    assert denial.metadata["permission"] == PROCESS_PERMISSION


def test_authorization_is_checked_before_the_images_are_even_looked_at(rig):
    with pytest.raises(PermissionDenied):                              # not InvalidInputError
        rig.run(rig.header(("operator",)), front=b"garbage", back=None)
    assert rig.processor.calls == []


def test_per_user_rate_limit_stops_ocr_after_the_budget(rig):
    header = rig.header()
    rig.run(header), rig.run(header)
    with pytest.raises(RateLimitExceeded):
        rig.run(header)
    assert len(rig.processor.calls) == 2
    assert rig.sink.events[-1].event_type == "rate_limit.exceeded"
    rig.run(rig.header(subject="user-2"))                                # another user has their own budget
    assert len(rig.processor.calls) == 3


def test_repeated_authentication_failures_lock_the_client_out_even_with_a_valid_token(rig):
    for _ in range(3):
        with pytest.raises(AuthenticationError):
            rig.service.process("Bearer junk.junk.junk", "ip:evil", FRONT, BACK)
    with pytest.raises(RateLimitExceeded):
        rig.run(client="ip:evil")
    assert rig.processor.calls == []


# ================================================================== image validation
@pytest.mark.parametrize("bad,reason", [
    (None, "not_bytes"), ("/etc/passwd", "not_bytes"), (Path("/etc/passwd"), "not_bytes"), (12345, "not_bytes"),
    (b"", "empty"), (b"GIF89a" + b"\x00" * 100, "unsupported_type"), (b"%PDF-1.4 " + b"x" * 100, "unsupported_type"),
    (b"<svg xmlns='x'></svg>", "unsupported_type"), (b"MZ\x90\x00" + b"\x00" * 100, "unsupported_type"),
    (b"../../etc/passwd", "unsupported_type"), (b"\xff\xd8\xff\xe0" + b"\x00" * 50, "unreadable_dimensions"),
    (b"\x89PNG\r\n\x1a\n" + b"\x00" * 30, "unreadable_dimensions"),
    (make_png(0, 400), "unreadable_dimensions"), (make_png(640, 0), "unreadable_dimensions"),
    (make_png(60000, 60000), "too_many_pixels"), (make_jpeg(30000, 30000), "too_many_pixels"),
    (make_png(50, 400), "too_small"), (make_jpeg(640, 20), "too_small"),
])
def test_invalid_images_are_rejected_before_ocr_on_either_side(rig, bad, reason):
    for front, back in ((bad, BACK), (FRONT, bad)):
        with pytest.raises(ImageRejected) as info:
            rig.run(front=front, back=back)
        assert info.value.reason == reason
        assert info.value.to_public() == {"code": "validation_failed", "message": "The request is invalid."}
    assert rig.processor.calls == []
    failure = rig.sink.events[-1]
    assert (failure.event_type, failure.outcome, failure.error_code) == ("identity.process", AuditOutcome.FAILURE, "validation_failed")
    assert failure.metadata["reason"] == reason and failure.metadata["side"] == "back"


def test_oversized_images_are_rejected_using_the_configured_limit():
    tight = Rig(settings=IdentityProcessingSettings(max_image_bytes=1024))
    with pytest.raises(ImageRejected) as info:
        tight.run(front=make_png(640, 400, extra=b"\x00" * 2000))
    assert info.value.reason == "too_large" and tight.processor.calls == []


def test_valid_jpeg_variants_and_png_are_accepted():
    for image in (make_jpeg(), make_jpeg(progressive=True), make_png(), make_jpeg(1600, 1000)):
        info = inspect_image(image, 5_000_000, 25_000_000, 100)
        assert info.kind in {"jpeg", "png"} and info.width >= 640 and info.pixels == info.width * info.height
    info = inspect_image(bytearray(make_png(321, 200)), 5_000_000, 25_000_000, 100)
    assert (info.width, info.height) == (321, 200)


def test_a_hostile_jpeg_segment_chain_cannot_hang_the_parser():
    nasty = b"\xff\xd8" + b"\xff\xfe\x00\x02" * 5000 + b"\xff\xd9"
    with pytest.raises(ImageRejected):
        inspect_image(nasty, 10_000_000, 25_000_000, 100)
    with pytest.raises(ImageRejected):
        inspect_image(b"\xff\xd8" + b"\xff" * 100000, 10_000_000, 25_000_000, 100)


def test_paths_are_never_interpreted_the_service_only_accepts_bytes(tmp_path):
    rig = Rig(process_limit=20)                      # rejected requests still spend the user's budget, by design
    real_file = tmp_path / "card.jpg"
    real_file.write_bytes(FRONT)
    for as_path in (str(real_file), real_file, "../card.jpg"):
        with pytest.raises(ImageRejected) as info:
            rig.run(front=as_path)
        assert info.value.reason == "not_bytes"
    assert rig.processor.calls == []


# ================================================================== no PII anywhere it should not be
def capture_logs():
    stream = io.StringIO()
    configure_logging(LoggingSettings(), stream)
    return stream


def assert_clean(text):
    for forbidden in (FAKE_NID, "010101", NAME_AR, ADDRESS_AR, json.dumps(NAME_AR)[1:-1], json.dumps(ADDRESS_AR)[1:-1],
                      "1990-01-01", SECRET_IN_ERROR, "abcdef0123456789"):
        assert forbidden not in text, forbidden


def test_success_leaks_no_personal_data_to_logs_audit_summary_or_repr(rig):
    stream = capture_logs()
    result = rig.run()
    everything = stream.getvalue() + repr([e.to_dict() for e in rig.sink.events]) + repr(result.summary) \
        + repr(result) + str(result) + repr(result.sensitive) + str(result.sensitive)
    assert_clean(everything)
    assert result.summary["status"] == "MANUAL_REVIEW" and result.summary["ocr_calls"] == 29
    assert result.summary["national_id"] == "[REDACTED]" and result.summary["full_name"] == "[REDACTED]"
    log = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert log[-1]["event"] == "identity_process_finished" and log[-1]["correlation_id"] == result.request_id
    success = rig.sink.events[-1]
    assert success.correlation_id == result.request_id and success.outcome is AuditOutcome.SUCCESS
    assert set(success.metadata) == {"stage", "front_type", "back_type", "front_bytes", "back_bytes", "duration_ms"}


def test_raw_results_need_the_view_sensitive_permission_a_reason_and_an_audit_record(rig):
    result = rig.run()
    processor_user = Principal("user-1", frozenset({"identity_processor"}))
    reviewer = Principal("rev-1", frozenset({"reviewer"}))
    with pytest.raises(PermissionDenied):
        reveal_sensitive(result.sensitive, processor_user, rig.authorizer, "manual review", rig.sink)
    raw = reveal_sensitive(result.sensitive, reviewer, rig.authorizer, "manual review", rig.sink)
    assert raw["national_id"] == FAKE_NID
    assert rig.sink.events[-1].event_type == "pii.reveal"


def test_ocr_errors_become_a_generic_error_and_leak_nothing(rig):
    stream = capture_logs()
    rig.processor.error = RuntimeError(f"bad card {FAKE_NID} {NAME_AR} {SECRET_IN_ERROR} /home/user/cards/front.jpg")
    with pytest.raises(BankWiseError) as info:
        rig.run()
    public = to_public_error(info.value)
    assert public == {"code": "internal_error", "message": "An internal error occurred."}
    assert_clean(repr(public) + str(info.value) + info.value.detail + stream.getvalue() + repr([e.to_dict() for e in rig.sink.events]))
    assert "/home/user" not in info.value.detail and info.value.__suppress_context__ is True
    failure = rig.sink.events[-1]
    assert (failure.outcome, failure.error_code) == (AuditOutcome.FAILURE, "internal_error")
    assert dict(failure.metadata) == {"stage": "ocr", "error_type": "RuntimeError"}


@pytest.mark.parametrize("bad_result", [None, "text", [1, 2], 5, b"bytes"])
def test_a_processor_that_returns_something_else_than_a_mapping_is_a_failure(rig, bad_result):
    rig.processor.result = bad_result
    with pytest.raises(BankWiseError):
        rig.run()
    assert rig.sink.events[-1].metadata["error_type"] == "TypeError"


def test_odd_exception_class_names_cannot_inject_into_audit_metadata(rig):
    Weird = type("Weird\nName {x}", (Exception,), {})
    rig.processor.error = Weird("boom")
    with pytest.raises(BankWiseError):
        rig.run()
    assert rig.sink.events[-1].metadata["error_type"] == "Exception"


def test_a_failing_ocr_run_still_releases_its_slot_so_the_next_request_works():
    rig = Rig(process_limit=10)
    rig.processor.error = RuntimeError("boom")
    for _ in range(3):
        with pytest.raises(BankWiseError):
            rig.run()
    rig.processor.error = None
    assert rig.run().summary["status"] == "MANUAL_REVIEW"


# ================================================================== audit failures
class BrokenSink:
    def __init__(self, fail_on=None):
        self.fail_on, self.events = fail_on, []

    def write(self, event):
        if self.fail_on is None or event.event_type == self.fail_on:
            raise OSError("disk full /var/audit")
        self.events.append(event)


def test_without_an_audit_record_the_result_is_not_released():
    rig = Rig(sink=BrokenSink(fail_on="identity.process"))
    with pytest.raises(BankWiseError) as info:
        rig.run()
    assert info.value.to_public()["code"] == "internal_error" and "/var/audit" not in repr(info.value.detail)
    assert len(rig.processor.calls) == 1 and rig.service.audit_failures == 1       # OCR ran, nothing was returned


def test_an_audit_failure_while_reporting_an_ocr_error_does_not_hide_that_error():
    rig = Rig(sink=BrokenSink(fail_on="identity.process"))
    rig.processor.error = RuntimeError("boom")
    with pytest.raises(BankWiseError) as info:
        rig.run()
    assert "ocr failed" in info.value.detail and rig.service.audit_failures == 1


def test_an_audit_failure_while_reporting_a_validation_error_still_raises_the_validation_error():
    rig = Rig(sink=BrokenSink(fail_on="identity.process"))
    with pytest.raises(ImageRejected):
        rig.run(front=b"not an image")
    assert rig.service.audit_failures == 1


# ================================================================== capacity and request ids
def test_concurrent_ocr_runs_are_capped():
    entered, release = threading.Event(), threading.Event()

    class Slow(Processor):
        def process(self, front_image, back_image):
            entered.set()
            release.wait(5)
            return super().process(front_image, back_image)

    rig = Rig(processor=Slow(), process_limit=10, settings=IdentityProcessingSettings(max_concurrent=1))
    first = threading.Thread(target=rig.run)
    first.start()
    assert entered.wait(5)
    with pytest.raises(RateLimitExceeded) as info:
        rig.run(rig.header(subject="user-2"))
    assert info.value.to_public()["code"] == "rate_limited"
    assert rig.sink.events[-1].error_code == "rate_limited" and rig.sink.events[-1].metadata["stage"] == "capacity"
    release.set()
    first.join(5)
    assert len(rig.processor.calls) == 1 and rig.run(rig.header(subject="user-3"))


def test_request_ids_are_reused_when_safe_and_replaced_when_untrusted():
    rig = Rig(process_limit=20)
    assert rig.run(request_id="req-0123456789").request_id == "req-0123456789"
    hostile = rig.run(request_id='x"\n{"evil": 1}')
    assert hostile.request_id != 'x"\n{"evil": 1}' and len(hostile.request_id) == 32
    with correlation_scope("outer-scope-12345"):
        assert rig.run().request_id == "outer-scope-12345"


# ================================================================== settings, wiring
@pytest.mark.parametrize("kwargs", [{"max_image_bytes": 10}, {"max_image_bytes": True}, {"max_image_bytes": 10 ** 9},
                                    {"max_pixels": 5}, {"min_side_pixels": 5}, {"max_concurrent": 0},
                                    {"max_concurrent": 100}, {"rate_policy": "Bad Name"}, {"rate_policy": 5}])
def test_invalid_processing_settings_are_rejected(kwargs):
    with pytest.raises(ConfigError):
        IdentityProcessingSettings(**kwargs)


def test_repository_config_defines_the_section_and_its_rate_policy():
    config = load_app_config(environ={"BANKWISE_ENV": "development"})
    assert config.identity_processing == IdentityProcessingSettings()
    assert config.identity_processing.rate_policy in config.rate_limits


@pytest.fixture
def dev_configs(tmp_path):
    folder = tmp_path / "configs"
    folder.mkdir()
    for name in ("app.yaml", "rbac.yaml", "logging.yaml"):
        shutil.copy(REPO_CONFIGS / name, folder / name)
    return folder


DEV_ENV = {"BANKWISE_ENV": "development", "BANKWISE_JWT_SECRET": STRONG_SECRET,
           "BANKWISE_AUDIT_HMAC_KEY": "audit-key-Zq8Xv3Nc7Rt1Ws5Ye9Bu2Hd6Jf4Lg0"}


def test_create_identity_service_wires_everything_through_build_security(dev_configs, tmp_path):
    from bankwise.core.audit import verify_audit_file

    processor = Processor()
    service = create_identity_service(processor, dev_configs, DEV_ENV, root=tmp_path)
    header = "Bearer " + service._security.issue_token(Principal("user-1", frozenset({"identity_processor"})))
    assert service.process(header, "ip:1", FRONT, BACK).summary["status"] == "MANUAL_REVIEW"
    denied = "Bearer " + service._security.issue_token(Principal("user-2", frozenset({"operator"})))
    with pytest.raises(PermissionDenied):
        service.process(denied, "ip:1", FRONT, BACK)
    assert len(processor.calls) == 1
    audit_file = tmp_path / "outputs" / "audit" / "audit.jsonl"
    assert verify_audit_file(audit_file, Sensitive(DEV_ENV["BANKWISE_AUDIT_HMAC_KEY"])).ok
    assert_clean(audit_file.read_text())


def test_start_up_fails_when_the_processing_section_is_missing(dev_configs, tmp_path):
    app = dev_configs / "app.yaml"
    text = app.read_text(encoding="utf-8")
    head = text[:text.index("# Limits for processing an ID-card image pair")]
    app.write_text(head, encoding="utf-8")
    with pytest.raises(ConfigError) as info:
        create_identity_service(Processor(), dev_configs, DEV_ENV, root=tmp_path)
    assert "identity_processing" in info.value.detail


def test_start_up_fails_when_rbac_lacks_the_process_permission_or_the_rate_policy(dev_configs, tmp_path):
    rbac = dev_configs / "rbac.yaml"
    rbac.write_text(rbac.read_text(encoding="utf-8").replace("  identity_processor:\n    - identity:process\n", "")
                    .replace("  - identity:process\n", ""), encoding="utf-8")
    with pytest.raises(ConfigError) as info:
        create_identity_service(Processor(), dev_configs, DEV_ENV, root=tmp_path)
    assert "identity:process" in info.value.detail
    shutil.copy(REPO_CONFIGS / "rbac.yaml", rbac)
    app = dev_configs / "app.yaml"
    app.write_text(app.read_text(encoding="utf-8").replace("  identity_process:                # OCR is expensive: per-user budget\n    limit: 5\n    window_seconds: 60\n", ""), encoding="utf-8")
    with pytest.raises(ConfigError) as info2:
        create_identity_service(Processor(), dev_configs, DEV_ENV, root=tmp_path)
    assert "identity_process" in info2.value.detail


def test_production_start_up_rules_still_apply_through_the_integration_layer(dev_configs, tmp_path):
    prod = dict(DEV_ENV, BANKWISE_ENV="production")
    with pytest.raises(ConfigError) as info:
        create_identity_service(Processor(), dev_configs, prod, root=tmp_path)          # repo config: single_process false
    assert "single_process" in info.value.detail
