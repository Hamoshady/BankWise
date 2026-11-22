import json

import pytest

from bankwise.core.errors import (
    AuthenticationError, BankWiseError, ConfigError, ErrorCode, InvalidInputError, OtpError, PermissionDenied,
    RateLimitExceeded, to_public_error,
)
from core_test_data import FAKE_NID

ALL = [BankWiseError, ConfigError, InvalidInputError, AuthenticationError, PermissionDenied, RateLimitExceeded, OtpError]


def test_error_codes_are_stable_strings():
    assert {code.name: code.value for code in ErrorCode} == {
        "CONFIG_INVALID": "config_invalid", "VALIDATION_FAILED": "validation_failed",
        "AUTHENTICATION_FAILED": "authentication_failed", "PERMISSION_DENIED": "permission_denied", "RATE_LIMITED": "rate_limited", "OTP_FAILED": "otp_failed", "INTERNAL_ERROR": "internal_error",
    }
    assert ConfigError.code is ErrorCode.CONFIG_INVALID
    assert InvalidInputError.code is ErrorCode.VALIDATION_FAILED
    assert AuthenticationError.code is ErrorCode.AUTHENTICATION_FAILED
    assert PermissionDenied.code is ErrorCode.PERMISSION_DENIED
    assert RateLimitExceeded.code is ErrorCode.RATE_LIMITED
    assert OtpError.code is ErrorCode.OTP_FAILED
    assert BankWiseError.code is ErrorCode.INTERNAL_ERROR


def test_otp_error_only_exposes_allowlisted_challenge_reasons():
    assert OtpError("sensitive details", reason="012345 secret").to_public() == {
        "code": "otp_failed", "message": "OTP verification failed.", "reason": "invalid_otp"}
    assert OtpError(reason="exhausted").to_public()["reason"] == "exhausted"


@pytest.mark.parametrize("cls", ALL)
def test_public_view_never_contains_internal_detail(cls):
    secret_detail = f"failed on C:\\Users\\someone\\card.jpg id={FAKE_NID} token=abcdef123456"
    error = cls(secret_detail)
    for exposed in (str(error), repr(error.to_public()), json.dumps(error.to_public()), str(error.args)):
        assert "Users" not in exposed and FAKE_NID not in exposed and "abcdef" not in exposed
    assert error.to_public()["code"] == cls.code.value
    assert error.to_public()["message"] == cls.public_message
    assert error.detail == secret_detail            # kept for internal diagnostics


def test_internal_summary_is_redacted():
    summary = ConfigError(f"bad value for {FAKE_NID} password=hunter2hunter2").internal_summary()
    assert FAKE_NID not in repr(summary) and "hunter2" not in repr(summary)
    assert summary["code"] == "config_invalid" and summary["type"] == "ConfigError"


def test_unexpected_exceptions_become_a_generic_internal_error():
    for error in (ValueError(f"boom {FAKE_NID}"), KeyError("secret_key"), RuntimeError("/home/x/y.py line 3")):
        public = to_public_error(error)
        assert public == {"code": "internal_error", "message": BankWiseError.public_message}


def test_rate_limit_error_exposes_only_a_retry_hint():
    public = RateLimitExceeded("limit 'identity_submit' hit", retry_after_seconds=12.2).to_public()
    assert public == {"code": "rate_limited", "message": RateLimitExceeded.public_message, "retry_after_seconds": 13}
    assert "retry_after_seconds" not in RateLimitExceeded("x").to_public()


def test_authentication_errors_keep_the_reason_internal():
    error = AuthenticationError("token rejected: expired", "expired")
    assert error.reason == "expired"
    assert error.to_public() == {"code": "authentication_failed", "message": "Authentication failed."}
    assert "expired" not in str(error) and "expired" not in repr(error.to_public())
