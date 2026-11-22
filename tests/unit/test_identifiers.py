from uuid import UUID

from bankwise.core.audit import AuditEvent, AuditOutcome
from bankwise.core.identifiers import new_opaque_uuid
from bankwise.core.pii import redact_text


def test_digit_heavy_uuid_is_resampled_without_weakening_redaction(monkeypatch):
    unsafe = UUID("54344e8e-787e-463c-b6ce-e61814319641")
    safe = UUID("aabbccdd-abcd-4abc-aabc-aabbccddeeff")
    assert redact_text(str(unsafe)) != str(unsafe)
    candidates = iter([unsafe, safe])
    monkeypatch.setattr("bankwise.core.identifiers.uuid4", lambda: next(candidates))
    chosen = new_opaque_uuid()
    assert chosen == safe
    assert AuditEvent("account.created", AuditOutcome.SUCCESS, actor_id=str(chosen)).actor_id == str(safe)
    assert redact_text(str(unsafe)) != str(unsafe)
