"""Unit tests for the 2FA primitives and the separated SMS providers (no network, no database)."""
import httpx
import pytest

from bankwise.core.errors import ConfigError, InvalidInputError, ServiceUnavailable
from bankwise.core.otp import (OTP_MAX_ATTEMPTS, OTP_RESEND_COOLDOWN_SECONDS, OTP_TTL_SECONDS,
                               generate_otp, hash_otp, verify_otp)
from bankwise.core.security import Principal
from bankwise.core.sms import (SMS_KEY_ENV, SMS_URL_ENV, HttpSmsProvider, MockSmsProvider, build_sms_provider)
from test_guard import rig  # noqa: F401  (reuse the real security-service fixture)


def test_otp_policy_constants():
    assert (OTP_TTL_SECONDS, OTP_MAX_ATTEMPTS, OTP_RESEND_COOLDOWN_SECONDS) == (300, 5, 60)


def test_otp_is_random_six_digits_and_never_fixed():
    codes = [generate_otp() for _ in range(200)]
    assert all(len(code) == 6 and code.isdigit() and code.isascii() for code in codes)
    assert len(set(codes)) > 190                       # not a hard-coded or sequential value
    assert len({hash_otp(code) for code in codes[:8]}) == 8   # salted digests, sample only (Argon2 is slow)


def test_otp_digest_verifies_only_the_original_code():
    code = generate_otp()
    digest = hash_otp(code)
    assert digest.startswith("$argon2id$") and code not in digest
    assert verify_otp(digest, code)
    other = "000000" if code != "000000" else "111111"
    assert not verify_otp(digest, other)
    for bad in (None, 123456, code[:5], code + "0"):
        assert not verify_otp(digest, bad)
    assert not verify_otp("not-a-digest", code)


def test_hash_rejects_non_numeric_codes():
    for bad in ("abcdef", "12 345", "", "12345", "1234567"):
        with pytest.raises(InvalidInputError):
            hash_otp(bad)


def test_mock_sms_provider_keeps_only_codes_by_opaque_reference():
    provider = MockSmsProvider()
    provider.send("+201000000123", "314159", reference="challenge-1")
    assert provider.code_for("challenge-1") == "314159"
    assert provider.code_for("missing") is None
    assert "201000000123" not in repr(provider.__dict__)
    provider.forget("challenge-1")
    assert provider.code_for("challenge-1") is None


def test_provider_selection_is_explicit_and_production_never_uses_the_mock():
    provider, is_mock = build_sms_provider({}, False)
    assert is_mock and isinstance(provider, MockSmsProvider)
    with pytest.raises(ConfigError):
        build_sms_provider({}, True)
    for bad_url in ("http://sms.example/api", "ftp://sms.example/api"):
        with pytest.raises(ConfigError):
            build_sms_provider({SMS_URL_ENV: bad_url}, False)
    with pytest.raises(ConfigError):
        build_sms_provider({SMS_URL_ENV: "https://sms.example/api"}, True)
    provider, is_mock = build_sms_provider({SMS_URL_ENV: "https://sms.example/api",
                                            SMS_KEY_ENV: "a-strong-gateway-key-0123456789"}, True)
    assert not is_mock and isinstance(provider, HttpSmsProvider)


class _Response:
    def raise_for_status(self):
        return None


class _Client:
    last = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, *, json, headers):
        _Client.last = (url, json, headers, self.kwargs)
        return _Response()


def test_http_provider_sends_over_a_non_proxied_https_request(monkeypatch):
    monkeypatch.setattr("bankwise.core.sms.httpx.Client", _Client)
    provider = HttpSmsProvider("https://sms.example/api", "gateway-key")
    provider.send("+201000000123", "314159", reference="challenge-1")
    url, payload, headers, kwargs = _Client.last
    assert url == "https://sms.example/api"
    assert payload == {"to": "+201000000123", "message": "Your BankWise verification code is 314159",
                       "reference": "challenge-1"}
    assert headers["Authorization"] == "Bearer gateway-key"
    assert kwargs == {"timeout": 5.0, "trust_env": False}


def test_http_provider_failure_is_generic_and_never_leaks_the_code(monkeypatch):
    class Broken(_Client):
        def post(self, *args, **kwargs):
            raise httpx.ConnectError("POST https://sms.example code=314159 api_key=gateway-key")

    monkeypatch.setattr("bankwise.core.sms.httpx.Client", Broken)
    with pytest.raises(ServiceUnavailable) as info:
        HttpSmsProvider("https://sms.example/api", "gateway-key").send("+201000000123", "314159",
                                                                       reference="challenge-1")
    assert "314159" not in str(info.value) and "gateway-key" not in info.value.detail


def test_login_step_one_verifies_but_issues_no_token(rig):  # noqa: F811
    service, _, limiter, sink, _ = rig
    principal = Principal("user-1", frozenset({"operator"}))
    result = service.login("user-1", "correct", "ip:1",
                           lambda u, p: principal if (u == "user-1" and p == "correct") else None)
    assert result == principal
    assert sink.events == []                                    # no token_issued audit event
    assert limiter.peek("auth_attempt", "ip:1").remaining == 2
