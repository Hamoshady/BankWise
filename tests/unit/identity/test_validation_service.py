import json
from datetime import date

import cv2
import pytest

from bankwise.core.config import load_identity_config
from bankwise.identity.reader.base import BackRead, FrontRead, IdVote
from bankwise.identity.reader.vlm_reader import VlmFieldReader
from bankwise.identity.schemas import Gender
from bankwise.identity.service import verify_identity_fields
from bankwise.identity.validation import build_checks, build_info
from bankwise.identity.vision.schemas import VisionStatus
from identity_helpers import (
    AGE_115_ID,
    FEMALE_ID,
    MALE_ID,
    TODAY,
    FakeCaller,
    back_response,
    back_zoom_response,
    front_response,
    front_zoom_response,
)
from test_vision import make_card_scene

BIRTH = date(1995, 3, 14)


@pytest.fixture(scope="module")
def cfg():
    return load_identity_config()


def front_read(vote=IdVote(MALE_ID, False, 3, 3), birth=BIRTH):
    return FrontRead("First", "Others", "Addr", birth, vote)


def back_read(vote=IdVote(MALE_ID, False, 2, 2), gender=Gender.MALE, issue="2025-05", expiry=date(2030, 5, 30)):
    return BackRead(vote, gender, issue, expiry)


def checks_for(front, back, min_agreeing=2):
    info_front = build_info(front.national_id, TODAY)
    info_back = build_info(back.national_id, TODAY)
    result = build_checks(front, back, info_front, info_back, TODAY, min_agreeing)
    return {check.name: check for check in result}


def test_consistent_card_passes_every_check():
    checks = checks_for(front_read(), back_read())
    assert all(check.passed for check in checks.values())
    for name in (
        "front_id_format_valid",
        "front_id_read_unambiguous",
        "front_id_reads_agree",
        "front_back_id_match",
        "gender_matches_id",
        "printed_birth_date_matches_id",
        "card_not_expired",
        "expiry_after_issue",
    ):
        assert name in checks


def test_single_supporting_read_fails_agreement_check():
    checks = checks_for(front_read(IdVote(MALE_ID, False, 1, 3)), back_read())
    assert checks["front_id_reads_agree"].passed is False
    assert checks["front_id_reads_agree"].detail == "1/3"


def test_agreement_requirement_adapts_when_only_one_read_exists():
    checks = checks_for(front_read(IdVote(MALE_ID, False, 1, 1)), back_read())
    assert checks["front_id_reads_agree"].passed


def test_ambiguous_side_never_matches():
    checks = checks_for(front_read(), back_read(IdVote(MALE_ID, True, 0, 2)))
    assert checks["back_id_read_unambiguous"].passed is False
    assert checks["front_back_id_match"].passed is False
    assert checks["front_back_id_match"].detail == "ambiguous_read"


def test_different_ids_do_not_match():
    checks = checks_for(front_read(), back_read(IdVote(FEMALE_ID, False, 2, 2)))
    assert checks["front_back_id_match"].passed is False


def test_back_without_id_skips_match_check():
    checks = checks_for(front_read(), back_read(IdVote(None, False, 0, 2)))
    assert "front_back_id_match" not in checks


def test_gender_birth_and_expiry_mismatches():
    checks = checks_for(
        front_read(birth=date(1995, 3, 15)),
        back_read(gender=Gender.FEMALE, issue="2031-01", expiry=date(2020, 1, 1)),
    )
    assert checks["gender_matches_id"].passed is False
    assert checks["printed_birth_date_matches_id"].passed is False
    assert checks["card_not_expired"].passed is False
    assert checks["expiry_after_issue"].passed is False


def test_invalid_front_id_skips_dependent_checks():
    checks = checks_for(front_read(IdVote("2950314010123", False, 0, 3)), back_read(IdVote(None, False, 0, 2)))
    assert checks["front_id_format_valid"].passed is False
    assert "gender_matches_id" not in checks
    assert "printed_birth_date_matches_id" not in checks


def test_age_warning_alone_fails_no_check():
    checks = checks_for(front_read(IdVote(AGE_115_ID, False, 3, 3), date(1911, 5, 20)), back_read(IdVote(AGE_115_ID, False, 2, 2)))
    assert all(check.passed for check in checks.values())
    assert "age_over_100" in build_info(IdVote(AGE_115_ID, False, 3, 3), TODAY).warnings


def encoded_scene(blur=0):
    scene = make_card_scene()
    if blur:
        scene = cv2.GaussianBlur(scene, (0, 0), blur)
    ok, buffer = cv2.imencode(".jpg", scene)
    assert ok
    return buffer.tobytes()


class ExplodingReader:
    name = "exploding"

    def read_front(self, card):
        raise AssertionError("reader must not be called")

    def read_back(self, card):
        raise AssertionError("reader must not be called")


def test_reader_is_not_called_when_an_image_fails_quality(cfg):
    report = verify_identity_fields(encoded_scene(blur=12), encoded_scene(), ExplodingReader(), cfg, TODAY)
    assert report.reader_called is False
    assert report.front_image.status != VisionStatus.OK
    assert report.checks == []


def test_full_flow_with_fake_vlm(cfg):
    caller = FakeCaller(
        front=front_response(),
        front_zoom=[front_zoom_response()] * 2,
        back=back_response(),
        back_zoom=[back_zoom_response()],
    )
    reader = VlmFieldReader(cfg.reader, caller=caller)
    report = verify_identity_fields(encoded_scene(), encoded_scene(), reader, cfg, TODAY)
    assert report.reader_called
    assert report.reader == reader.name
    assert report.full_name == "FirstName Father Grandfather"
    assert report.front_id.format_valid
    assert report.front_id.birth_date == BIRTH
    assert report.gender == Gender.MALE
    assert report.expiry_date == date(2030, 5, 30)
    assert report.missing_fields == []
    assert all(check.passed for check in report.checks)
    assert len(caller.calls) == (1 + 2 + 1) + (1 + 1 + 1)

def test_report_never_contains_raw_id_and_redaction_hides_pii(cfg):
    reader = VlmFieldReader(cfg.reader, caller=FakeCaller())
    report = verify_identity_fields(encoded_scene(), encoded_scene(), reader, cfg, TODAY)
    assert MALE_ID not in report.model_dump_json()
    redacted = json.dumps(report.redacted(), ensure_ascii=False)
    assert MALE_ID not in redacted
    assert "FirstName" not in redacted
    assert "Street one" not in redacted
    assert "1995-03-14" not in redacted
    assert report.front_id.masked in redacted


def test_ambiguous_read_is_reported_and_unmatched(cfg):
    caller = FakeCaller(front_zoom=[front_zoom_response(FEMALE_ID)] * 2)
    reader = VlmFieldReader(cfg.reader, caller=caller)
    report = verify_identity_fields(encoded_scene(), encoded_scene(), reader, cfg, TODAY)
    names = {check.name: check for check in report.checks}
    assert report.front_id.ambiguous
    assert report.front_id.birth_date is None
    assert names["front_id_read_unambiguous"].passed is False
    assert names["front_back_id_match"].passed is False
    assert MALE_ID not in report.model_dump_json()
    assert FEMALE_ID not in report.model_dump_json()