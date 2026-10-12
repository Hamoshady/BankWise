import re
from collections import Counter
from typing import Dict, List, Optional, Sequence, Tuple, TypeVar

import cv2
import numpy as np

from bankwise.core.config import ReaderConfig, Region
from bankwise.core.errors import ReaderError
from bankwise.identity.national_id import parse_national_id
from bankwise.identity.normalize import (
    clean_text,
    normalize_date,
    normalize_for_compare,
    normalize_id,
    normalize_serial,
    normalize_text,
    normalize_year_month,
)
from bankwise.identity.reader.base import BackRead, FrontRead, IdVote
from bankwise.identity.reader.prompts import (
    BACK_PROMPT,
    BACK_TEXT_PROMPT,
    BACK_ZOOM_PROMPT,
    FRONT_PROMPT,
    FRONT_TEXT_PROMPT,
    FRONT_ZOOM_PROMPT,
    MALE_WORD,
)
from bankwise.identity.reader.providers import Caller, build_caller
from bankwise.identity.schemas import Gender

T = TypeVar("T")
Labelled = List[Tuple[str, dict]]
FEMALE_COMPARE = "\u0627\u0646\u062b\u064a"
MALE_COMPARE = normalize_for_compare(MALE_WORD)
MAX_UNREADABLE = 20
MAX_LINES = 20
MAX_LINE_LENGTH = 500
TEXT_FIELDS = ("first_name", "other_names", "address_line1", "address_line2")
FRONT_LINE_FIELDS = TEXT_FIELDS
BACK_BASE_KEYS = ("job",)
BACK_SENSITIVE_KEYS = ("religion", "marital_status")
LETTER_SPACED_RATIO = 0.6
MIN_TOKENS_FOR_SPACING_CHECK = 4
PRESENTATION_FORMS = re.compile("[\ufb50-\ufdff\ufe70-\ufeff]")
ARABIC_LETTER = re.compile("[\u0621-\u064a]")


def crop_region(card: np.ndarray, region: Region, scale: float) -> np.ndarray:
    height, width = card.shape[:2]
    x0, y0, x1, y1 = region
    crop = card[int(y0 * height) : int(y1 * height), int(x0 * width) : int(x1 * width)]
    if crop.size == 0:
        raise ReaderError("empty crop region")
    return cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)


def vote_id(reads: Sequence[Optional[str]]) -> IdVote:
    total = len(reads)
    valid = [item for item in reads if item and parse_national_id(item).format_valid]
    distinct = list(dict.fromkeys(valid))
    if len(distinct) > 1:
        return IdVote(distinct[0], True, 0, total)
    if len(distinct) == 1:
        return IdVote(distinct[0], False, len(valid), total)
    present = [item for item in reads if item]
    if present:
        return IdVote(Counter(present).most_common(1)[0][0], False, 0, total)
    return IdVote(None, False, 0, total)


def vote_value(values: Sequence[Optional[T]]) -> Tuple[Optional[T], bool]:
    present = [item for item in values if item is not None]
    if not present:
        return None, False
    if all(item == present[0] for item in present):
        return present[0], False
    return None, True


def id_read_notes(reads: Labelled) -> List[str]:
    notes: List[str] = []
    for label, raw in reads:
        value = normalize_id(raw.get("national_id"))
        if value is None:
            notes.append(f"id_read_missing:{label}")
        elif not parse_national_id(value).format_valid:
            notes.append(f"id_read_invalid:{label}")
    return notes


def parse_gender(value: object) -> Optional[Gender]:
    if not isinstance(value, str):
        return None
    compared = normalize_for_compare(value).lower()
    if compared in ("male", "m") or MALE_COMPARE in compared:
        return Gender.MALE
    if compared in ("female", "f") or FEMALE_COMPARE in compared:
        return Gender.FEMALE
    return None


def as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def unreadable_fields(raw: dict) -> List[str]:
    items = raw.get("unreadable")
    if not isinstance(items, list):
        return []
    return [str(item)[:40] for item in items[:MAX_UNREADABLE] if isinstance(item, str)]


def raw_lines(value: object) -> List[str]:
    if not isinstance(value, list):
        return []
    lines: List[str] = []
    for item in value[:MAX_LINES]:
        if isinstance(item, str) and item.strip():
            lines.append(item.strip()[:MAX_LINE_LENGTH])
    return lines


def arabic_line_notes(lines: Sequence[str], prefix: str) -> List[str]:
    notes: List[str] = []
    if any(PRESENTATION_FORMS.search(line) for line in lines):
        notes.append(f"{prefix}_lines_presentation_forms")
    for line in lines:
        tokens = line.split()
        if len(tokens) < MIN_TOKENS_FOR_SPACING_CHECK or not ARABIC_LETTER.search(line):
            continue
        singles = sum(1 for token in tokens if len(token) == 1)
        if singles >= LETTER_SPACED_RATIO * len(tokens):
            notes.append(f"{prefix}_lines_letters_spaced")
            break
    return notes


def join_parts(*parts: Optional[str]) -> Optional[str]:
    return " ".join(part for part in parts if part) or None


def text_fields(raw: dict) -> Dict[str, Optional[str]]:
    values = {key: normalize_text(raw.get(key)) for key in TEXT_FIELDS}
    return {
        "name": join_parts(values["first_name"], values["other_names"]),
        "address": join_parts(values["address_line1"], values["address_line2"]),
    }


def back_text_fields(raw: dict, keys: Sequence[str]) -> Dict[str, Optional[str]]:
    return {key: normalize_text(raw.get(key)) for key in keys}


def squash(text: str) -> str:
    return re.sub(r"\s+", "", clean_text(text))


def compare_texts(values: Sequence[Optional[str]]) -> str:
    present = [item for item in values if item]
    if not present:
        return "empty"
    if len(present) != len(values):
        return "disagree"
    if all(item == present[0] for item in present):
        return "confirmed"
    if all(squash(item) == squash(present[0]) for item in present):
        return "spacing"
    return "disagree"


def compare_groups(
    primary: Dict[str, Optional[str]],
    others: Sequence[Dict[str, Optional[str]]],
    keys: Sequence[str],
    warnings: List[str],
    reasons: List[str],
) -> None:
    for key in keys:
        state = compare_texts([primary[key]] + [item[key] for item in others])
        if state == "spacing":
            warnings.append(f"{key}_spacing_differs")
        elif state == "disagree":
            warnings.append(f"{key}_reads_disagree")
            reasons.append(f"{key}_reads_disagree")


def fields_missing_from_lines(
    raw: dict, lines: List[str], keys: Sequence[str], missing_note: str
) -> List[str]:
    if not lines:
        return [missing_note]
    present = {clean_text(line) for line in lines}
    notes: List[str] = []
    for key in keys:
        value = normalize_text(raw.get(key))
        if value and value not in present:
            notes.append(f"field_not_in_lines:{key}")
    return notes


class VlmFieldReader:
    def __init__(self, cfg: ReaderConfig, caller: Optional[Caller] = None) -> None:
        self.cfg = cfg
        self.caller = caller or build_caller(cfg)
        self.name = f"{cfg.provider}:{cfg.model}"

    def labelled_reads(
        self,
        card: np.ndarray,
        regions: Sequence[Region],
        prompt: str,
        prefix: str,
        warnings: List[str],
    ) -> Labelled:
        reads: Labelled = []
        for index, region in enumerate(regions, start=1):
            try:
                crop = crop_region(card, region, self.cfg.zoom_scale)
                reads.append((f"{prefix}_{index}", as_dict(self.caller(crop, prompt))))
            except ReaderError:
                warnings.append(f"{prefix}_{index}_failed")
        return reads

    def cross_check(
        self,
        primary: Dict[str, Optional[str]],
        text_reads: Labelled,
        keys: Sequence[str],
        configured: bool,
        names: Tuple[str, str],
        parse,
        warnings: List[str],
        reasons: List[str],
    ) -> None:
        if not text_reads:
            if configured:
                reasons.append(names[1])
            else:
                warnings.append(names[0])
            return
        compare_groups(primary, [parse(raw) for _, raw in text_reads], keys, warnings, reasons)

    def read_front(self, card: np.ndarray) -> FrontRead:
        warnings: List[str] = []
        reasons: List[str] = []
        first = as_dict(self.caller(card, FRONT_PROMPT))
        if str(first.get("side", "front")).lower() != "front":
            warnings.append("side_mismatch")
        id_reads: Labelled = [("full_read", first)] + self.labelled_reads(
            card, self.cfg.front_zoom_regions, FRONT_ZOOM_PROMPT, "zoom_read", warnings
        )
        text_reads = self.labelled_reads(
            card, self.cfg.front_text_regions, FRONT_TEXT_PROMPT, "text_read", warnings
        )
        birth_date, conflict = vote_value([normalize_date(raw.get("birth_date")) for _, raw in id_reads])
        if conflict:
            warnings.append("birth_date_reads_disagree")
            reasons.append("birth_date_reads_disagree")
        serial, serial_conflict = vote_value([normalize_serial(raw.get("serial")) for _, raw in id_reads])
        if serial_conflict:
            warnings.append("serial_reads_disagree")
            reasons.append("serial_reads_disagree")
        warnings += id_read_notes(id_reads)
        primary = text_fields(first)
        self.cross_check(
            primary,
            text_reads,
            ("name", "address"),
            bool(self.cfg.front_text_regions),
            ("text_not_cross_checked", "text_cross_check_failed"),
            text_fields,
            warnings,
            reasons,
        )
        lines = raw_lines(first.get("lines"))
        warnings += fields_missing_from_lines(first, lines, FRONT_LINE_FIELDS, "text_lines_missing")
        warnings += arabic_line_notes(lines, "front")
        address_lines = [
            item
            for item in (normalize_text(first.get("address_line1")), normalize_text(first.get("address_line2")))
            if item
        ]
        return FrontRead(
            first_name=normalize_text(first.get("first_name")),
            other_names=normalize_text(first.get("other_names")),
            address=primary["address"],
            birth_date=birth_date,
            national_id=vote_id([normalize_id(raw.get("national_id")) for _, raw in id_reads]),
            address_lines=address_lines,
            serial=serial,
            lines=lines,
            unreadable=unreadable_fields(first),
            warnings=warnings,
            review_reasons=reasons,
        )

    def read_back(self, card: np.ndarray) -> BackRead:
        warnings: List[str] = []
        reasons: List[str] = []
        sensitive = self.cfg.collect_sensitive_fields
        keys = BACK_BASE_KEYS + (BACK_SENSITIVE_KEYS if sensitive else ())
        first = as_dict(self.caller(card, BACK_PROMPT))
        if str(first.get("side", "back")).lower() != "back":
            warnings.append("side_mismatch")
        id_reads: Labelled = [("full_read", first)] + self.labelled_reads(
            card, self.cfg.back_zoom_regions, BACK_ZOOM_PROMPT, "zoom_read", warnings
        )
        text_reads = self.labelled_reads(
            card, self.cfg.back_text_regions, BACK_TEXT_PROMPT, "text_read", warnings
        )
        every_read = id_reads + text_reads
        issue, issue_conflict = vote_value([normalize_year_month(raw.get("issue_date")) for _, raw in every_read])
        expiry, expiry_conflict = vote_value([normalize_date(raw.get("expiry_date")) for _, raw in every_read])
        gender, gender_conflict = vote_value([parse_gender(raw.get("gender")) for _, raw in every_read])
        for conflict, name in (
            (issue_conflict, "issue_date"),
            (expiry_conflict, "expiry_date"),
            (gender_conflict, "gender"),
        ):
            if conflict:
                warnings.append(f"{name}_reads_disagree")
                reasons.append(f"{name}_reads_disagree")
        warnings += id_read_notes(id_reads)
        if first.get("gender") and gender is None and not gender_conflict:
            warnings.append("gender_text_unrecognized")
        primary = back_text_fields(first, keys)
        self.cross_check(
            primary,
            text_reads,
            keys,
            bool(self.cfg.back_text_regions),
            ("back_text_not_cross_checked", "back_text_cross_check_failed"),
            lambda raw: back_text_fields(raw, keys),
            warnings,
            reasons,
        )
        lines: List[str] = []
        if sensitive:
            lines = raw_lines(first.get("lines"))
            warnings += fields_missing_from_lines(first, lines, keys, "back_text_lines_missing")
            warnings += arabic_line_notes(lines, "back")
        return BackRead(
            national_id=vote_id([normalize_id(raw.get("national_id")) for _, raw in id_reads]),
            gender=gender,
            issue_year_month=issue,
            expiry_date=expiry,
            job=primary["job"],
            religion=primary.get("religion"),
            marital_status=primary.get("marital_status"),
            lines=lines,
            unreadable=unreadable_fields(first),
            warnings=warnings,
            review_reasons=reasons,
        )