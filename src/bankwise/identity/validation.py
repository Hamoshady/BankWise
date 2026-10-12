from datetime import date
from typing import List, Optional

from bankwise.identity.national_id import parse_national_id
from bankwise.identity.reader.base import BackRead, FrontRead, IdVote
from bankwise.identity.schemas import ConsistencyCheck, NationalIdInfo


def build_info(vote: IdVote, today: date) -> Optional[NationalIdInfo]:
    if vote.ambiguous:
        return NationalIdInfo(
            format_valid=False, issues=["ambiguous_id_candidates"], ambiguous=True
        )
    if vote.value is None:
        return None
    return parse_national_id(vote.value, today)


def side_checks(prefix: str, vote: IdVote, info: Optional[NationalIdInfo], min_agreeing: int) -> List[ConsistencyCheck]:
    if info is None:
        return []
    checks = [
        ConsistencyCheck(
            name=f"{prefix}_id_format_valid",
            passed=info.format_valid,
            detail=",".join(info.issues),
        ),
        ConsistencyCheck(name=f"{prefix}_id_read_unambiguous", passed=not info.ambiguous),
    ]
    if info.format_valid and not info.ambiguous:
        required = min(min_agreeing, vote.reads)
        checks.append(
            ConsistencyCheck(
                name=f"{prefix}_id_reads_agree",
                passed=vote.support >= required,
                detail=f"{vote.support}/{vote.reads}",
            )
        )
    return checks


def build_checks(
    front: FrontRead,
    back: BackRead,
    front_info: Optional[NationalIdInfo],
    back_info: Optional[NationalIdInfo],
    today: date,
    min_agreeing_reads: int,
) -> List[ConsistencyCheck]:
    checks = side_checks("front", front.national_id, front_info, min_agreeing_reads)
    checks += side_checks("back", back.national_id, back_info, min_agreeing_reads)
    if front_info is not None and back_info is not None:
        if front_info.ambiguous or back_info.ambiguous:
            checks.append(
                ConsistencyCheck(name="front_back_id_match", passed=False, detail="ambiguous_read")
            )
        elif front.national_id.value and back.national_id.value:
            checks.append(
                ConsistencyCheck(
                    name="front_back_id_match",
                    passed=front.national_id.value == back.national_id.value,
                )
            )
    primary = next(
        (item for item in (front_info, back_info) if item is not None and item.format_valid),
        None,
    )
    if primary is not None and primary.gender is not None and back.gender is not None:
        checks.append(
            ConsistencyCheck(name="gender_matches_id", passed=back.gender == primary.gender)
        )
    if primary is not None and primary.birth_date is not None and front.birth_date is not None:
        checks.append(
            ConsistencyCheck(
                name="printed_birth_date_matches_id",
                passed=front.birth_date == primary.birth_date,
            )
        )
    if back.expiry_date is not None:
        checks.append(
            ConsistencyCheck(
                name="card_not_expired",
                passed=back.expiry_date >= today,
                detail=back.expiry_date.isoformat(),
            )
        )
        if back.issue_year_month is not None:
            checks.append(
                ConsistencyCheck(
                    name="expiry_after_issue",
                    passed=back.expiry_date.strftime("%Y-%m") > back.issue_year_month,
                )
            )
    return checks