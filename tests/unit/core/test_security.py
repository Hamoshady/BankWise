import os
import sys

import pytest

from bankwise.core.audit import AuditOutcome, InMemoryAuditSink
from bankwise.core.config import load_rbac_policy
from bankwise.core.errors import BankWiseError, ConfigError, InvalidInputError, PermissionDenied
from bankwise.core.pii import Sensitive
from bankwise.core.security import (
    Authorizer, Principal, RbacPolicy, reveal_sensitive, safe_output_path, write_private_text,
)
from core_test_data import FAKE_NID


@pytest.fixture
def authorizer():
    return Authorizer(load_rbac_policy())


def who(*roles, subject="user-1"):
    return Principal(subject, frozenset(roles))


def test_each_role_gets_exactly_its_permissions(authorizer):
    matrix = {
        "operator": {"identity:submit", "identity:read"},
        "reviewer": {"identity:read", "identity:review", "identity:view_sensitive"},
        "auditor": {"audit:read"},
        "admin": {"admin:manage"},
    }
    for role, allowed in matrix.items():
        for permission in authorizer.policy.permissions:
            assert authorizer.is_allowed(who(role), permission) == (permission in allowed), (role, permission)


def test_admin_wildcard_does_not_include_sensitive_data(authorizer):
    assert authorizer.is_allowed(who("admin"), "admin:manage")
    assert not authorizer.is_allowed(who("admin"), "identity:view_sensitive")


def test_everything_unknown_is_denied(authorizer):
    assert not authorizer.is_allowed(None, "identity:read")
    assert not authorizer.is_allowed("operator", "identity:read")                    # not a Principal
    assert not authorizer.is_allowed(who(), "identity:read")                         # no roles
    assert not authorizer.is_allowed(who("ghost"), "identity:read")                  # unknown role
    assert not authorizer.is_allowed(who("operator"), "identity:delete")             # unknown permission
    assert not authorizer.is_allowed(who("admin"), "admin:unlisted")                 # wildcard cannot invent permissions
    assert not authorizer.is_allowed(who("operator"), None)
    assert not authorizer.is_allowed(who("operator"), ["identity:read"])
    assert authorizer.is_allowed(who("ghost", "operator"), "identity:read")          # unknown roles are ignored, not fatal


def test_principal_requires_a_plain_subject_id():
    for bad in ("", "has space", "x" * 65, "line\nbreak", FAKE_NID + "/x", None, 5):
        with pytest.raises(InvalidInputError):
            Principal(bad, frozenset({"operator"}))
    assert Principal("user-1", ["operator"]).roles == frozenset({"operator"})


@pytest.mark.parametrize("raw", [
    None, [], {}, {"permissions": ["a:b"]}, {"permissions": ["a:b"], "roles": {}, "extra": 1},
    {"permissions": [], "roles": {}},
    {"permissions": ["a:b", "a:b"], "roles": {}},
    {"permissions": ["bad name"], "roles": {}},
    {"permissions": ["a:*"], "roles": {}},                                           # wildcard is not a permission
    {"permissions": ["a:b"], "roles": []},
    {"permissions": ["a:b"], "roles": {"Bad Role": ["a:b"]}},
    {"permissions": ["a:b"], "roles": {"r": ["a:c"]}},                               # grant not in catalogue
    {"permissions": ["a:b"], "roles": {"r": ["z:*"]}},                               # wildcard for unknown domain
    {"permissions": ["a:b"], "roles": {"r": ["a:b", "a:b"]}},
    {"permissions": ["a:b"], "roles": {"r": "a:b"}},
    {"permissions": ["a:b"], "roles": {"r": [5]}},
])
def test_invalid_policies_are_rejected(raw):
    with pytest.raises(ConfigError):
        RbacPolicy.from_mapping(raw)


def test_require_raises_and_audits_without_personal_data(authorizer):
    sink = InMemoryAuditSink()
    with pytest.raises(PermissionDenied) as info:
        authorizer.require(who("operator", subject="user-7"), "identity:review", sink)
    assert info.value.to_public()["code"] == "permission_denied"
    (event,) = sink.events
    assert (event.event_type, event.outcome, event.actor_id) == ("authz.denied", AuditOutcome.DENIED, "user-7")
    assert event.error_code == "permission_denied" and event.metadata["permission"] == "identity:review"
    authorizer.require(who("operator"), "identity:read", sink)                       # allowed: no event, no error
    assert len(sink.events) == 1


def test_denial_is_still_enforced_when_the_audit_sink_fails(authorizer):
    class Broken:
        def write(self, event):
            raise OSError("disk full")

    with pytest.raises(PermissionDenied):
        authorizer.require(None, "identity:read", Broken())


def test_garbage_permission_strings_are_audited_as_invalid(authorizer):
    sink = InMemoryAuditSink()
    with pytest.raises(PermissionDenied):
        authorizer.require(who("operator"), f"identity:{FAKE_NID} <script>", sink)
    assert sink.events[0].metadata["permission"] == "invalid"


# ---------------------------------------------------------------- explicit, audited raw access
def test_reveal_needs_permission_and_audits_before_releasing(authorizer):
    sink = InMemoryAuditSink()
    value = Sensitive(FAKE_NID)
    with pytest.raises(PermissionDenied):
        reveal_sensitive(value, who("operator"), authorizer, "support ticket 42", sink)
    assert [e.event_type for e in sink.events] == ["authz.denied"]
    assert reveal_sensitive(value, who("reviewer", subject="rev-1"), authorizer, "manual review", sink) == FAKE_NID
    assert sink.events[-1].event_type == "pii.reveal" and sink.events[-1].outcome is AuditOutcome.SUCCESS
    assert sink.events[-1].actor_id == "rev-1" and FAKE_NID not in repr(sink.events[-1].to_dict())


def test_reveal_is_refused_when_the_audit_cannot_be_written(authorizer):
    class Broken:
        def write(self, event):
            if event.event_type == "pii.reveal":
                raise OSError("disk full")

    with pytest.raises(BankWiseError) as info:
        reveal_sensitive(Sensitive(FAKE_NID), who("reviewer"), authorizer, "manual review", Broken())
    assert FAKE_NID not in repr(info.value.to_public()) and info.value.to_public()["code"] == "internal_error"


def test_reveal_rejects_unsafe_reasons_and_plain_values(authorizer):
    sink = InMemoryAuditSink()
    for reason in ("", "\u0627\u0633\u0645 \u0645\u062d\u0645\u062f", f"customer {FAKE_NID}", "x" * 300):
        with pytest.raises(InvalidInputError):
            reveal_sensitive(Sensitive("v"), who("reviewer"), authorizer, reason, sink)
    with pytest.raises(InvalidInputError):
        reveal_sensitive(FAKE_NID, who("reviewer"), authorizer, "manual review", sink)
    assert not any(e.event_type == "pii.reveal" for e in sink.events)


# ---------------------------------------------------------------- paths
@pytest.mark.parametrize("bad", ["", ".", "./", "..", "../x", "a/../../x", "a/b/../../../x", "/etc/x", "\\x", "C:\\x",
                                 "C:x", "\\\\server\\share\\x", "a\x00b", "CON", "con.txt", "dir/NUL", "COM1.json",
                                 "trailing.", "trailing ", "dir/ok/.."])
def test_unsafe_output_paths_are_rejected(tmp_path, bad):
    with pytest.raises(InvalidInputError):
        safe_output_path(tmp_path, bad)


def test_safe_output_paths_stay_inside_the_base(tmp_path):
    assert safe_output_path(tmp_path, "result.json") == (tmp_path / "result.json").resolve()
    nested = safe_output_path(tmp_path, "run1/side/front.json")
    assert nested == (tmp_path / "run1" / "side" / "front.json").resolve()
    assert safe_output_path(tmp_path, "run1\\front.json").parent.name == "run1"
    with pytest.raises(InvalidInputError):
        safe_output_path(tmp_path, 5)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need elevated rights on Windows")
def test_symlink_escape_is_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    base = tmp_path / "base"
    base.mkdir()
    os.symlink(outside, base / "link")
    with pytest.raises(InvalidInputError):
        safe_output_path(base, "link/stolen.json")


def test_private_writes_do_not_overwrite_by_default(tmp_path):
    target = safe_output_path(tmp_path, "out/result.json")
    write_private_text(target, "one")
    assert target.read_text(encoding="utf-8") == "one"
    with pytest.raises(InvalidInputError):
        write_private_text(target, "two")
    assert target.read_text(encoding="utf-8") == "one"
    write_private_text(target, "three", overwrite=True)
    assert target.read_text(encoding="utf-8") == "three"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits only")
def test_private_files_are_owner_only(tmp_path):
    target = tmp_path / "secret.json"
    write_private_text(target, "x")
    assert (target.stat().st_mode & 0o777) == 0o600
