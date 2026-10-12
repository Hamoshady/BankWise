from datetime import date
from typing import List, Optional, Tuple

from bankwise.core.config import IdentityConfig
from bankwise.core.pii import mask_sensitive_line
from bankwise.identity.national_id import calculate_age
from bankwise.identity.reader.base import FrontRead, IdFieldReader
from bankwise.identity.schemas import IdReport, NationalIdInfo, SideStatus
from bankwise.identity.validation import build_checks, build_info
from bankwise.identity.vision.processor import process_card
from bankwise.identity.vision.schemas import CardSide, VisionStatus


def side_status(output) -> SideStatus:
    return SideStatus(status=output.result.status, reasons=list(output.result.reasons))


def resolve_birth(
    front_info: Optional[NationalIdInfo],
    back_info: Optional[NationalIdInfo],
    front: FrontRead,
    today: date,
) -> Tuple[Optional[date], Optional[int], Optional[str]]:
    for info in (front_info, back_info):
        if info is not None and info.format_valid and not info.ambiguous and info.birth_date:
            return info.birth_date, info.age_years, "national_id"
    if front.birth_date is not None:
        return front.birth_date, calculate_age(front.birth_date, today), "printed_date"
    return None, None, None


def verify_identity_fields(
    front_bytes: bytes,
    back_bytes: bytes,
    reader: IdFieldReader,
    cfg: IdentityConfig,
    today: Optional[date] = None,
) -> IdReport:
    current = today or date.today()
    front_out = process_card(front_bytes, CardSide.FRONT, cfg.vision)
    back_out = process_card(back_bytes, CardSide.BACK, cfg.vision)
    report = IdReport(front_image=side_status(front_out), back_image=side_status(back_out))
    if front_out.result.status != VisionStatus.OK or back_out.result.status != VisionStatus.OK:
        return report
    front = reader.read_front(front_out.card_image)
    back = reader.read_back(back_out.card_image)
    front_info = build_info(front.national_id, current)
    back_info = build_info(back.national_id, current)
    full_name = " ".join(part for part in (front.first_name, front.other_names) if part) or None
    birth_date, age, birth_source = resolve_birth(front_info, back_info, front, current)
    sensitive = cfg.reader.collect_sensitive_fields
    required = (
        ("front_id", front_info),
        ("full_name", full_name),
        ("address", front.address),
        ("birth_date", birth_date),
        ("gender", back.gender),
        ("expiry_date", back.expiry_date),
    )
    optional = [("job", back.job), ("serial", front.serial)]
    if sensitive:
        optional += [("religion", back.religion), ("marital_status", back.marital_status)]
    warnings = front.warnings + back.warnings
    warnings += [f"unreadable:{item}" for item in front.unreadable + back.unreadable]
    if birth_source == "printed_date":
        warnings.append("birth_date_from_printed_date_only")
    checks = build_checks(
        front, back, front_info, back_info, current, cfg.reader.min_agreeing_reads
    )
    reasons = list(dict.fromkeys(front.review_reasons + back.review_reasons))
    reasons += [f"check_failed:{check.name}" for check in checks if not check.passed]
    report.reader_called = True
    report.reader = getattr(reader, "name", type(reader).__name__)
    report.front_id = front_info
    report.back_id = back_info
    report.first_name = front.first_name
    report.other_names = front.other_names
    report.full_name = full_name
    report.address_lines = front.address_lines
    report.address = front.address
    report.birth_date = birth_date
    report.birth_date_source = birth_source
    report.age_years = age
    report.gender = back.gender
    report.religion = back.religion
    report.job = back.job
    report.marital_status = back.marital_status
    report.serial = front.serial
    report.issue_year_month = back.issue_year_month
    report.expiry_date = back.expiry_date
    report.front_text_lines = [mask_sensitive_line(line) for line in front.lines]
    report.back_text_lines = [mask_sensitive_line(line) for line in back.lines]
    report.checks = checks
    report.warnings = warnings
    report.missing_fields = [name for name, value in required if value is None]
    report.unread_optional_fields = [name for name, value in optional if value is None]
    report.review_reasons = reasons
    report.needs_review = bool(reasons)
    return report