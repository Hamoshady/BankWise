import io
import json
from datetime import timezone

import pytest

from bankwise.core.audit import AuditEvent, AuditOutcome, InMemoryAuditSink, LoggingAuditSink
from bankwise.core.errors import InvalidInputError
from bankwise.core.logging import LoggingSettings, configure_logging, correlation_scope
from core_test_data import FAKE_NID

ARABIC_NAME = "\u0645\u062d\u0645\u062f \u0639\u0644\u064a"


def test_valid_event_serialises_with_utc_timestamp_and_safe_fields():
    event = AuditEvent("identity.submit", AuditOutcome.SUCCESS, actor_id="user-1",
                       metadata={"decision": "MANUAL_REVIEW", "ocr_calls": 29, "gpu": True, "ratio": 0.5, "none": None})
    data = event.to_dict()
    assert data["event_type"] == "identity.submit" and data["outcome"] == "success"
    assert data["actor_id"] == "user-1" and data["error_code"] is None and data["metadata"]["ocr_calls"] == 29
    assert event.timestamp.tzinfo is timezone.utc and data["timestamp"].endswith("+00:00")
    json.dumps(data)                                                              # JSON-serialisable


def test_correlation_id_is_taken_from_the_current_context():
    with correlation_scope("req-abc123456") as cid:
        assert AuditEvent("a.b", AuditOutcome.FAILURE).correlation_id == cid
        assert AuditEvent("a.b", AuditOutcome.FAILURE, correlation_id="other-id-12345").correlation_id == "other-id-12345"
    assert AuditEvent("a.b", AuditOutcome.FAILURE).correlation_id is None


def test_metadata_is_immutable_and_copied():
    source = {"k": "v"}
    event = AuditEvent("a.b", AuditOutcome.SUCCESS, metadata=source)
    source["k"] = "changed"
    assert event.metadata["k"] == "v"
    with pytest.raises(TypeError):
        event.metadata["k"] = "x"
    with pytest.raises(Exception):
        event.outcome = AuditOutcome.DENIED


@pytest.mark.parametrize("kwargs", [
    {"event_type": "BadName"}, {"event_type": "a..b"}, {"event_type": ""}, {"event_type": 5},
    {"outcome": "success"},
    {"actor_id": FAKE_NID}, {"actor_id": "has space"}, {"actor_id": ARABIC_NAME}, {"actor_id": "x" * 65},
    {"error_code": "made_up_code"},
    {"correlation_id": "short"}, {"correlation_id": "bad id with spaces!!"},
    # prohibited metadata
    {"metadata": {"national_id": "x"}}, {"metadata": {"first_name": "x"}}, {"metadata": {"address": "x"}},
    {"metadata": {"api_token": "x"}}, {"metadata": {"Bad-Key": "x"}}, {"metadata": {"k": ARABIC_NAME}},
    {"metadata": {"k": f"id {FAKE_NID}"}}, {"metadata": {"k": FAKE_NID}}, {"metadata": {"k": int(FAKE_NID)}},
    {"metadata": {"k": "password=hunter2hunter2"}}, {"metadata": {"k": "Bearer abcdefghijklmnop"}},
    {"metadata": {"k": "x" * 201}}, {"metadata": {"k": "line\nbreak"}},
    {"metadata": {"k": {"nested": "x"}}}, {"metadata": {"k": ["x"]}}, {"metadata": {"k": b"x"}},
    {"metadata": {f"k{i}": i for i in range(21)}}, {"metadata": ["not", "a", "mapping"]},
])
def test_events_that_could_carry_personal_data_are_rejected(kwargs):
    base = {"event_type": "a.b", "outcome": AuditOutcome.SUCCESS}
    base.update(kwargs)
    with pytest.raises(InvalidInputError) as info:
        AuditEvent(**base)
    assert FAKE_NID not in repr(info.value.to_public()) and ARABIC_NAME not in repr(info.value.to_public())


def test_in_memory_sink_is_bounded():
    sink = InMemoryAuditSink(max_events=3)
    for i in range(5):
        sink.write(AuditEvent("a.b", AuditOutcome.SUCCESS, metadata={"n": i}))
    assert [e.metadata["n"] for e in sink.events] == [2, 3, 4]


def test_logging_sink_writes_one_structured_line_per_event():
    stream = io.StringIO()
    configure_logging(LoggingSettings(), stream)
    LoggingAuditSink().write(AuditEvent("authz.denied", AuditOutcome.DENIED, actor_id="u1",
                                        error_code="permission_denied", metadata={"permission": "a:b"}))
    record = json.loads(stream.getvalue().strip())
    assert record["logger"] == "bankwise.audit" and record["event"] == "audit_event"
    assert record["fields"]["event_type"] == "authz.denied" and record["fields"]["metadata"] == {"permission": "a:b"}
