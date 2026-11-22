import copy
import pickle

import pytest

from bankwise.core.pii import (
    REDACTED, REDACTED_ID, Sensitive, clear_registered_secrets, is_sensitive_key, redact, redact_text,
    register_secret_value,
)
from core_test_data import FAKE_NID

ARABIC_INDIC_NID = FAKE_NID.translate(str.maketrans("0123456789", "\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669"))
PERSIAN_NID = FAKE_NID.translate(str.maketrans("0123456789", "\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9"))


@pytest.mark.parametrize("key", ["national_id", "NationalID", "first_name", "full_name", "address", "address_line2",
                                 "home_address", "birth_date", "text_rows", "raw_text", "api_key", "Authorization",
                                 "jwt_secret", "password", "access_token", "candidates", "decoded"])
def test_sensitive_keys_are_recognised(key):
    assert is_sensitive_key(key)


@pytest.mark.parametrize("key", ["event", "event_name", "logger_name", "file_name", "status", "outcome",
                                 "ocr_calls", "inference_ms", "decision", "permission"])
def test_ordinary_keys_are_not_flagged(key):
    assert not is_sensitive_key(key)


def test_nested_structures_are_redacted_by_key_and_by_content():
    payload = {
        "front": {"national_id": FAKE_NID, "first_name": "Test", "status": "ok",
                  "id_read": {"candidates": {FAKE_NID: 5}, "agreeing_reads": 5},
                  "text_rows": ["row one", "row two"]},
        "notes": [f"see {FAKE_NID} here", ("tuple", FAKE_NID)],
        FAKE_NID: "key itself is an id",
    }
    out = redact(payload)
    dumped = repr(out)
    assert FAKE_NID not in dumped and "Test" not in dumped and "row one" not in dumped
    assert out["front"]["status"] == "ok"
    assert out["front"]["id_read"]["agreeing_reads"] == 5
    assert out["front"]["id_read"]["candidates"] == REDACTED
    assert REDACTED_ID in out["notes"][0]


def test_id_numbers_are_masked_in_every_digit_script_and_with_separators():
    grouped = " ".join([FAKE_NID[:4], FAKE_NID[4:8], FAKE_NID[8:12], FAKE_NID[12:]])
    dashed = "-".join([FAKE_NID[:7], FAKE_NID[7:]])
    for text in (FAKE_NID, ARABIC_INDIC_NID, PERSIAN_NID, grouped, dashed, f"x{FAKE_NID}y", f"id={FAKE_NID}."):
        out = redact_text(text)
        assert "010101" not in out and "\u0660\u0661" not in out, text
        assert REDACTED_ID in out, text


def test_ordinary_numbers_dates_and_scores_are_left_alone():
    for text in ("score 0.93", "2026-10-22", "12:21:48", "ocr_calls=29", "1976 ms", "short 1234567890"):
        assert redact_text(text) == text


def test_tokens_keys_and_assignments_are_masked():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1MSJ9.c2lnbmF0dXJl"
    samples = [jwt, "Authorization: Bearer abcDEF1234567890xyz", "AIza" + "A" * 35, "sk-" + "b" * 24,
               "password=hunter2hunter2", "api_key: 'abc123abc123'", "token = tok_value_123"]
    for text in samples:
        out = redact_text(f"prefix {text} suffix")
        assert "hunter2" not in out and "abc123" not in out and "tok_value" not in out
        assert "AAAA" not in out and "bbbb" not in out and "c2lnbmF0" not in out and "abcDEF" not in out
        assert "prefix" in out and "suffix" in out


def test_registered_secret_values_are_masked_anywhere():
    register_secret_value("my-very-private-value")
    assert redact_text("log my-very-private-value end") == f"log {REDACTED} end"
    clear_registered_secrets()
    assert redact_text("log my-very-private-value end") == "log my-very-private-value end"


def test_unknown_objects_bytes_and_long_ints_never_leak_through_repr():
    class Card:
        def __repr__(self):
            return "Card(" + FAKE_NID + ")"

    out = redact({"obj": Card(), "img": b"\x89PNG" * 100, "big": int(FAKE_NID), "small": 42, "flag": True})
    assert out == {"obj": "<Card>", "img": "<400 bytes>", "big": REDACTED_ID, "small": 42, "flag": True}


def test_deep_nesting_is_cut_off_instead_of_recursing_forever():
    deep = current = {}
    for _ in range(60):
        current["x"] = {}
        current = current["x"]
    assert "[MAX_DEPTH]" in repr(redact(deep))


def test_sensitive_wrapper_never_shows_its_value():
    secret = Sensitive(FAKE_NID)
    for rendered in (str(secret), repr(secret), f"{secret}", f"{secret:>40}", "%s" % secret, "%r" % secret,
                     str([secret]), str({"k": secret})):
        assert FAKE_NID not in rendered
    assert redact({"anything": secret}) == {"anything": REDACTED}
    assert secret.reveal() == FAKE_NID
    assert copy.deepcopy(secret).reveal() == FAKE_NID
    with pytest.raises(TypeError):
        pickle.dumps(secret)


@pytest.mark.parametrize("key", ["all_text_lines", "job", "serial", "gender", "religion", "marital_status", "other_names",
                                 "address_line1", "address_line2"])
def test_every_personal_field_of_the_ocr_result_schema_is_a_sensitive_key(key):
    assert is_sensitive_key(key)
    assert redact({key: "value", "decision": "MANUAL_REVIEW"}) == {key: REDACTED, "decision": "MANUAL_REVIEW"}
