from datetime import datetime, timezone

import pytest

from bankwise.core.audit import AuditOutcome, InMemoryAuditSink
from bankwise.core.auth import AuthSettings, TokenService
from bankwise.core.config import load_rbac_policy
from bankwise.core.errors import AuthenticationError, InvalidInputError, PermissionDenied, RateLimitExceeded
from bankwise.core.guard import SecurityService, parse_bearer
from bankwise.core.pii import Sensitive
from bankwise.core.rate_limit import InMemoryRateLimiter, RateLimit
from bankwise.core.security import Authorizer, Principal
from core_test_data import FAKE_NID, STRONG_SECRET

SETTINGS = AuthSettings(issuer="bankwise", audience="bankwise-api")


class Clock:
    def __init__(self):
        self.now = 5000.0

    def __call__(self):
        return self.now


@pytest.fixture
def rig():
    clock = Clock()
    limiter = InMemoryRateLimiter({"auth_attempt": RateLimit(3, 60), "identity_submit": RateLimit(2, 60)}, clock=clock)
    sink = InMemoryAuditSink()
    tokens = TokenService(SETTINGS, Sensitive(STRONG_SECRET), clock=lambda: datetime.now(timezone.utc))
    service = SecurityService(tokens, Authorizer(load_rbac_policy()), limiter, sink)
    return service, tokens, limiter, sink, clock


def bearer(tokens, subject="user-1", roles=("operator",)):
    return "Bearer " + tokens.issue(Principal(subject, frozenset(roles)))


@pytest.mark.parametrize("header,reason", [
    (None, "missing"), ("", "missing"), ("   ", "missing"), (5, "missing"),
    ("Basic abc", "malformed"), ("Bearer", "malformed"), ("Bearer a b", "malformed"), ("abc", "malformed"),
])
def test_bad_authorization_headers_are_rejected(header, reason):
    with pytest.raises(AuthenticationError) as info:
        parse_bearer(header)
    assert info.value.reason == reason


def test_bearer_scheme_is_case_insensitive_and_whitespace_tolerant():
    assert parse_bearer("bearer  tok ") == "tok" and parse_bearer("BEARER tok") == "tok"


def test_valid_token_authenticates_and_is_not_charged_to_the_failure_budget(rig):
    service, tokens, limiter, sink, _ = rig
    header = bearer(tokens)
    for _ in range(25):                                                   # far above the 3-failure budget
        assert service.authenticate(header, "ip:1").subject_id == "user-1"
    assert sink.events == [] and limiter.peek("auth_attempt", "ip:1").remaining == 3


def test_failures_are_audited_with_a_reason_and_never_with_the_token(rig):
    service, tokens, _, sink, _ = rig
    stolen = bearer(tokens)
    broken = stolen[:-3] + ("AAA" if not stolen.endswith("AAA") else "BBB")
    with pytest.raises(AuthenticationError):
        service.authenticate(broken, "ip:1")
    (event,) = sink.events
    assert (event.event_type, event.outcome, event.error_code) == ("auth.failed", AuditOutcome.FAILURE, "authentication_failed")
    assert event.metadata["reason"] == "invalid_signature" and event.actor_id is None
    assert broken.split()[1][:20] not in repr(event.to_dict())


def test_repeated_failures_lock_the_client_out_even_for_valid_tokens(rig):
    service, tokens, limiter, sink, clock = rig
    for _ in range(3):
        with pytest.raises(AuthenticationError):
            service.authenticate("Bearer nonsense.token.value", "ip:evil")
    with pytest.raises(RateLimitExceeded) as info:
        service.authenticate(bearer(tokens), "ip:evil")                   # a valid token is refused during lockout
    assert info.value.to_public()["code"] == "rate_limited"
    assert sink.events[-1].event_type == "auth.locked_out" and sink.events[-1].outcome is AuditOutcome.DENIED
    assert service.authenticate(bearer(tokens), "ip:other").subject_id == "user-1"   # other clients unaffected
    clock.now += 61
    assert service.authenticate(bearer(tokens), "ip:evil")                            # lockout ends with the window


def test_missing_headers_count_as_failures_too(rig):
    service, _, _, sink, _ = rig
    for _ in range(3):
        with pytest.raises(AuthenticationError):
            service.authenticate(None, "ip:1")
    with pytest.raises(RateLimitExceeded):
        service.authenticate(None, "ip:1")
    assert [e.metadata.get("reason") for e in sink.events[:3]] == ["missing"] * 3


def test_invalid_client_keys_are_refused_not_pooled(rig):
    service, tokens, *_ = rig
    for bad in (None, "", "x" * 200):
        with pytest.raises(InvalidInputError):
            service.authenticate(bearer(tokens), bad)


def test_guard_allows_a_permitted_request_and_rejects_a_forbidden_one(rig):
    service, tokens, _, sink, _ = rig
    assert service.guard(bearer(tokens), "identity:submit", "ip:1").subject_id == "user-1"
    with pytest.raises(PermissionDenied):
        service.guard(bearer(tokens), "identity:review", "ip:1")             # operator cannot review
    (event,) = sink.events
    assert event.event_type == "authz.denied" and event.actor_id == "user-1"


def test_guard_never_authorizes_an_unauthenticated_caller(rig):
    service, _, _, sink, _ = rig
    with pytest.raises(AuthenticationError):
        service.guard(None, "identity:read", "ip:1")
    assert [e.event_type for e in sink.events] == ["auth.failed"]


def test_per_user_rate_limit_is_enforced_audited_and_runs_before_authorization(rig):
    service, tokens, _, sink, _ = rig
    header = bearer(tokens, subject="busy-user")
    service.guard(header, "identity:submit", "ip:1", rate_policy="identity_submit")
    service.guard(header, "identity:submit", "ip:1", rate_policy="identity_submit")
    with pytest.raises(RateLimitExceeded):
        service.guard(header, "identity:submit", "ip:1", rate_policy="identity_submit")
    assert sink.events[-1].event_type == "rate_limit.exceeded" and sink.events[-1].metadata == {"policy": "identity_submit"}
    service.guard(bearer(tokens, subject="calm-user"), "identity:submit", "ip:1", rate_policy="identity_submit")


def test_token_issue_and_revoke_are_audited_and_revocation_takes_effect(rig):
    service, tokens, _, sink, _ = rig
    token = service.issue_token(Principal("user-9", frozenset({"reviewer"})))
    header = f"Bearer {token}"
    assert service.authenticate(header, "ip:1").roles == frozenset({"reviewer"})
    service.revoke(header)
    with pytest.raises(AuthenticationError) as info:
        service.authenticate(header, "ip:1")
    assert info.value.reason == "revoked"
    assert [e.event_type for e in sink.events] == ["auth.token_issued", "auth.token_revoked", "auth.failed"]
    assert all(token not in repr(e.to_dict()) for e in sink.events)


def test_audit_failures_never_change_the_security_decision(rig):
    service, tokens, limiter, _, _ = rig

    class Broken:
        def write(self, event):
            raise OSError("disk full")

    service._sink = Broken()
    with pytest.raises(AuthenticationError):
        service.authenticate("Bearer bad.token.here", "ip:1")
    with pytest.raises(PermissionDenied):
        service.guard(bearer(tokens), "identity:review", "ip:2")
    assert FAKE_NID not in repr(service)


# ------------------------------------------------------------------ rate limiter peek
def test_peek_reports_without_consuming():
    clock = Clock()
    limiter = InMemoryRateLimiter({"p": RateLimit(2, 10)}, clock=clock)
    assert [limiter.peek("p", "k").remaining for _ in range(5)] == [2] * 5
    limiter.acquire("p", "k")
    assert limiter.peek("p", "k").remaining == 1
    limiter.acquire("p", "k")
    blocked = limiter.peek("p", "k")
    assert (blocked.allowed, blocked.retry_after_seconds) == (False, 10.0)
    clock.now += 10
    assert limiter.peek("p", "k").allowed
    for bad in (None, "", " "):
        with pytest.raises(InvalidInputError):
            limiter.peek("p", bad)


def test_the_documented_composition_works_from_the_repository_configs(monkeypatch):
    """Mirrors the wiring example in docs/security_integration.md, built from the real configs/ files."""
    from bankwise.core.audit import LoggingAuditSink
    from bankwise.core.config import get_secret, load_app_config

    monkeypatch.setenv("BANKWISE_JWT_SECRET", STRONG_SECRET)
    config = load_app_config()
    service = SecurityService(
        TokenService(config.auth, get_secret("jwt_secret")),
        Authorizer(load_rbac_policy()),
        InMemoryRateLimiter(config.rate_limits),
        LoggingAuditSink(),
    )
    token = service.issue_token(Principal("user-1", frozenset({"operator"})))
    principal = service.guard(f"Bearer {token}", "identity:submit", "ip:127.0.0.1", rate_policy="identity_submit")
    assert principal.subject_id == "user-1"
    with pytest.raises(PermissionDenied):
        service.guard(f"Bearer {token}", "audit:read", "ip:127.0.0.1")
