"""Regression tests for the final hardening pass: token claims and revocation by subject, guard fail-closed
behaviour, key normalisation, path/file hardening, vault safety, safe uncaught-exception hook."""
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from bankwise.core.audit import AuditOutcome, InMemoryAuditSink
from bankwise.core.auth import AuthSettings, InMemoryRevocationStore, TokenService
from bankwise.core.config import load_rbac_policy
from bankwise.core.errors import AuthenticationError, BankWiseError, ConfigError, InvalidInputError, PermissionDenied
from bankwise.core.guard import MAX_HEADER_CHARS, SecurityService, parse_bearer
from bankwise.core.logging import LoggingSettings, configure_logging, install_safe_excepthook
from bankwise.core.pii import Sensitive
from bankwise.core.rate_limit import InMemoryRateLimiter, RateLimit, normalise_key
from bankwise.core.security import Authorizer, Principal, safe_output_path, write_private_text
from bankwise.core.storage import SecureFileStore, StorageSettings
from core_test_data import FAKE_NID, STRONG_SECRET

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink/permission semantics")
JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-body" * 4
SETTINGS = AuthSettings(issuer="bankwise", audience="bankwise-api")
START = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.now = START

    def __call__(self):
        return self.now


def forge(**overrides):
    ts = int(START.timestamp())
    claims = {"iss": "bankwise", "aud": "bankwise-api", "sub": "user-1", "roles": ["operator"], "typ": "access",
              "jti": "a" * 32, "iat": ts, "nbf": ts, "exp": ts + 900}
    claims.update(overrides)
    return jwt.encode(claims, STRONG_SECRET, algorithm="HS256")


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def tokens(clock):
    return TokenService(SETTINGS, Sensitive(STRONG_SECRET), clock=clock)


def reason_of(service, token):
    with pytest.raises(AuthenticationError) as info:
        service.authenticate(token)
    return info.value.reason


# ------------------------------------------------------------------ token claims (CVE-2024-53861 class of bugs)
@pytest.mark.parametrize("issuer", ["bank", "bankwise-extra", "xbankwise", "BANKWISE", "", "bankwis"])
def test_partial_or_altered_issuers_are_rejected_by_our_own_exact_check(tokens, issuer):
    assert reason_of(tokens, forge(iss=issuer)) in {"wrong_issuer", "invalid_claims"}


@pytest.mark.parametrize("audience", ["bankwise", "bankwise-api-2", "api", ["other"], ["bankwise"], []])
def test_partial_or_wrong_audiences_are_rejected(tokens, audience):
    assert reason_of(tokens, forge(aud=audience)) in {"wrong_audience", "invalid_claims"}


def test_an_audience_list_that_contains_ours_is_accepted(tokens):
    assert tokens.authenticate(forge(aud=["other", "bankwise-api"])).subject_id == "user-1"


@pytest.mark.parametrize("secret", ["short", "x" * 31])
def test_signing_secrets_shorter_than_256_bits_are_refused(secret):
    with pytest.raises(ConfigError) as info:
        TokenService(SETTINGS, Sensitive(secret))
    assert secret not in info.value.detail
    TokenService(SETTINGS, Sensitive("x" * 32))


# ------------------------------------------------------------------ revocation by subject (role change / suspension)
def test_revoking_a_subject_refuses_all_older_tokens_but_not_newer_ones(tokens, clock):
    principal = Principal("user-1", frozenset({"operator"}))
    first, second = tokens.issue(principal), tokens.issue(principal)
    other = tokens.issue(Principal("user-2", frozenset({"operator"})))
    clock.now += timedelta(seconds=5)
    tokens.revoke_subject("user-1")
    assert reason_of(tokens, first) == "revoked" and reason_of(tokens, second) == "revoked"
    assert tokens.authenticate(other).subject_id == "user-2"                  # other users are untouched
    clock.now += timedelta(seconds=1)
    fresh = tokens.issue(Principal("user-1", frozenset({"reviewer"})))        # new login after the role change
    assert tokens.authenticate(fresh).roles == frozenset({"reviewer"})


def test_a_token_issued_in_the_same_second_as_the_revocation_is_also_refused(tokens):
    tokens.revoke_subject("user-1")
    assert reason_of(tokens, tokens.issue(Principal("user-1", frozenset()))) == "revoked"


def test_subject_rules_expire_once_every_older_token_has_expired():
    store = InMemoryRevocationStore(max_entries=2)
    store.revoke_subject("user-1", cutoff=100, keep_until=1000, now=100)
    assert store.is_subject_revoked("user-1", 100, 999) and not store.is_subject_revoked("user-1", 101, 999)
    assert not store.is_subject_revoked("user-1", 100, 1000)
    store.revoke_subject("a", 1, 2000, 1000)
    store.revoke_subject("b", 1, 2000, 1000)
    with pytest.raises(BankWiseError):
        store.revoke_subject("c", 1, 2000, 1000)                              # full: fails loudly, never forgets silently


def test_revoke_subject_validates_the_identifier(tokens):
    with pytest.raises(InvalidInputError):
        tokens.revoke_subject("bad subject!")


# ------------------------------------------------------------------ guard
@pytest.fixture
def rig(tokens):
    sink = InMemoryAuditSink()
    limiter = InMemoryRateLimiter({"auth_attempt": RateLimit(3, 60)})
    return SecurityService(tokens, Authorizer(load_rbac_policy()), limiter, sink), tokens, sink


def test_oversized_authorization_headers_are_rejected_before_parsing():
    with pytest.raises(AuthenticationError) as info:
        parse_bearer("Bearer " + "a" * MAX_HEADER_CHARS)
    assert info.value.reason == "malformed"


def test_token_issuance_fails_closed_when_the_audit_record_cannot_be_written(tokens):
    class Broken:
        def write(self, event):
            raise OSError("disk full")

    service = SecurityService(tokens, Authorizer(load_rbac_policy()), InMemoryRateLimiter({"auth_attempt": RateLimit(3, 60)}), Broken())
    with pytest.raises(BankWiseError) as info:
        service.issue_token(Principal("user-1", frozenset({"operator"})))
    assert info.value.to_public()["code"] == "internal_error" and service.audit_failures == 1


def test_audit_failures_on_denials_are_counted_not_swallowed_silently(tokens):
    class Broken:
        def write(self, event):
            raise OSError("disk full")

    service = SecurityService(tokens, Authorizer(load_rbac_policy()), InMemoryRateLimiter({"auth_attempt": RateLimit(3, 60)}), Broken())
    with pytest.raises(AuthenticationError):
        service.authenticate("Bearer junk.junk.junk", "ip:1")
    assert service.audit_failures == 1


def test_only_admins_can_revoke_a_subject_and_the_action_is_audited(rig):
    service, tokens, sink = rig
    victim = tokens.issue(Principal("user-1", frozenset({"operator"})))
    operator = Principal("user-2", frozenset({"operator"}))
    admin = Principal("admin-1", frozenset({"admin"}))
    with pytest.raises(PermissionDenied):
        service.revoke_subject(operator, "user-1")
    with pytest.raises(PermissionDenied):
        service.revoke_subject(None, "user-1")
    assert tokens.authenticate(victim)                                         # nothing happened yet
    service.revoke_subject(admin, "user-1")
    assert reason_of(tokens, victim) == "revoked"
    event = sink.events[-1]
    assert (event.event_type, event.outcome, event.actor_id, dict(event.metadata)) == \
        ("auth.subject_revoked", AuditOutcome.SUCCESS, "admin-1", {"target": "user-1"})


# ------------------------------------------------------------------ rate limit keys
def test_trivially_different_keys_share_one_budget():
    limiter = InMemoryRateLimiter({"p": RateLimit(2, 60)})
    variants = ["Alice", "alice", "  ALICE  ", "\uff21\uff2c\uff29\uff23\uff25"]   # last one: full-width letters
    results = [limiter.acquire("p", key).allowed for key in variants]
    assert results == [True, True, False, False]
    assert limiter.peek("p", "aLiCe").allowed is False
    assert normalise_key("\u00c5ngstr\u00f6m ") == normalise_key("\u0041\u030angstro\u0308m")


@pytest.mark.parametrize("key", ["a\nb", "a\x00b", "a\tb", "\u200b" * 3 + "\x07"])
def test_keys_with_control_characters_are_rejected(key):
    with pytest.raises(InvalidInputError):
        normalise_key(key)


# ------------------------------------------------------------------ paths and private files
@pytest.mark.parametrize("bad", ["report.json:hidden", "a<b", 'a"b', "a|b", "a?b", "a*b", "dir/na:me", "tab\there",
                                 "bell\x07.txt"])
def test_windows_stream_and_special_characters_are_rejected(tmp_path, bad):
    with pytest.raises(InvalidInputError):
        safe_output_path(tmp_path, bad)


def test_private_writes_are_capped_binary_and_clean_up_after_failures(tmp_path, monkeypatch):
    target = tmp_path / "out.json"
    write_private_text(target, "line1\nline2\n")
    assert target.read_bytes() == b"line1\nline2\n"                          # no newline translation, any platform
    with pytest.raises(InvalidInputError):
        write_private_text(tmp_path / "big.json", "x" * 101, max_bytes=100)
    assert not (tmp_path / "big.json").exists()

    real_fdopen = os.fdopen

    class Failing:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.handle.close()

        def write(self, data):
            raise OSError("disk full")

    monkeypatch.setattr(os, "fdopen", lambda *a, **k: Failing(real_fdopen(*a, **k)))
    with pytest.raises(OSError):
        write_private_text(tmp_path / "half.json", "content")
    assert not (tmp_path / "half.json").exists()                             # no half-written sensitive file left behind


@POSIX
def test_private_writes_never_follow_a_symlink_at_the_final_component(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("precious")
    link = tmp_path / "out.json"
    os.symlink(victim, link)
    with pytest.raises(InvalidInputError):
        write_private_text(link, "overwritten?", overwrite=True)
    assert victim.read_text() == "precious"


# ------------------------------------------------------------------ vault
@pytest.fixture
def clock_f():
    class C:
        now = 1_000_000.0

        def __call__(self):
            return self.now
    return C()


def make_store(tmp_path, clock_f, **kwargs):
    settings = StorageSettings(base_dir="vault", ttl_seconds=100, max_bytes=1000, **kwargs)
    return SecureFileStore(settings, root=tmp_path, clock=clock_f)


@POSIX
def test_a_planted_symlink_is_never_read_and_never_makes_us_overwrite_its_target(tmp_path, clock_f):
    store = make_store(tmp_path, clock_f)
    victim = tmp_path / "victim.txt"
    victim.write_text("precious data")
    planted = "b" * 32
    os.symlink(victim, store._dir / f"{planted}.bin")
    with pytest.raises(InvalidInputError):
        store.load(planted)
    assert victim.read_text() == "precious data"                              # not read through, not zeroed
    assert not (store._dir / f"{planted}.bin").is_symlink()                   # the link itself was removed

    os.symlink(victim, store._dir / f"{planted}.bin")
    assert store.delete(planted) is True and victim.read_text() == "precious data"
    os.symlink(victim, store._dir / f"{planted}.bin")
    assert store.purge_expired() == 1 and victim.read_text() == "precious data"


@POSIX
def test_a_hard_linked_file_is_unlinked_not_overwritten(tmp_path, clock_f):
    store = make_store(tmp_path, clock_f)
    victim = tmp_path / "victim.txt"
    victim.write_text("precious data")
    os.link(victim, store._dir / f"{'c' * 32}.bin")
    store.delete("c" * 32)
    assert victim.read_text() == "precious data"


def test_the_vault_refuses_new_files_beyond_its_quota_after_trying_to_purge(tmp_path, clock_f):
    store = make_store(tmp_path, clock_f, max_files=2)
    first, second = store.save(JPEG), store.save(JPEG)
    with pytest.raises(InvalidInputError) as info:
        store.save(JPEG)
    assert "full" in info.value.detail
    os.utime(store._path(first), (clock_f.now - 500, clock_f.now - 500))     # now expired: room is made automatically
    third = store.save(JPEG)
    assert not store._path(first).exists() and store.load(second) and store.load(third)


def test_expired_files_from_an_earlier_run_are_removed_at_start(tmp_path, clock_f):
    store = make_store(tmp_path, clock_f)
    leftover = store.save(JPEG)
    os.utime(store._path(leftover), (clock_f.now - 500, clock_f.now - 500))
    restarted = make_store(tmp_path, clock_f)
    assert not restarted._path(leftover).exists()


@POSIX
def test_a_world_readable_vault_directory_is_tightened_at_start(tmp_path, clock_f):
    vault = tmp_path / "vault"
    vault.mkdir()
    os.chmod(vault, 0o777)
    make_store(tmp_path, clock_f)
    assert (vault.stat().st_mode & 0o777) == 0o700


def test_max_files_is_validated():
    for bad in (0, True, 100001, "5"):
        with pytest.raises(ConfigError):
            StorageSettings(max_files=bad)


# ------------------------------------------------------------------ uncaught exceptions
def raise_and_capture(exc):
    try:
        raise exc
    except BaseException:
        return sys.exc_info()


def test_the_safe_excepthook_logs_redacted_details_and_prints_only_a_generic_line(monkeypatch, capsys):
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    stream = io.StringIO()
    configure_logging(LoggingSettings(), stream)
    install_safe_excepthook()
    sys.excepthook(*raise_and_capture(ValueError(f"cannot parse {FAKE_NID} password=hunter2hunter2")))
    err = capsys.readouterr().err
    assert FAKE_NID not in err and "hunter2" not in err and "Traceback" not in err and "unexpected error" in err
    record = json.loads(stream.getvalue())
    assert record["level"] == "CRITICAL" and FAKE_NID not in stream.getvalue() and "hunter2" not in stream.getvalue()
    assert "[REDACTED_ID]" in record["exception"]["message"]


def test_the_safe_excepthook_logs_nothing_when_logging_is_not_configured(monkeypatch, capsys):
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    install_safe_excepthook()
    sys.excepthook(*raise_and_capture(RuntimeError(f"id {FAKE_NID}")))
    captured = capsys.readouterr()
    assert FAKE_NID not in captured.err and "not configured" in captured.err and captured.out == ""


def test_keyboard_interrupts_keep_the_default_behaviour(monkeypatch):
    seen = []
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(sys, "__excepthook__", lambda *a: seen.append(a[0]))
    install_safe_excepthook()
    sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
    assert seen == [KeyboardInterrupt]
