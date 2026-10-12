from datetime import date

from bankwise.identity.national_id import parse_national_id
from bankwise.identity.normalize import (
    clean_text,
    digits_only,
    normalize_date,
    normalize_for_compare,
    normalize_id,
    normalize_text,
    normalize_year_month,
    to_ascii_digits,
)
from bankwise.identity.schemas import Gender
from identity_helpers import AGE_115_ID, FEMALE_ID, MALE_ID, TODAY, indic

EASTERN = str.maketrans("0123456789", "\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9")


def test_digit_conversion_handles_both_digit_scripts():
    assert to_ascii_digits(indic("2950314")) == "2950314"
    assert digits_only("\u06f2\u06f9 abc \u0665") == "295"
    mixed = indic(MALE_ID[:7]) + MALE_ID[7:].translate(EASTERN)
    assert digits_only(mixed) == MALE_ID


def test_clean_text_removes_marks_and_diacritics():
    assert clean_text("\u0645\u064f\u062d\u064e\u0645\u0651\u064e\u062f\u200f") == "\u0645\u062d\u0645\u062f"


def test_normalize_for_compare_unifies_letters():
    assert normalize_for_compare("\u0623\u0646\u062b\u0649") == "\u0627\u0646\u062b\u064a"


def test_normalize_text_limits_length_and_rejects_non_strings():
    assert normalize_text("x" * 500) == "x" * 200
    assert normalize_text("   ") is None
    assert normalize_text(12) is None
    assert normalize_text(None) is None


def test_normalize_id_accepts_strings_and_ints_only():
    assert normalize_id(indic(MALE_ID)) == MALE_ID
    assert normalize_id(" 2950 3140-101234 ") == MALE_ID
    assert normalize_id(int(MALE_ID)) == MALE_ID
    assert normalize_id(2.5) is None
    assert normalize_id(True) is None
    assert normalize_id(None) is None
    assert normalize_id("abc") is None


def test_normalize_date_variants():
    assert normalize_date("2028-05-30") == date(2028, 5, 30)
    assert normalize_date(indic("2028/05/30")) == date(2028, 5, 30)
    assert normalize_date("2028-13-45") is None
    assert normalize_date("1850-01-01") is None
    assert normalize_date("2028-05") is None
    assert normalize_date(20280530) is None


def test_normalize_year_month():
    assert normalize_year_month("2025-05") == "2025-05"
    assert normalize_year_month(indic("2025/5")) == "2025-05"
    assert normalize_year_month("2025-13") is None
    assert normalize_year_month(None) is None


def test_valid_male_id():
    info = parse_national_id(MALE_ID, TODAY)
    assert info.format_valid
    assert info.gender == Gender.MALE
    assert info.birth_date == date(1995, 3, 14)
    assert info.age_years == 31
    assert MALE_ID not in info.masked


def test_valid_female_id():
    info = parse_national_id(indic(FEMALE_ID), TODAY)
    assert info.format_valid
    assert info.gender == Gender.FEMALE


def test_structural_failures():
    assert "invalid_length" in parse_national_id("2950314010123", TODAY).issues
    assert "invalid_birth_date" in parse_national_id("29513140101234", TODAY).issues
    assert "invalid_century_digit" in parse_national_id("49503140101234", TODAY).issues
    assert "birth_date_in_future" in parse_national_id("39903140101234", TODAY).issues


def test_unknown_governorate_is_warning_only():
    info = parse_national_id("29503149901234", TODAY)
    assert info.format_valid
    assert "unknown_governorate_code" in info.warnings


def test_age_over_100_is_a_warning_only():
    info = parse_national_id(AGE_115_ID, TODAY)
    assert info.format_valid
    assert info.issues == []
    assert "age_over_100" in info.warnings


def test_age_boundaries():
    assert "age_over_100" not in parse_national_id("22610100101234", TODAY).warnings
    assert "age_over_100" in parse_national_id("22510100101234", TODAY).warnings
    at_limit = parse_national_id("20610100101234", TODAY)
    assert at_limit.age_years == 120 and at_limit.format_valid
    over = parse_national_id("20503140101234", TODAY)
    assert over.age_years == 121
    assert not over.format_valid
    assert "implausible_age" in over.issues