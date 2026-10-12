from datetime import date

import numpy as np
import pytest

from bankwise.core.config import load_identity_config
from bankwise.core.pii import mask_sensitive_line
from bankwise.identity.reader.base import IdVote
from bankwise.identity.reader.prompts import (
    BACK_PROMPT,
    BACK_ZOOM_PROMPT,
    FRONT_PROMPT,
    FRONT_TEXT_PROMPT,
    FRONT_ZOOM_PROMPT,
)
from bankwise.identity.reader.vlm_reader import (
    VlmFieldReader,
    compare_texts,
    id_read_notes,
    raw_lines,
)
from bankwise.identity.service import verify_identity_fields
from bankwise.identity.vision.schemas import VisionStatus
from identity_helpers import (
    FEMALE_ID,
    MALE_ID,
    TODAY,
    FakeCaller,
    back_response,
    back_zoom_response,
    failing,
    front_response,
    front_text_response,
    front_zoom_response,
    indic,
)
from test_validation_service import encoded_scene

CARD = np.zeros((638, 1012, 3), dtype=np.uint8)
ADDRESS_SPACED = "\u0627\u0644\u0645\u0633\u0627\u0643\u0646 \u0627\u0644\u0627 \u0642\u062a\u0635\u0627\u062f\u064a\u0629"
ADDRESS_JOINED = "\u0627\u0644\u0645\u0633\u0627\u0643\u0646 \u0627\u0644\u0627\u0642\u062a\u0635\u0627\u062f\u064a\u0629"
ADDRESS_LINE2 = "\u0627\u0644\u0642\u0635\u0627\u0635\u064a\u0646 \u0627\u0644\u062c\u062f\u064a\u062f\u0629 - \u0627\u0644\u0627\u0633\u0645\u0627\u0639\u064a\u0644\u064a\u0629"
FIRST = "\u0633\u0644\u064a\u0645\u0627\u0646"
OTHERS_YA = "\u0645\u062d\u0645\u062f \u0627\u0628\u0648\u0627\u0644\u0645\u0639\u0627\u0637\u064a"
OTHERS_MAQSURA = "\u0645\u062d\u0645\u062f \u0627\u0628\u0648\u0627\u0644\u0645\u0639\u0627\u0637\u0649"


@pytest.fixture(scope="module")
def cfg():
    return load_identity_config().reader


def consistent_front(first=FIRST, others=OTHERS_YA, line1=ADDRESS_SPACED, line2=ADDRESS_LINE2, **extra):
    lines = [first, others, line1, line2, indic("2004/12/20"), indic(MALE_ID)]
    return front_response(
        first_name=first,
        other_names=others,
        address_line1=line1,
        address_line2=line2,
        lines=lines,
        **extra,
    )


def text_zoom(first=FIRST, others=OTHERS_YA, line1=ADDRESS_SPACED, line2=ADDRESS_LINE2):
    return front_text_response(
        first_name=first,
        other_names=others,
        address_line1=line1,
        address_line2=line2,
        lines=[first, others, line1, line2],
    )


def read(cfg, front=None, front_text=None, **kwargs):
    caller = FakeCaller(
        front=front if front is not None else consistent_front(),
        front_text=front_text if front_text is not None else [text_zoom()],
        **kwargs,
    )
    return VlmFieldReader(cfg, caller=caller).read_front(CARD)


def test_front_prompt_requests_lines_and_literal_copying():
    assert '"lines"' in FRONT_PROMPT
    assert "gap inside a word" in FRONT_PROMPT
    assert "except lines" in FRONT_PROMPT
    assert '"lines"' in FRONT_TEXT_PROMPT
    assert "gap inside a word" in BACK_PROMPT
for prompt in (FRONT_ZOOM_PROMPT, BACK_ZOOM_PROMPT):
    assert "Convert digits to Western digits" in prompt
    assert "gap inside a word" not in prompt


def test_agreeing_text_reads_are_confirmed_without_warnings(cfg):
    result = read(cfg)
    assert result.warnings == []
    assert result.review_reasons == []
    assert result.address == ADDRESS_SPACED + " " + ADDRESS_LINE2


def test_lines_keep_order_and_printed_spacing(cfg):
    result = read(cfg)
    assert result.lines[0] == FIRST
    assert result.lines[2] == ADDRESS_SPACED
    assert result.lines[3] == ADDRESS_LINE2
    assert len(result.lines) == 6


def test_spacing_only_difference_is_a_warning_and_keeps_primary_text(cfg):
    result = read(cfg, front_text=[text_zoom(line1=ADDRESS_JOINED)])
    assert "address_spacing_differs" in result.warnings
    assert result.review_reasons == []
    assert result.address.startswith(ADDRESS_SPACED)


def test_letter_variant_difference_needs_review_and_keeps_primary_text(cfg):
    result = read(cfg, front_text=[text_zoom(others=OTHERS_MAQSURA)])
    assert "name_reads_disagree" in result.review_reasons
    assert "name_reads_disagree" in result.warnings
    assert result.other_names == OTHERS_YA


def test_dropped_word_needs_review_and_is_not_silently_replaced(cfg):
    short = text_zoom(line2="\u0627\u0644\u062c\u062f\u064a\u062f\u0629 - \u0627\u0644\u0627\u0633\u0645\u0627\u0639\u064a\u0644\u064a\u0629")
    result = read(cfg, front_text=[short])
    assert "address_reads_disagree" in result.review_reasons
    assert ADDRESS_LINE2 in result.address


def test_missing_value_in_second_read_counts_as_disagreement(cfg):
    result = read(cfg, front_text=[text_zoom(first=None)])
    assert "name_reads_disagree" in result.review_reasons


def test_failed_text_read_needs_review(cfg):
    result = read(cfg, front_text=[failing()])
    assert "text_read_1_failed" in result.warnings
    assert "text_cross_check_failed" in result.review_reasons


def test_disabled_text_read_is_only_a_warning(cfg):
    disabled = cfg.model_copy(update={"front_text_regions": []})
    caller = FakeCaller(front=consistent_front())
    result = VlmFieldReader(disabled, caller=caller).read_front(CARD)
    assert "text_not_cross_checked" in result.warnings
    assert result.review_reasons == []


def test_field_missing_from_lines_is_reported(cfg):
    front = consistent_front()
    front["lines"] = [FIRST, OTHERS_YA, ADDRESS_LINE2]
    result = read(cfg, front=front)
    assert "field_not_in_lines:address_line1" in result.warnings
    assert "field_not_in_lines:first_name" not in result.warnings


def test_missing_lines_are_reported(cfg):
    front = consistent_front()
    front.pop("lines")
    result = read(cfg, front=front)
    assert "text_lines_missing" in result.warnings


def test_raw_lines_keep_text_untouched_except_outer_whitespace():
    assert raw_lines("text") == []
    assert raw_lines([" a  b ", "", None, 5, "c"]) == ["a  b", "c"]
    assert len(raw_lines(["x"] * 40)) == 20
    assert len(raw_lines(["x" * 900])[0]) == 500

def test_compare_texts_states():
    assert compare_texts(["a b", "a b"]) == "confirmed"
    assert compare_texts(["a b", "ab"]) == "spacing"
    assert compare_texts(["a b", "a c"]) == "disagree"
    assert compare_texts(["a b", None]) == "disagree"
    assert compare_texts([None, None]) == "empty"


def test_id_read_notes_name_the_non_confirming_reads():
    reads = [
        ("full_read", {"national_id": MALE_ID}),
        ("zoom_read_1", {"national_id": None}),
        ("zoom_read_2", {"national_id": "2950314010123"}),
    ]
    assert id_read_notes(reads) == ["id_read_missing:zoom_read_1", "id_read_invalid:zoom_read_2"]


def test_unconfirmed_id_read_is_visible_in_warnings(cfg):
    result = read(cfg, front_zoom=[front_zoom_response(None), front_zoom_response()])
    assert "id_read_missing:zoom_read_1" in result.warnings
    assert result.national_id == IdVote(MALE_ID, False, 2, 3)


def test_date_conflicts_need_review(cfg):
    front = read(cfg, front_zoom=[front_zoom_response(birth_date="1995-03-15")] * 2)
    assert "birth_date_reads_disagree" in front.review_reasons
    caller = FakeCaller(back_zoom=[back_zoom_response()], back=back_response(expiry_date="2030-05-30"))
    back = VlmFieldReader(cfg, caller=caller).read_back(CARD)
    assert back.review_reasons == []
    conflicting = FakeCaller(back=back_response(issue_date="2025-05"), back_zoom=[back_zoom_response(issue_date="2024-01")])
    back = VlmFieldReader(cfg, caller=conflicting).read_back(CARD)
    assert "issue_date_reads_disagree" in back.review_reasons


def test_mask_sensitive_line():
    assert mask_sensitive_line("abc") == "abc"
    assert mask_sensitive_line(indic("2004/12/20")) == indic("2004/12/20")
    masked = mask_sensitive_line(indic(MALE_ID))
    assert indic(MALE_ID) not in masked
    spaced = mask_sensitive_line(" ".join(indic(MALE_ID)))
    assert indic(MALE_ID[3:8]) not in spaced


def service_report(caller_kwargs, full_config):
    reader = VlmFieldReader(full_config.reader, caller=FakeCaller(**caller_kwargs))
    return verify_identity_fields(encoded_scene(), encoded_scene(), reader, full_config, TODAY)


@pytest.fixture(scope="module")
def full_cfg():
    return load_identity_config()


def test_report_lines_are_masked_and_hidden_when_redacted(full_cfg):
    front = consistent_front()
    front["lines"][-1] = " ".join(indic(MALE_ID))
    report = service_report({"front": front, "front_text": [text_zoom()]}, full_cfg)
    dumped = report.model_dump_json()
    assert report.front_text_lines[0] == FIRST
    assert report.front_text_lines[2] == ADDRESS_SPACED
    assert indic(MALE_ID) not in dumped
    assert MALE_ID not in dumped
    assert " ".join(indic(MALE_ID)[3:9]) not in dumped
    assert report.redacted()["front_text_lines"] == "<redacted>:6_lines"


def test_clean_card_does_not_need_review(full_cfg):
    report = service_report({"front": consistent_front(), "front_text": [text_zoom()]}, full_cfg)
    assert report.needs_review is False
    assert report.review_reasons == []


def test_disagreeing_text_marks_report_for_review(full_cfg):
    report = service_report(
        {"front": consistent_front(), "front_text": [text_zoom(others=OTHERS_MAQSURA)]}, full_cfg
    )
    assert report.needs_review is True
    assert "name_reads_disagree" in report.review_reasons


def test_failed_check_marks_report_for_review(full_cfg):
    report = service_report(
        {
            "front": consistent_front(),
            "front_text": [text_zoom()],
            "back": back_response(national_id=FEMALE_ID),
            "back_zoom": [back_zoom_response(FEMALE_ID)],
        },
        full_cfg,
    )
    assert report.needs_review is True
    assert "check_failed:front_back_id_match" in report.review_reasons


def test_vision_failure_does_not_call_reader_or_flag_review(full_cfg):
    class Exploding:
        name = "x"

        def read_front(self, card):
            raise AssertionError("called")

        def read_back(self, card):
            raise AssertionError("called")

    import cv2

    blurred = cv2.GaussianBlur(cv2.imdecode(np.frombuffer(encoded_scene(), np.uint8), 1), (0, 0), 12)
    ok, buffer = cv2.imencode(".jpg", blurred)
    report = verify_identity_fields(buffer.tobytes(), encoded_scene(), Exploding(), full_cfg, TODAY)
    assert report.reader_called is False
    assert report.front_image.status != VisionStatus.OK
    assert report.needs_review is False