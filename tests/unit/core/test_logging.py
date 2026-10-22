import io
import json
import logging

import pytest

from bankwise.core.errors import ConfigError, PermissionDenied
from bankwise.core.logging import (
    LoggingSettings, configure_logging, correlation_scope, get_correlation_id, get_logger, log_event,
)
from bankwise.core.pii import register_secret_value
from core_test_data import FAKE_NID, STRONG_SECRET

NAME_AR = "\u0633\u0644\u064a\u0645\u0627\u0646 \u0645\u062d\u0645\u062f"
ADDRESS_AR = "\u0627\u0644\u0645\u0633\u0627\u0643\u0646 \u0627\u0644\u0627\u0642\u062a\u0635\u0627\u062f\u064a\u0629"


def setup(**kwargs):
    stream = io.StringIO()
    configure_logging(LoggingSettings(**kwargs), stream)
    return stream


def records(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def test_settings_are_validated():
    for bad in ({"level": "LOUD"}, {"format": "xml"}, {"max_message_chars": 10}, {"max_message_chars": True},
                {"max_message_chars": "2000"}):
        with pytest.raises(ConfigError):
            LoggingSettings(**bad)


def test_json_records_have_consistent_fields_and_utc_time():
    stream = setup()
    with correlation_scope("req-0123456789") as cid:
        log_event(get_logger("ocr"), logging.INFO, "ocr_finished", decision="MANUAL_REVIEW", ocr_calls=29)
    (record,) = records(stream)
    assert set(record) == {"ts", "level", "logger", "event", "correlation_id", "fields"}
    assert record["level"] == "INFO" and record["logger"] == "bankwise.ocr" and record["event"] == "ocr_finished"
    assert record["correlation_id"] == cid == "req-0123456789"
    assert record["fields"] == {"decision": "MANUAL_REVIEW", "ocr_calls": 29} and record["ts"].endswith("Z")


OCR_PAYLOAD = {"front": {"national_id": FAKE_NID, "first_name": NAME_AR, "full_name": NAME_AR + " x",
                         "address": ADDRESS_AR, "text_rows": [NAME_AR, ADDRESS_AR], "status": "ok",
                         "id_read": {"candidates": {FAKE_NID: 5}, "raw_reads": {FAKE_NID + "9": 2}}}}


def assert_no_personal_data(text):
    assert FAKE_NID not in text and "010101" not in text
    for value in (NAME_AR, ADDRESS_AR):
        assert value not in text
        assert json.dumps(value)[1:-1] not in text                                 # JSON-escaped form too


def test_full_ocr_payloads_logged_as_arguments_or_fields_never_reach_the_output():
    stream = setup()
    log = get_logger("ocr")
    log.info("result %s", OCR_PAYLOAD)                                            # positional argument
    log.info("result %(front)s", OCR_PAYLOAD)                                     # mapping argument
    log_event(log, logging.INFO, "result", **OCR_PAYLOAD)                         # structured fields
    assert len(records(stream)) == 3
    assert_no_personal_data(stream.getvalue())
    assert "ok" in stream.getvalue()                                              # non-sensitive values survive


def test_known_limit_names_inside_already_formatted_text_are_not_detected():
    """Documented limitation: Arabic names/addresses have no pattern. Only ID numbers and secrets are masked in
    free text, so callers must pass structured data (above) and never pre-format raw OCR text into a message."""
    stream = setup()
    get_logger("ocr").info("raw text: " + NAME_AR + " " + FAKE_NID)
    out = json.loads(stream.getvalue())["event"]
    assert FAKE_NID not in out and "[REDACTED_ID]" in out                          # the ID is still masked
    assert NAME_AR in out                                                         # the name is NOT: this is the limit


def test_id_numbers_and_secrets_in_free_text_are_masked():
    register_secret_value(STRONG_SECRET)
    stream = setup()
    log = get_logger("x")
    log.info("applicant id " + FAKE_NID + " submitted")
    log.warning("Authorization: Bearer abcdef0123456789abcdef")
    log.error("login failed password=hunter2hunter2 key " + STRONG_SECRET)
    out = stream.getvalue()
    assert FAKE_NID not in out and "abcdef0123456789" not in out and "hunter2" not in out and STRONG_SECRET not in out
    assert "[REDACTED_ID]" in out and "[REDACTED]" in out


def test_exceptions_are_logged_without_paths_values_or_raw_messages():
    stream = setup()
    log = get_logger("x")
    secret_local = "super-secret-local-variable"
    try:
        _ = secret_local
        raise ValueError(f"cannot parse {FAKE_NID} token=tok_abcdef123456")
    except ValueError:
        log.exception("processing failed")
    try:
        raise PermissionDenied(f"user for {FAKE_NID} denied")
    except PermissionDenied:
        log.exception("denied")
    out = stream.getvalue()
    assert FAKE_NID not in out and "tok_abcdef" not in out and secret_local not in out
    first, second = records(stream)
    assert first["exception"]["type"] == "ValueError" and "[REDACTED_ID]" in first["exception"]["message"]
    assert second["exception"]["code"] == "permission_denied" and "detail" in second["exception"]
    for frame in first["exception"]["frames"]:
        assert "/" not in frame and "\\" not in frame and ".py:" in frame


def test_initialising_twice_never_duplicates_handlers_or_touches_the_root_logger():
    root_handlers = list(logging.getLogger().handlers)
    first, second = io.StringIO(), io.StringIO()
    configure_logging(LoggingSettings(), first)
    configure_logging(LoggingSettings(), second)
    configure_logging(LoggingSettings(), second)
    logger = logging.getLogger("bankwise")
    assert len(logger.handlers) == 1 and logger.propagate is False
    get_logger("x").info("hello")
    assert first.getvalue() == "" and len(records(second)) == 1
    assert logging.getLogger().handlers == root_handlers


def test_level_filtering_and_message_truncation():
    stream = setup(level="WARNING", max_message_chars=60)
    log = get_logger("x")
    log.info("dropped")
    log.warning("w" * 500)
    (record,) = records(stream)
    assert len(record["event"]) < 100 and record["event"].endswith("[truncated]")


def test_bad_format_strings_do_not_crash_or_leak_arguments():
    stream = setup()
    get_logger("x").info("needs %d and %s", FAKE_NID)
    (record,) = records(stream)
    assert FAKE_NID not in stream.getvalue() and record["event"] == "[unformattable log message]"


def test_console_format_is_single_line_and_redacted():
    stream = setup(format="console")
    get_logger("x").info("line one\nline two " + FAKE_NID)
    lines = stream.getvalue().splitlines()
    assert len(lines) == 1 and FAKE_NID not in lines[0] and "\\n" in lines[0]


def test_non_ascii_text_does_not_break_the_output_stream():
    stream = setup()
    log_event(get_logger("x"), logging.INFO, "event " + ADDRESS_AR[:0] + "ok", note="\u0645\u0631\u062d\u0628\u0627")
    assert json.loads(stream.getvalue())["event"].startswith("event")
    stream.getvalue().encode("ascii")                                             # pure ASCII on the wire


def test_correlation_ids_are_scoped_and_untrusted_values_are_replaced():
    assert get_correlation_id() is None
    with correlation_scope("good-id-123456") as one:
        assert get_correlation_id() == one
        with correlation_scope('evil"\n{"injected": true}') as two:
            assert two != 'evil"\n{"injected": true}' and len(two) == 32 and get_correlation_id() == two
        assert get_correlation_id() == one
    assert get_correlation_id() is None


def test_each_record_is_one_valid_json_line_even_with_hostile_input():
    stream = setup()
    get_logger("x").info('quote " backslash \\ newline \n tab \t {"a": 1}')
    assert len(stream.getvalue().splitlines()) == 1
    json.loads(stream.getvalue())
