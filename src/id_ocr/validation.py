"""Structural validation for Egyptian national ID numbers and printed dates.

IMPORTANT: everything here is STRUCTURAL / CONSISTENCY checking only.
A structurally valid number is not proof that a card is genuine or that it
belongs to the person presenting it. No check-digit algorithm is applied
because none has been verified for this project.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

from .config import OcrConfig
from .normalize import match_key, to_western_digits


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------
@dataclass
class ParsedDate:
    ok: bool
    year: Optional[int] = None
    month: Optional[int] = None
    day: Optional[int] = None
    flags: List[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def precision(self) -> Optional[str]:
        if not self.ok:
            return None
        return "day" if self.day is not None else "month"

    def iso(self) -> Optional[str]:
        if not self.ok:
            return None
        if self.day is None:
            return f"{self.year:04d}-{self.month:02d}"
        return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"

    def format(self, style: str = "iso") -> Optional[str]:
        iso = self.iso()
        if iso is None:
            return None
        return iso.replace("-", "/") if style == "slash" else iso


def _fail(msg: str) -> ParsedDate:
    return ParsedDate(ok=False, error=msg)


def _build(y: int, m: int, d: Optional[int], flags: List[str]) -> ParsedDate:
    if not (1900 <= y <= 2100):
        return _fail("year_out_of_range")
    if not (1 <= m <= 12):
        return _fail("month_invalid")
    if d is not None:
        try:
            date(y, m, d)
        except ValueError:
            return _fail("day_invalid")
    return ParsedDate(ok=True, year=y, month=m, day=d, flags=flags)


def parse_date(raw: str) -> ParsedDate:
    """Parse a year-first printed date: YYYY/MM/DD, YYYY/MM, or digits-only forms.

    Observed behaviour of EasyOCR's Arabic model on these cards: the '/'
    separator is frequently returned as the digit '١' (so 2028/07/05 arrives as
    '٢٠٢٨١٠٧١٠٥'). A 10-digit string with '1' at positions 5 and 8 (or a
    7-digit string with '1' at position 5) is therefore read as separators and
    flagged ``separator_inferred`` instead of being silently accepted.
    """
    s = re.sub(r"\s+", "", to_western_digits(raw or ""))
    if not s:
        return _fail("empty")

    m = re.fullmatch(r"(\d{4})[/\-.](\d{1,2})(?:[/\-.](\d{1,2}))?", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), m.group(3)
        return _build(y, mo, int(d) if d else None, [])

    if not s.isdigit():
        return _fail("unexpected_characters")

    if len(s) == 8:
        return _build(int(s[:4]), int(s[4:6]), int(s[6:8]), [])
    if len(s) == 6:
        return _build(int(s[:4]), int(s[4:6]), None, [])
    if len(s) == 10 and s[4] == "1" and s[7] == "1":
        return _build(int(s[:4]), int(s[5:7]), int(s[8:10]), ["separator_inferred"])
    if len(s) == 7 and s[4] == "1":
        return _build(int(s[:4]), int(s[5:7]), None, ["separator_inferred"])
    return _fail("unsupported_length")


# --------------------------------------------------------------------------
# National ID
# --------------------------------------------------------------------------
@dataclass
class NidDecode:
    valid: bool
    errors: List[str] = field(default_factory=list)
    birth_date: Optional[str] = None      # ISO, DECODED from the number (not OCR'd)
    century: Optional[int] = None
    governorate_code: Optional[str] = None
    governorate: Optional[str] = None
    gender: Optional[str] = None          # "male" | "female"

    def as_dict(self) -> Dict[str, object]:
        return {
            "valid_structure": self.valid, "errors": list(self.errors),
            "decoded_birth_date": self.birth_date, "decoded_governorate_code": self.governorate_code,
            "decoded_governorate": self.governorate, "decoded_gender": self.gender,
        }


def decode_national_id(nid: Optional[str], cfg: Optional[OcrConfig] = None, today: Optional[date] = None) -> NidDecode:
    """Decode and structurally validate a 14-digit Egyptian national ID."""
    cfg = cfg or OcrConfig()
    today = today or date.today()
    errors: List[str] = []
    out = NidDecode(valid=False)

    s = to_western_digits(nid or "")
    if not s.isdigit():
        errors.append("non_digit_characters")
    if len(s) != 14:
        errors.append("length_not_14")
    if errors:
        out.errors = errors
        return out

    century = cfg.first_digit_centuries.get(s[0])
    if century is None:
        errors.append("unsupported_century_digit")
    else:
        out.century = century
        try:
            bd = date(century + int(s[1:3]), int(s[3:5]), int(s[5:7]))
            out.birth_date = bd.isoformat()
            if bd > today:
                errors.append("birth_date_in_future")
        except ValueError:
            errors.append("birth_date_invalid")

    out.governorate_code = s[7:9]
    out.governorate = cfg.governorates.get(s[7:9])
    if out.governorate is None:
        errors.append("unknown_governorate_code")

    out.gender = "male" if int(s[12]) % 2 == 1 else "female"
    out.errors = errors
    out.valid = not errors
    return out


def cross_check_birth_date(ocr_birth_date: Optional[str], decoded_birth_date: Optional[str]) -> str:
    """'match' | 'mismatch' | 'unavailable'. Never overwrites either value."""
    if not ocr_birth_date or not decoded_birth_date:
        return "unavailable"
    p = parse_date(ocr_birth_date)
    if not p.ok or p.day is None:
        return "unavailable"
    return "match" if p.iso() == decoded_birth_date else "mismatch"


_GENDER_AR = {"male": "ذكر", "female": "أنثى"}


def cross_check_gender(ocr_gender: Optional[str], decoded_gender: Optional[str]) -> str:
    if not ocr_gender or not decoded_gender:
        return "unavailable"
    return "match" if match_key(ocr_gender) == match_key(_GENDER_AR[decoded_gender]) else "mismatch"


def expiry_state(expiry_iso: Optional[str], today: Optional[date] = None) -> str:
    """'valid_until_future' | 'expired' | 'unknown'.

    Only compares the PRINTED expiry date with today's date. No universal
    validity period is assumed.
    """
    today = today or date.today()
    p = parse_date(expiry_iso or "")
    if not p.ok:
        return "unknown"
    if p.day is None:
        # month precision: card considered valid through the whole printed month
        end = (p.year, p.month)
        return "expired" if end < (today.year, today.month) else "valid_until_future"
    return "expired" if date(p.year, p.month, p.day) < today else "valid_until_future"


def date_order_issues(issue_iso: Optional[str], expiry_iso: Optional[str], today: Optional[date] = None) -> List[str]:
    today = today or date.today()
    issues: List[str] = []
    i, e = parse_date(issue_iso or ""), parse_date(expiry_iso or "")
    if i.ok and (i.year, i.month, i.day or 1) > (today.year, today.month, today.day):
        issues.append("issue_date_in_future")
    if i.ok and e.ok and (i.year, i.month, i.day or 1) > (e.year, e.month, e.day or 1):
        issues.append("issue_after_expiry")
    return issues
