"""Unit tests for normalisation, national-id/date validation, merging and the decision engine.

All identities below are SYNTHETIC. No real card data is used and no OCR model is needed.
"""
from datetime import date

import pytest

from id_ocr.config import OcrConfig
from id_ocr.decision import DecisionInput, decide
from id_ocr.extract import Candidate, FieldResult, SideResult, resolve
from id_ocr.merge import merge_sides
from id_ocr.normalize import (clean_arabic_line, digits_only, has_arabic_letters, mask_national_id, match_key,
                              normalize_serial, normalize_unicode, to_western_digits)
from id_ocr.validation import (cross_check_birth_date, cross_check_gender, date_order_issues, decode_national_id,
                               expiry_state, parse_date)

TODAY = date(2026, 10, 20)
# Synthetic: century 3 (2000s), 2000-01-01, governorate 01 (Cairo), odd serial digit => male.
NID = "30001010100011"
NID_FEMALE = "30001010100021"


def ar_digits(s: str) -> str:
    return s.translate({ord(str(i)): c for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")})


# --------------------------------------------------------------------------- normalisation
class TestNormalize:
    def test_arabic_indic_and_persian_digits_to_western(self):
        assert to_western_digits("٠١٢٣٤٥٦٧٨٩") == "0123456789"
        assert to_western_digits("۰۱۲۳۴۵۶۷۸۹") == "0123456789"
        assert to_western_digits("0123456789") == "0123456789"

    def test_digits_only_mixed(self):
        assert digits_only("٣ ٠٠٠ 1٠١-٠١") == "30001010" + "1"

    def test_arabic_letters_are_never_substituted(self):
        for word in ("مصطفى", "فاطمة", "أحمد", "إبراهيم", "هالة", "علي"):
            cleaned, _ = clean_arabic_line(word)
            assert cleaned == word, "normalisation must not change ى/ي, ة/ه or hamza forms"

    def test_match_key_is_only_for_comparison(self):
        assert match_key("أنثى") == match_key("انثي")      # loose key merges look-alikes ...
        assert clean_arabic_line("أنثى")[0] == "أنثى"        # ... but the output value is untouched

    def test_whitespace_and_invisible_characters(self):
        assert normalize_unicode("  علي ‏  حسن‫ ") == "علي حسن"
        assert normalize_unicode("محـــمد") == "محمد"           # tatweel is purely typographic

    def test_short_noise_token_removed_and_flagged(self):
        cleaned, changed = clean_arabic_line("شارع ~٤ النيل")
        assert cleaned == "شارع النيل" and changed

    def test_clean_line_does_not_invent_text(self):
        assert clean_arabic_line("")[0] == ""
        assert clean_arabic_line("~ _ =")[0] == ""

    def test_has_arabic_letters(self):
        assert has_arabic_letters("علي") and not has_arabic_letters("123 ABC")

    def test_serial_normalisation_no_lookalike_substitution(self):
        assert normalize_serial(" ab 1234567 ") == "AB1234567"
        assert normalize_serial("1B1234567") == "1B1234567"     # a leading '1' is NOT turned into 'I'

    def test_mask_national_id_keeps_only_last_four(self):
        assert mask_national_id(NID) == "*" * 10 + NID[-4:]
        assert mask_national_id(None) == "<none>"


# ---------------------------------------------------------------------------------- dates
class TestParseDate:
    @pytest.mark.parametrize("raw,expected", [
        ("2028/07/05", "2028-07-05"), ("2028-07-05", "2028-07-05"), ("20280705", "2028-07-05"),
        ("2021/07", "2021-07"), ("202107", "2021-07"),
        (ar_digits("2028/07/05"), "2028-07-05"), ("۲۰۲۸/۰۷/۰۵", "2028-07-05"),
    ])
    def test_valid_forms(self, raw, expected):
        p = parse_date(raw)
        assert p.ok and p.iso() == expected

    def test_slash_misread_as_digit_one_is_inferred_and_flagged(self):
        p = parse_date("٢٠٢٨١٠٧١٠٥")
        assert p.ok and p.iso() == "2028-07-05" and "separator_inferred" in p.flags
        p = parse_date("2021107")
        assert p.ok and p.iso() == "2021-07" and "separator_inferred" in p.flags

    @pytest.mark.parametrize("raw", ["", "abc", "2028/13/05", "2028/02/30", "1800/01/01", "2028", "12345678901"])
    def test_invalid_forms_return_failure_not_a_guess(self, raw):
        p = parse_date(raw)
        assert not p.ok and p.iso() is None and p.error

    def test_output_style(self):
        assert parse_date("2028/07/05").format("slash") == "2028/07/05"


# --------------------------------------------------------------------------- national id
class TestNationalId:
    def test_valid_structure_and_decoding(self):
        d = decode_national_id(NID, today=TODAY)
        assert d.valid and not d.errors
        assert d.birth_date == "2000-01-01"
        assert d.governorate_code == "01" and d.governorate == "Cairo"
        assert d.gender == "male"
        assert decode_national_id(NID_FEMALE, today=TODAY).gender == "female"

    def test_arabic_digits_accepted(self):
        assert decode_national_id(ar_digits(NID), today=TODAY).valid

    @pytest.mark.parametrize("bad,error", [
        ("3000101010001", "length_not_14"), ("300010101000111", "length_not_14"), ("", "length_not_14"),
        ("3000101010001X", "non_digit_characters"),
    ])
    def test_wrong_length_or_characters(self, bad, error):
        d = decode_national_id(bad, today=TODAY)
        assert not d.valid and error in d.errors

    def test_unsupported_century_digit(self):
        d = decode_national_id("10001010100011", today=TODAY)
        assert not d.valid and "unsupported_century_digit" in d.errors

    def test_invalid_calendar_date(self):
        d = decode_national_id("30013010100011", today=TODAY)          # month 13
        assert not d.valid and "birth_date_invalid" in d.errors
        d = decode_national_id("30002300100011", today=TODAY)          # 30 February
        assert "birth_date_invalid" in d.errors

    def test_birth_date_in_future(self):
        d = decode_national_id("39912310100011", today=TODAY)          # 2099-12-31
        assert "birth_date_in_future" in d.errors

    def test_unknown_governorate(self):
        d = decode_national_id("30001010700011", today=TODAY)          # code 07 not in default map
        assert not d.valid and "unknown_governorate_code" in d.errors

    def test_governorate_mapping_is_configurable(self):
        cfg = OcrConfig()
        cfg.governorates = {"07": "Custom"}
        assert decode_national_id("30001010700011", cfg, TODAY).valid

    def test_valid_structure_is_not_a_claim_of_authenticity(self):
        # nothing in the decode result suggests authenticity
        assert not hasattr(decode_national_id(NID, today=TODAY), "authentic")


class TestCrossChecks:
    def test_birth_date_match_mismatch_unavailable(self):
        assert cross_check_birth_date("2000-01-01", "2000-01-01") == "match"
        assert cross_check_birth_date(ar_digits("2000/01/01"), "2000-01-01") == "match"
        assert cross_check_birth_date("2000-01-02", "2000-01-01") == "mismatch"
        assert cross_check_birth_date(None, "2000-01-01") == "unavailable"
        assert cross_check_birth_date("garbage", "2000-01-01") == "unavailable"

    def test_gender_check(self):
        assert cross_check_gender("ذكر", "male") == "match"
        assert cross_check_gender("أنثى", "male") == "mismatch"
        assert cross_check_gender(None, "male") == "unavailable"

    def test_expiry_state(self):
        assert expiry_state("2028-07-05", TODAY) == "valid_until_future"
        assert expiry_state("2020-01-01", TODAY) == "expired"
        assert expiry_state("2026-10", TODAY) == "valid_until_future"     # month precision: whole month counts
        assert expiry_state("2026-09", TODAY) == "expired"
        assert expiry_state(None, TODAY) == "unknown"
        assert expiry_state("nonsense", TODAY) == "unknown"

    def test_date_order(self):
        assert date_order_issues("2021-07", "2028-07-05", TODAY) == []
        assert "issue_after_expiry" in date_order_issues("2030-01-01", "2028-07-05", TODAY)
        assert "issue_date_in_future" in date_order_issues("2031-01-01", None, TODAY)


# -------------------------------------------------------------------- candidate resolution
def cand(value, conf=0.9, variant="gray", normalized=None):
    return Candidate(raw=value or "", normalized=normalized if normalized is not None else (value or ""),
                     value=value, conf=conf, variant=variant, reader="ar")


class TestResolve:
    cfg = OcrConfig()

    def test_two_agreeing_reads_are_ok(self):
        fr = resolve("national_id", "front", [cand(NID, 0.9), cand(NID, 0.8, "up2")], self.cfg, numeric=True)
        assert fr.status == "ok" and fr.value == NID and fr.confidence == 0.8   # min, not an invented probability

    def test_single_read(self):
        fr = resolve("national_id", "front", [cand(NID), cand(None, 0.0, "up2", "")], self.cfg, numeric=True)
        assert fr.status == "single_read" and "some_attempts_unreadable" in fr.flags

    def test_low_confidence(self):
        fr = resolve("national_id", "front", [cand(NID, 0.2), cand(NID, 0.3, "up2")], self.cfg, numeric=True)
        assert fr.status == "low_confidence" and fr.value == NID

    def test_disagreement_is_a_conflict_and_nothing_is_picked(self):
        other = "30001010100022"
        fr = resolve("national_id", "front", [cand(NID, 0.6), cand(other, 0.99, "up2")], self.cfg, numeric=True)
        assert fr.status == "conflict" and fr.value is None and "candidate_conflict" in fr.flags

    def test_text_disagreement_policy_is_configurable(self):
        cfg = OcrConfig(on_text_disagreement="prefer_highest_confidence")
        fr = resolve("first_name", "front", [cand("أحمد", 0.5), cand("احمد", 0.9, "clahe")], cfg, numeric=False, text=True)
        assert fr.status == "low_confidence" and fr.value == "احمد" and "variant_disagreement" in fr.flags

    def test_unreadable_vs_invalid(self):
        assert resolve("x", "front", [cand(None, 0.0, normalized="")], self.cfg, True).status == "unreadable"
        assert resolve("x", "front", [cand(None, 0.4, normalized="123")], self.cfg, True).status == "invalid"


# -------------------------------------------------------------------------------- merging
def fr(name, value, status="ok", conf=0.9, side="front"):
    return FieldResult(name=name, side=side, value=value, status=status, confidence=conf if value else None)


class TestMerge:
    def test_same_value_on_both_sides_is_confirmed(self):
        m = merge_sides(SideResult("front", {"national_id": fr("national_id", NID)}),
                        SideResult("back", {"national_id": fr("national_id", NID, side="back")}))
        f = m.fields["national_id"]
        assert f.value == NID and "confirmed_on_both_sides" in f.flags

    def test_different_values_conflict(self):
        m = merge_sides(SideResult("front", {"national_id": fr("national_id", NID)}),
                        SideResult("back", {"national_id": fr("national_id", NID_FEMALE, side="back")}))
        f = m.fields["national_id"]
        assert f.value is None and f.status == "conflict" and "cross_side_conflict" in f.flags

    def test_weaker_empty_read_never_overwrites_a_valid_value(self):
        m = merge_sides(SideResult("front", {"national_id": fr("national_id", NID, "single_read", 0.7)}),
                        SideResult("back", {"national_id": fr("national_id", None, "unreadable", side="back")}))
        assert m.fields["national_id"].value == NID

    def test_value_is_not_promoted_when_other_side_conflicted(self):
        m = merge_sides(SideResult("front", {"national_id": fr("national_id", NID, "ok")}),
                        SideResult("back", {"national_id": fr("national_id", None, "conflict", side="back")}))
        f = m.fields["national_id"]
        assert f.value == NID and f.status == "single_read" and "other_side_unresolved" in f.flags

    def test_side_specific_fields_are_kept(self):
        m = merge_sides(SideResult("front", {"first_name": fr("first_name", "أحمد")}),
                        SideResult("back", {"job": fr("job", "طالب", side="back")}))
        assert m.fields["first_name"].value == "أحمد" and m.fields["job"].value == "طالب"


# ------------------------------------------------------------------------ decision engine
CFG = OcrConfig()


def dinp(**kw):
    base = dict(sides_supplied=["front"], field_status={"national_id": "ok", "first_name": "ok", "other_names": "ok"},
                required_fields=["national_id", "first_name", "other_names"], critical_fields=["national_id"],
                nid_structure="valid", expiry_state="unknown", attempt=1)
    base.update(kw)
    return DecisionInput(**base)


class TestDecision:
    def test_verified_requires_everything(self):
        d = decide(dinp(), CFG)
        assert d.status == "VERIFIED" and d.document_authenticity == "not_assessed"

    def test_fourteen_digits_alone_is_not_verified(self):
        d = decide(dinp(field_status={"national_id": "single_read", "first_name": "ok", "other_names": "ok"}), CFG)
        assert d.status == "MANUAL_REVIEW"

    def test_unknown_field_status_can_never_reach_verified(self):
        # fail-safe: only the explicit "ok" status may produce VERIFIED, even for statuses added in future
        d = decide(dinp(field_status={"national_id": "ok", "first_name": "some_future_status", "other_names": "ok"}), CFG)
        assert d.status == "MANUAL_REVIEW" and "not_ok:first_name" in d.reasons

    def test_structurally_invalid_id_goes_to_manual_review(self):
        d = decide(dinp(nid_structure="invalid"), CFG)
        assert d.status == "MANUAL_REVIEW" and "national_id_structure_invalid" in d.reasons

    @pytest.mark.parametrize("kw,reason", [
        (dict(birth_date_check="mismatch"), "birth_date_mismatch_with_national_id"),
        (dict(gender_check="mismatch"), "gender_mismatch_with_national_id"),
        (dict(id_sides_check="mismatch"), "national_id_front_back_mismatch"),
        (dict(date_issues=["issue_after_expiry"]), "issue_after_expiry"),
    ])
    def test_inconsistencies_go_to_manual_review(self, kw, reason):
        d = decide(dinp(**kw), CFG)
        assert d.status == "MANUAL_REVIEW" and reason in d.reasons and d.data_consistency == "inconsistent"

    def test_conflict_is_never_retried_or_resolved(self):
        d = decide(dinp(field_status={"national_id": "conflict", "first_name": "ok", "other_names": "ok"},
                        nid_structure="unresolved"), CFG)
        assert d.status == "MANUAL_REVIEW" and "conflict:national_id" in d.reasons

    def test_unreadable_required_field_retries_then_escalates(self):
        fs = {"national_id": "ok", "first_name": "unreadable", "other_names": "ok"}
        assert decide(dinp(field_status=fs, attempt=1), CFG).status == "RETRY"
        assert decide(dinp(field_status=fs, attempt=CFG.max_attempts), CFG).status == "MANUAL_REVIEW"

    def test_quality_issue_with_weak_read_retries(self):
        d = decide(dinp(field_status={"national_id": "low_confidence", "first_name": "ok", "other_names": "ok"},
                        quality_issues={"front": ["blurry"]}), CFG)
        assert d.status == "RETRY" and "quality:blurry" in d.reasons

    def test_quality_warning_alone_does_not_block_verified(self):
        d = decide(dinp(quality_issues={"front": ["too_dark"]}), CFG)
        assert d.status == "VERIFIED" and "quality:too_dark" in d.review_flags

    def test_expired_default_manual_review_and_configurable_rejection(self):
        assert decide(dinp(expiry_state="expired"), CFG).status == "MANUAL_REVIEW"
        assert decide(dinp(expiry_state="expired"), OcrConfig(expired_action="REJECTED")).status == "REJECTED"

    @pytest.mark.parametrize("code", ["file_missing", "unsupported_format", "file_too_large"])
    def test_unacceptable_input_is_rejected(self, code):
        d = decide(dinp(sides_supplied=[], input_errors=[code], field_status={}), CFG)
        assert d.status == "REJECTED"

    def test_no_input_is_rejected(self):
        assert decide(dinp(sides_supplied=[], field_status={}), CFG).status == "REJECTED"

    def test_require_both_sides(self):
        assert decide(dinp(), OcrConfig(require_both_sides=True)).status == "REJECTED"

    def test_recoverable_image_errors_retry(self):
        d = decide(dinp(sides_supplied=[], input_errors=["corrupted_image"], field_status={}), CFG)
        assert d.status == "RETRY"

    def test_decision_is_deterministic(self):
        assert decide(dinp(), CFG).as_dict() == decide(dinp(), CFG).as_dict()
