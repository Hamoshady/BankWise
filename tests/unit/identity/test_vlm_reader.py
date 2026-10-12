from datetime import date

import numpy as np
import pytest

from bankwise.core.config import load_identity_config
from bankwise.core.errors import ReaderError
from bankwise.identity.reader.base import IdVote
from bankwise.identity.reader.vlm_reader import (
    VlmFieldReader,
    crop_region,
    parse_gender,
    vote_id,
    vote_value,
)
from bankwise.identity.schemas import Gender
from identity_helpers import (
    FEMALE_ID,
    FEMALE_TEXT,
    MALE_ID,
    MALE_TEXT,
    FakeCaller,
    back_response,
    back_text_response,
    back_zoom_response,
    failing,
    front_response,
    front_zoom_response,
    indic,
)

CARD = np.zeros((638, 1012, 3), dtype=np.uint8)


@pytest.fixture(scope="module")
def cfg():
    return load_identity_config().reader


def reader(cfg, **queues):
    return VlmFieldReader(cfg, caller=FakeCaller(**queues))


def test_vote_id_all_reads_agree():
    assert vote_id([MALE_ID, MALE_ID, MALE_ID]) == IdVote(MALE_ID, False, 3, 3)


def test_vote_id_conflicting_valid_ids_are_ambiguous():
    vote = vote_id([MALE_ID, MALE_ID, FEMALE_ID])
    assert vote.ambiguous
    assert vote.support == 0


def test_vote_id_ignores_invalid_and_missing_reads():
    vote = vote_id([MALE_ID, "2950314010123", None])
    assert vote == IdVote(MALE_ID, False, 1, 3)


def test_vote_id_without_any_valid_read_keeps_best_invalid_candidate():
    vote = vote_id(["2950314010123", "2950314010123", None])
    assert vote.value == "2950314010123"
    assert vote.support == 0
    assert not vote.ambiguous


def test_vote_id_no_reads():
    assert vote_id([None, None]) == IdVote(None, False, 0, 2)


def test_vote_value_conflict_returns_none():
    assert vote_value([date(2000, 1, 1), None, date(2000, 1, 1)]) == (date(2000, 1, 1), False)
    assert vote_value([date(2000, 1, 1), date(2001, 1, 1)]) == (None, True)
    assert vote_value([None, None]) == (None, False)


def test_parse_gender():
    assert parse_gender(MALE_TEXT) == Gender.MALE
    assert parse_gender(FEMALE_TEXT) == Gender.FEMALE
    assert parse_gender("male") == Gender.MALE
    assert parse_gender("Female") == Gender.FEMALE
    assert parse_gender("unknown") is None
    assert parse_gender(None) is None


def test_crop_region_scales_and_rejects_empty_regions():
    assert crop_region(CARD, (0.0, 0.0, 0.5, 0.5), 2.0).shape == (638, 1012, 3)
    with pytest.raises(ReaderError):
        crop_region(CARD, (0.5, 0.5, 0.5, 0.5), 2.0)


def test_front_read_agreeing_reads(cfg):
    caller = FakeCaller(front=front_response(indic(MALE_ID)), front_zoom=[front_zoom_response(indic(MALE_ID))] * 2)
    result = VlmFieldReader(cfg, caller=caller).read_front(CARD)
    assert result.national_id == IdVote(MALE_ID, False, 3, 3)
    assert result.birth_date == date(1995, 3, 14)
    assert result.first_name == "FirstName"
    assert result.address == "Street one City two"
    assert result.warnings == []


def test_front_read_conflicting_zoom_makes_id_ambiguous(cfg):
    result = reader(cfg, front_zoom=[front_zoom_response(FEMALE_ID), front_zoom_response()]).read_front(CARD)
    assert result.national_id.ambiguous


def test_front_read_zoom_failure_is_a_warning_not_an_error(cfg):
    result = reader(cfg, front_zoom=[failing(), front_zoom_response()]).read_front(CARD)
    assert "zoom_read_1_failed" in result.warnings
    assert result.national_id.reads == 2
    assert result.national_id.value == MALE_ID


def test_front_read_main_failure_propagates(cfg):
    with pytest.raises(ReaderError):
        reader(cfg, front=failing()).read_front(CARD)


def test_front_read_birth_date_disagreement_is_dropped(cfg):
    result = reader(cfg, front_zoom=[front_zoom_response(birth_date="1995-03-15")] * 2).read_front(CARD)
    assert result.birth_date is None
    assert "birth_date_reads_disagree" in result.warnings


def test_front_read_tolerates_non_dict_and_garbage_fields(cfg):
    caller = FakeCaller(front=["not", "a", "dict"], front_zoom=[{"national_id": {"x": 1}}] * 2)
    result = VlmFieldReader(cfg, caller=caller).read_front(CARD)
    assert result.national_id.value is None
    assert result.first_name is None
    assert result.birth_date is None


def test_front_read_side_mismatch_and_unreadable_are_reported(cfg):
    result = reader(cfg, front=front_response(side="back", unreadable=["address_line2", 5])).read_front(CARD)
    assert "side_mismatch" in result.warnings
    assert result.unreadable == ["address_line2"]


def test_front_text_fields_are_length_limited(cfg):
    result = reader(cfg, front=front_response(first_name="x" * 1000)).read_front(CARD)
    assert len(result.first_name) == 200


def test_back_read_parses_dates_gender_and_id(cfg):
    caller = FakeCaller(
    back=back_response(indic(MALE_ID), FEMALE_TEXT, indic("2025/05"), indic("2030/05/30")),
    back_zoom=[back_zoom_response(indic(MALE_ID), indic("2025/05"))],
    back_text=[back_text_response(gender=FEMALE_TEXT, expiry_date=indic("2030/05/30"))],
)
    result = VlmFieldReader(cfg, caller=caller).read_back(CARD)
    assert result.gender == Gender.FEMALE
    assert result.issue_year_month == "2025-05"
    assert result.expiry_date == date(2030, 5, 30)
    assert result.national_id == IdVote(MALE_ID, False, 2, 2)


def test_back_read_unrecognized_gender_is_flagged(cfg):
    result = reader(cfg, back=back_response(gender="???"), back_text=[back_text_response(gender="???")]).read_back(CARD)
    assert result.gender is None
    assert "gender_text_unrecognized" in result.warnings


def test_back_read_issue_date_disagreement(cfg):
    result = reader(cfg, back_zoom=[back_zoom_response(issue_date="2024-05")]).read_back(CARD)
    assert result.issue_year_month is None
    assert "issue_date_reads_disagree" in result.warnings