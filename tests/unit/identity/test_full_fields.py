import json
from datetime import date

import numpy as np
import pytest

from bankwise.core.config import load_identity_config
from bankwise.core.pii import mask_serial
from bankwise.identity.national_id import GOVERNORATES, parse_national_id
from bankwise.identity.normalize import normalize_serial
from bankwise.identity.reader.prompts import BACK_PROMPT, FRONT_PROMPT, FRONT_ZOOM_PROMPT
from bankwise.identity.reader.vlm_reader import VlmFieldReader, arabic_line_notes
from bankwise.identity.service import verify_identity_fields
from identity_helpers import (
    JOB_TEXT,
    MALE_ID,
    MARITAL_TEXT,
    RELIGION_TEXT,
    SERIAL,
    TODAY,
    FakeCaller,
    back_response,
    back_text_response,
    failing,
    front_response,
    front_zoom_response,
    indic,
)
from test_validation_service import encoded_scene

CARD = np.zeros((638, 1012, 3), dtype=np.uint8)
OTHER_RELIGION = "\u0645\u0633\u064a\u062d\u064a"
OUTSIDE_ID = "29503148801234"


@pytest.fixture(scope="module")
def full_cfg():
    return load_identity_config()


def run_service(full_cfg, **kwargs):
    reader = VlmFieldReader(full_cfg.reader, caller=FakeCaller(**kwargs))
    return verify_identity_fields(encoded_scene(), encoded_scene(), reader, full_cfg, TODAY)


def test_prompts_request_the_new_fields_without_guessing():
    assert '"serial"' in FRONT_PROMPT
    assert '"serial"' in FRONT_ZOOM_PROMPT
    for key in ('"job"', '"religion"', '"marital_status"', '"lines"'):
        assert key in BACK_PROMPT
    assert "never infer" in BACK_PROMPT
    assert "Not every card shows every field" in BACK_PROMPT


def test_normalize_serial_rules():
    assert normalize_serial("ix 3863-212") == "IX3863212"
    assert normalize_serial(None) is None
    assert normalize_serial("ABC") is None
    assert normalize_serial("ABCDEFGH") is None
    assert normalize_serial("1" * 30) is None


def test_mask_serial():
    assert mask_serial("IX3863212") == "IX*****12"
    assert mask_serial("A1") == "**"


def test_governorate_name_is_derived_from_the_code():
    assert parse_national_id(MALE_ID, TODAY).governorate_name == GOVERNORATES["01"] == "Cairo"
    assert parse_national_id(OUTSIDE_ID, TODAY).governorate_name == "Born outside Egypt"
    unknown = parse_national_id("29503149901234", TODAY)
    assert unknown.governorate_name is None
    assert "unknown_governorate_code" in unknown.warnings


def test_back_fields_are_extracted_exactly_as_read():
    cfg = load_identity_config().reader
    result = VlmFieldReader(cfg, caller=FakeCaller()).read_back(CARD)
    assert result.job == JOB_TEXT
    assert result.religion == RELIGION_TEXT
    assert result.marital_status == MARITAL_TEXT
    assert result.lines[0] == JOB_TEXT
    assert result.warnings == []
    assert result.review_reasons == []


def test_missing_back_fields_stay_null_and_are_never_inferred():
    cfg = load_identity_config().reader
    blank_back = back_response(job=None, religion=None, marital_status=None)
    blank_text = back_text_response(job=None, religion=None, marital_status=None)
    result = VlmFieldReader(cfg, caller=FakeCaller(back=blank_back, back_text=[blank_text])).read_back(CARD)
    assert result.job is None
    assert result.religion is None
    assert result.marital_status is None


def test_religion_disagreement_needs_review_and_keeps_primary():
    cfg = load_identity_config().reader
    caller = FakeCaller(back_text=[back_text_response(religion=OTHER_RELIGION)])
    result = VlmFieldReader(cfg, caller=caller).read_back(CARD)
    assert "religion_reads_disagree" in result.review_reasons
    assert result.religion == RELIGION_TEXT


def test_back_gender_conflict_needs_review():
    cfg = load_identity_config().reader
    caller = FakeCaller(back_text=[back_text_response(gender="\u0623\u0646\u062b\u0649")])
    result = VlmFieldReader(cfg, caller=caller).read_back(CARD)
    assert "gender_reads_disagree" in result.review_reasons
    assert result.gender is None


def test_failed_back_text_read_needs_review():
    cfg = load_identity_config().reader
    result = VlmFieldReader(cfg, caller=FakeCaller(back_text=[failing()])).read_back(CARD)
    assert "back_text_cross_check_failed" in result.review_reasons
    assert "text_read_1_failed" in result.warnings


def test_serial_is_voted_and_conflicts_need_review():
    cfg = load_identity_config().reader
    agreeing = VlmFieldReader(cfg, caller=FakeCaller(front_zoom=[front_zoom_response(serial=None), front_zoom_response()]))
    assert agreeing.read_front(CARD).serial == SERIAL
    conflicting = VlmFieldReader(cfg, caller=FakeCaller(front_zoom=[front_zoom_response(serial="KF6484722"), front_zoom_response()]))
    result = conflicting.read_front(CARD)
    assert result.serial is None
    assert "serial_reads_disagree" in result.review_reasons


def test_sensitive_fields_can_be_switched_off():
    cfg = load_identity_config().reader.model_copy(update={"collect_sensitive_fields": False})
    result = VlmFieldReader(cfg, caller=FakeCaller()).read_back(CARD)
    assert result.religion is None
    assert result.marital_status is None
    assert result.lines == []
    assert result.job == JOB_TEXT


def test_arabic_line_quality_notes():
    spaced = "\u0633 \u0644 \u064a \u0645 \u0627 \u0646"
    assert arabic_line_notes([spaced], "front") == ["front_lines_letters_spaced"]
    assert arabic_line_notes(["\ufee3\ufea3\ufee4\ufea9"], "back") == ["back_lines_presentation_forms"]
    assert arabic_line_notes(["\u0645\u062d\u0645\u062f \u0633\u0644\u064a\u0645\u0627\u0646 \u0645\u0648\u0633\u0649"], "front") == []
    assert arabic_line_notes(["a b c d e f"], "front") == []


def test_all_fields_reach_the_final_report(full_cfg):
    report = run_service(full_cfg)
    assert report.first_name == "FirstName"
    assert report.other_names == "Father Grandfather"
    assert report.full_name == "FirstName Father Grandfather"
    assert report.address_lines == ["Street one", "City two"]
    assert report.address == "Street one City two"
    assert report.birth_date == date(1995, 3, 14)
    assert report.birth_date_source == "national_id"
    assert report.age_years == 31
    assert report.gender.value == "male"
    assert report.religion == RELIGION_TEXT
    assert report.job == JOB_TEXT
    assert report.marital_status == MARITAL_TEXT
    assert report.serial == SERIAL
    assert report.issue_year_month == "2025-05"
    assert report.expiry_date == date(2030, 5, 30)
    assert report.front_id.governorate_name == "Cairo"
    assert len(report.front_text_lines) == 7
    assert len(report.back_text_lines) == 7
    assert report.missing_fields == []
    assert report.unread_optional_fields == []
    assert report.needs_review is False


def test_report_lines_never_contain_the_full_id(full_cfg):
    dumped = run_service(full_cfg).model_dump_json()
    assert MALE_ID not in dumped
    assert indic(MALE_ID) not in dumped


def test_json_output_contains_every_new_key(full_cfg):
    data = json.loads(run_service(full_cfg).model_dump_json())
    for key in (
        "first_name", "other_names", "address_lines", "birth_date", "birth_date_source", "age_years",
        "religion", "job", "marital_status", "serial", "back_text_lines", "unread_optional_fields",
    ):
        assert key in data


def test_birth_date_falls_back_to_printed_date_with_a_warning(full_cfg):
    bad_id = "2950314010123"
    report = run_service(
        full_cfg,
        front=front_response(national_id=bad_id),
        front_zoom=[front_zoom_response(national_id=bad_id)] * 2,
        back=back_response(national_id=None),
        back_zoom=[{"national_id": None, "issue_date": "2025-05"}],
    )
    assert report.birth_date == date(1995, 3, 14)
    assert report.birth_date_source == "printed_date"
    assert report.age_years == 31
    assert "birth_date_from_printed_date_only" in report.warnings
    assert report.needs_review is True


def test_unread_fields_are_reported_without_guessing(full_cfg):
    report = run_service(
        full_cfg,
        back=back_response(job=None, religion=None, marital_status=None),
        back_text=[back_text_response(job=None, religion=None, marital_status=None)],
        front=front_response(serial=None),
        front_zoom=[front_zoom_response(serial=None)] * 2,
    )
    assert report.religion is None and report.job is None and report.marital_status is None
    assert report.serial is None
    assert report.unread_optional_fields == ["job", "serial", "religion", "marital_status"]
    assert report.missing_fields == []


def test_required_fields_missing_are_listed(full_cfg):
    report = run_service(
        full_cfg,
        front=front_response(address_line1=None, address_line2=None),
    )
    assert "address" in report.missing_fields


def test_redacted_view_hides_all_personal_fields(full_cfg):
    report = run_service(full_cfg)
    redacted = report.redacted()
    dumped = json.dumps(redacted, ensure_ascii=False)
    for secret in ("FirstName", "Father Grandfather", "Street one", "1995-03-14", SERIAL, RELIGION_TEXT, JOB_TEXT, MARITAL_TEXT, MALE_ID, indic(MALE_ID)):
        assert secret not in dumped
    assert redacted["serial"] == "IX*****12"
    assert redacted["back_text_lines"] == "<redacted>:7_lines"
    assert redacted["address_lines"] == "<redacted>:2_lines"
    assert redacted["age_years"] == 31
    assert redacted["front_id"]["governorate_name"] == "Cairo"


def test_sensitive_switch_hides_religion_and_marital_from_unread_list(full_cfg):
    cfg = full_cfg.model_copy(update={"reader": full_cfg.reader.model_copy(update={"collect_sensitive_fields": False})})
    reader = VlmFieldReader(cfg.reader, caller=FakeCaller())
    report = verify_identity_fields(encoded_scene(), encoded_scene(), reader, cfg, TODAY)
    assert report.religion is None
    assert report.marital_status is None
    assert report.back_text_lines == []
    assert "religion" not in report.unread_optional_fields
    assert report.gender.value == "male"