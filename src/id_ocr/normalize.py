"""Safe text / number normalisation.

Three layers are kept distinct everywhere else in the code:
  1. original OCR text   - never modified
  2. normalised text     - digits -> Western, whitespace/bidi cleanup, edge junk removed
  3. final field value   - produced by the field extractor after validation

Arabic letters are NEVER substituted (no ي<->ى, ة<->ه, أ<->ا) in output values.
The loose ``match_key`` below exists ONLY for fuzzy comparison against closed
vocabularies and is never returned as a value.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Tuple

_AR_INDIC = "٠١٢٣٤٥٦٧٨٩"
_EXT_INDIC = "۰۱۲۳۴۵۶۷۸۹"
_DIGIT_MAP = {ord(c): str(i) for i, c in enumerate(_AR_INDIC)}
_DIGIT_MAP.update({ord(c): str(i) for i, c in enumerate(_EXT_INDIC)})

# bidi controls, zero-width chars, tatweel (purely typographic)
_INVISIBLE = re.compile("[​-‏‪-‮⁦-⁩﻿ـ]")
_WS = re.compile(r"\s+")
_ARABIC_LETTER = re.compile("[ء-يٱ]")
_DIAC = re.compile("[ً-ٰٟ]")


def to_western_digits(text: str) -> str:
    """Convert Arabic-Indic and Eastern Arabic/Persian digits to 0-9."""
    return text.translate(_DIGIT_MAP)


def normalize_unicode(text: str) -> str:
    """NFKC (folds presentation forms), strips invisible/bidi chars, collapses whitespace."""
    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLE.sub("", text)
    return _WS.sub(" ", text).strip()


def digits_only(text: str) -> str:
    """Keep only 0-9 after converting any Arabic digit variants."""
    return re.sub(r"\D", "", to_western_digits(text))


def has_arabic_letters(text: str) -> bool:
    return bool(_ARABIC_LETTER.search(text))


_TOKEN_CHAR = re.compile(r"[ء-يٱً-ٰٟ\d\-]")


def _clean_token(tok: str) -> str:
    """Drop foreign symbols (~ _ = ? ...) from a token.

    A short token (<= 3 chars) that contained any foreign symbol is treated as pure
    OCR noise and removed entirely (e.g. '~٤'); in longer tokens only the symbols are
    stripped so a real word glued to noise is preserved. Arabic letters are never touched.
    """
    kept = "".join(ch for ch in tok if _TOKEN_CHAR.match(ch))
    if kept == tok:
        return tok
    return "" if len(tok) <= 3 else kept


def clean_arabic_line(text: str) -> Tuple[str, bool]:
    """Normalise a printed Arabic line and drop OCR junk.

    Returns (cleaned_text, changed_beyond_whitespace) so callers can flag every edit.
    Only characters are REMOVED; nothing is substituted or invented.
    """
    base = normalize_unicode(text)
    toks = [_clean_token(t) for t in base.split(" ")]
    cleaned = " ".join(t for t in toks if t)
    cleaned = cleaned.strip(" -")   # a dangling hyphen at either edge is junk
    return cleaned, cleaned != base


def normalize_serial(text: str) -> str:
    """Upper-case latin alphanumerics only (no look-alike substitution)."""
    return re.sub(r"[^A-Z0-9]", "", normalize_unicode(text).upper())


def match_key(text: str) -> str:
    """Loose key for fuzzy vocabulary matching ONLY (never used as an output value)."""
    t = normalize_unicode(text)
    t = _DIAC.sub("", t)
    for a, b in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ى", "ي"), ("ة", "ه"), ("ؤ", "و"), ("ئ", "ي")):
        t = t.replace(a, b)
    return t.replace(" ", "")


def mask_national_id(nid: str | None) -> str:
    """Log-safe representation: only the last 4 digits survive."""
    if not nid:
        return "<none>"
    return "*" * max(len(nid) - 4, 0) + nid[-4:]
