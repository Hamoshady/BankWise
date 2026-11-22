import base64
import json
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from bankwise.core.auth import AuthSettings, InMemoryRevocationStore, TokenService
from bankwise.core.errors import AuthenticationError, BankWiseError, ConfigError, InvalidInputError
from bankwise.core.passwords import (
    hash_password, password_needs_rehash, verify_password, verify_password_or_dummy,
)
from bankwise.core.pii import Sensitive
from bankwise.core.security import Principal
from core_test_data import STRONG_SECRET

SETTINGS = AuthSettings(issuer="bankwise", audience="bankwise-api", access_token_ttl_seconds=900, leeway_seconds=10)
START = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)
ROLES = frozenset({"operator", "reviewer"})


class Clock:
    def __init__(self):
        self.now = START

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now = self.now + timedelta(seconds=seconds)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def service(clock):
    return TokenService(SETTINGS, Sensitive(STRONG_SECRET), clock=clock)


def forge(claims=None, key=STRONG_SECRET, algorithm="HS256", **overrides):
    """A token signed by the attacker/test, to probe every verification rule."""
    ts = int(START.timestamp())
    base = {"iss": "bankwise", "aud": "bankwise-api", "sub": "user-1", "roles": ["operator"], "typ": "access",
            "jti": "a" * 32, "iat": ts, "nbf": ts, "exp": ts + 900}
    base.update(claims or {})
    base.update(overrides)
    base = {k: v for k, v in base.items() if v is not None}
    return jwt.encode(base, key, algorithm=algorithm)


def reason(service, token):
    with pytest.raises(AuthenticationError) as info:
        service.authenticate(token)
    assert info.value.to_public() == {"code": "authentication_failed", "message": "Authentication failed."}
    if isinstance(token, str) and len(token) >= 8:
        assert token[:8] not in str(info.value) and token[:8] not in repr(info.value.to_public())
    return info.value.reason


# ------------------------------------------------------------------ passwords
def test_passwords_are_argon2id_salted_and_verifiable():
    first, second = hash_password("correct horse battery"), hash_password("correct horse battery")
    assert first.startswith("$argon2id$") and first != second
    assert "correct horse" not in first
    assert verify_password(first, "correct horse battery")
    assert not verify_password(first, "correct horse batterY")


def test_verify_never_raises_on_bad_input():
    good = hash_password("correct horse battery")
    for stored, plain in ((None, "x"), ("", "x"), ("not-a-hash", "x"), (good, None), (good, "x" * 300), (5, "x"), (good, "")):
        assert verify_password(stored, plain) is False


def test_password_length_rules_and_unicode_normalisation():
    for bad in ("short", "x" * 257, None, 12345678901234):
        with pytest.raises(InvalidInputError):
            hash_password(bad)
    stored = hash_password("\uff50\uff41\uff53\uff53\uff57\uff4f\uff52\uff44\uff11\uff12\uff13\uff14")   # full-width
    assert verify_password(stored, "password1234")


def test_rehash_detection_and_dummy_verification():
    from argon2 import PasswordHasher
    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash("correct horse battery")
    assert password_needs_rehash(weak) is True and password_needs_rehash("garbage") is True
    assert password_needs_rehash(hash_password("correct horse battery")) is False
    assert verify_password_or_dummy(None, "correct horse battery") is False         # unknown user: always False
    assert verify_password_or_dummy(hash_password("correct horse battery"), "correct horse battery") is True


# ------------------------------------------------------------------ settings and construction
@pytest.mark.parametrize("kwargs", [
    {"issuer": ""}, {"issuer": "has space"}, {"audience": 5}, {"algorithm": "none"}, {"algorithm": "RS256"},
    {"access_token_ttl_seconds": 59}, {"access_token_ttl_seconds": 86401}, {"access_token_ttl_seconds": True},
    {"leeway_seconds": -1}, {"leeway_seconds": 61}, {"leeway_seconds": "10"},
])
def test_invalid_auth_settings_are_rejected(kwargs):
    base = {"issuer": "bankwise", "audience": "bankwise-api"}
    base.update(kwargs)
    with pytest.raises(ConfigError):
        AuthSettings(**base)


def test_the_signing_secret_must_be_a_sensitive_wrapper():
    for bad in (STRONG_SECRET, None, Sensitive(""), Sensitive(b"bytes")):
        with pytest.raises(ConfigError):
            TokenService(SETTINGS, bad)


# ------------------------------------------------------------------ happy path
def test_issue_then_authenticate_round_trip(service):
    token = service.issue(Principal("user-1", ROLES))
    principal = service.authenticate(token)
    assert principal == Principal("user-1", ROLES)
    claims = jwt.decode(token, STRONG_SECRET, algorithms=["HS256"], audience="bankwise-api",
                        options={"verify_exp": False, "verify_iat": False, "verify_nbf": False})
    assert set(claims) == {"iss", "aud", "sub", "roles", "typ", "jti", "iat", "nbf", "exp"}
    assert claims["exp"] - claims["iat"] == 900 and len(claims["jti"]) == 32 and claims["typ"] == "access"
    assert jwt.get_unverified_header(token)["alg"] == "HS256"


def test_every_token_has_a_fresh_jti(service):
    principal = Principal("user-1", ROLES)
    assert len({jwt.decode(service.issue(principal), options={"verify_signature": False})["jti"] for _ in range(20)}) == 20


def test_only_principals_can_be_issued_tokens(service):
    with pytest.raises(InvalidInputError):
        service.issue("user-1")


# ------------------------------------------------------------------ rejection rules
@pytest.mark.filterwarnings("ignore:The HMAC key")          # forging HS512 with a short key is deliberate here
def test_signature_and_algorithm_attacks_are_rejected(service):
    assert reason(service, forge(key="x" * 40)) == "invalid_signature"
    good = forge()
    head, body, sig = good.split(".")
    assert reason(service, f"{head}.{body}.{'A' * len(sig)}") == "invalid_signature"
    assert reason(service, forge(algorithm="HS512")) == "wrong_algorithm"            # same secret, other algorithm
    for header in ({"alg": "none", "typ": "JWT"}, {"alg": "None"}, {"alg": "RS256"}):
        h = base64.urlsafe_b64encode(json.dumps(header).encode()).rstrip(b"=").decode()
        payload = good.split(".")[1]
        assert reason(service, f"{h}.{payload}.") in {"wrong_algorithm", "malformed"}
        assert reason(service, f"{h}.{payload}.{sig}") in {"wrong_algorithm", "malformed"}


def test_malformed_tokens_are_rejected(service):
    assert reason(service, "") == "missing" and reason(service, None) == "missing" and reason(service, 5) == "missing"
    assert reason(service, "not.a.jwt") == "malformed" and reason(service, "onlyone") == "malformed"
    assert reason(service, "x" * 5000) == "token_too_large"


@pytest.mark.parametrize("override,expected", [
    ({"iss": "someone-else"}, "wrong_issuer"), ({"aud": "other-api"}, "wrong_audience"),
    ({"sub": None}, "invalid_claims"), ({"jti": None}, "invalid_claims"), ({"exp": None}, "invalid_claims"),
    ({"iat": None}, "invalid_claims"), ({"nbf": None}, "invalid_claims"), ({"iss": None}, "invalid_claims"),
    ({"aud": None}, "invalid_claims"),
    ({"typ": "refresh"}, "invalid_claims"), ({"typ": None}, "invalid_claims"),
    ({"roles": "operator"}, "invalid_claims"), ({"roles": None}, "invalid_claims"), ({"roles": [5]}, "invalid_claims"),
    ({"roles": ["Bad Role"]}, "invalid_claims"), ({"sub": "has space"}, "invalid_claims"),
    ({"jti": "short"}, "invalid_claims"), ({"jti": "Z" * 32}, "invalid_claims"),
    ({"exp": 1.5e9}, "invalid_claims"), ({"iat": True}, "invalid_claims"),
])
def test_claim_rules(service, override, expected):
    assert reason(service, forge(**override)) == expected


def test_lifetime_and_time_rules_use_the_service_clock_and_leeway(service, clock):
    ts = int(START.timestamp())
    assert reason(service, forge(exp=ts + 901)) == "invalid_claims"                  # longer than the configured ttl
    assert reason(service, forge(exp=ts - 10, iat=ts - 20, nbf=ts - 20)) == "expired"      # exp <= now - leeway
    assert service.authenticate(forge(exp=ts - 9, iat=ts - 20, nbf=ts - 20))              # one second inside the leeway
    token = service.issue(Principal("user-1", ROLES))
    clock.advance(900 + 9)
    assert service.authenticate(token)                                               # inside leeway (exp <= now - 10 is false)
    clock.advance(1)
    assert reason(service, token) == "expired"                                       # exactly exp + leeway
    clock.now = START                                                                # back to the start for the not-yet-valid cases
    future = forge(nbf=ts + 11, iat=ts, exp=ts + 900)
    assert reason(service, future) == "not_yet_valid"
    assert service.authenticate(forge(nbf=ts + 10))                                  # within leeway
    assert reason(service, forge(iat=ts + 11, nbf=ts)) == "not_yet_valid"


def test_revocation_blocks_a_token_until_it_would_have_expired(service, clock):
    token = service.issue(Principal("user-1", ROLES))
    other = service.issue(Principal("user-1", ROLES))
    jti = service.revoke(token)
    assert len(jti) == 32 and reason(service, token) == "revoked"
    assert service.authenticate(other)                                               # other tokens are unaffected


def test_revoking_requires_a_valid_token_and_is_not_repeatable_with_a_revoked_one(service):
    with pytest.raises(AuthenticationError):
        service.revoke(forge(key="x" * 40))
    token = service.issue(Principal("user-1", ROLES))
    service.revoke(token)
    with pytest.raises(AuthenticationError):
        service.revoke(token)


def test_revocation_store_expires_entries_and_fails_loudly_when_full():
    store = InMemoryRevocationStore(max_entries=2)
    store.revoke("a" * 32, expires_at=100, now=50)
    store.revoke("b" * 32, expires_at=200, now=50)
    assert store.is_revoked("a" * 32, now=99) and not store.is_revoked("a" * 32, now=100)
    store.revoke("c" * 32, expires_at=300, now=100)                                   # room again: 'a' expired
    with pytest.raises(BankWiseError):
        store.revoke("d" * 32, expires_at=300, now=100)
    store.revoke("c" * 32, expires_at=300, now=100)                                   # re-revoking an existing id is fine


def test_a_token_from_another_secret_or_service_is_rejected(clock):
    other = TokenService(SETTINGS, Sensitive("a-completely-different-secret-value-0123456789"), clock=clock)
    with pytest.raises(AuthenticationError):
        other.authenticate(TokenService(SETTINGS, Sensitive(STRONG_SECRET), clock=clock).issue(Principal("u", ROLES)))
