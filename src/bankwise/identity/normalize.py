import re
import unicodedata
from datetime import date
from typing import Optional

ARABIC_INDIC_DIGITS = str.maketrans("\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669", "0123456789")
EASTERN_ARABIC_DIGITS = str.maketrans("\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9", "0123456789")
DIRECTION_MARKS = re.compile("[\u200c\u200d\u200e\u200f\u202a-\u202e\u2066-\u2069]")
DIACRITICS = re.compile("[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed]")
TATWEEL = "\u0640"
ALEF_VARIANTS = str.maketrans("\u0623\u0625\u0622\u0671", "\u0627\u0627\u0627\u0627")
DATE_PATTERN = re.compile(r"(\d{4})\D{1,3}(\d{1,2})\D{1,3}(\d{1,2})")
YEAR_MONTH_PATTERN = re.compile(r"(\d{4})\D{1,3}(\d{1,2})")
MIN_YEAR = 1900
MAX_YEAR = 2100
MAX_TEXT_LENGTH = 200
SERIAL_STRIP = re.compile(r"[^A-Za-z0-9]")
MIN_SERIAL_LENGTH = 5
MAX_SERIAL_LENGTH = 14


def to_ascii_digits(text: str) -> str:
    return text.translate(ARABIC_INDIC_DIGITS).translate(EASTERN_ARABIC_DIGITS)


def digits_only(text: str) -> str:
    return "".join(character for character in to_ascii_digits(text) if character in "0123456789")


def clean_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", text)
    value = DIRECTION_MARKS.sub("", value)
    value = DIACRITICS.sub("", value)
    value = value.replace(TATWEEL, "")
    return re.sub(r"\s+", " ", value).strip()


def normalize_for_compare(text: str) -> str:
    value = clean_text(text).translate(ALEF_VARIANTS)
    return value.replace("\u0649", "\u064a").replace("\u0629", "\u0647")


def normalize_text(value: object, max_length: int = MAX_TEXT_LENGTH) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = clean_text(value)
    if not cleaned:
        return None
    return cleaned[:max_length]


def normalize_id(value: object) -> Optional[str]:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    digits = digits_only(str(value))
    return digits or None


def normalize_date(value: object) -> Optional[date]:
    if not isinstance(value, str):
        return None
    match = DATE_PATTERN.search(to_ascii_digits(value))
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    if not MIN_YEAR <= year <= MAX_YEAR:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def normalize_year_month(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    match = YEAR_MONTH_PATTERN.search(to_ascii_digits(value))
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    if not MIN_YEAR <= year <= MAX_YEAR or not 1 <= month <= 12:
        return None
    return f"{year:04d}-{month:02d}"
def normalize_serial(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = SERIAL_STRIP.sub("", to_ascii_digits(value)).upper()
    if not MIN_SERIAL_LENGTH <= len(cleaned) <= MAX_SERIAL_LENGTH:
        return None
    if not any(character.isdigit() for character in cleaned):
        return None
    return cleaned