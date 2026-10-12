from datetime import date
from typing import Optional

from bankwise.core.pii import mask_national_id
from bankwise.identity.normalize import digits_only
from bankwise.identity.schemas import Gender, NationalIdInfo

ID_LENGTH = 14
CENTURY_BASE = {"2": 1900, "3": 2000}
MAX_AGE_YEARS = 120
WARNING_AGE_YEARS = 100

GOVERNORATES = {
    "01": "Cairo",
    "02": "Alexandria",
    "03": "Port Said",
    "04": "Suez",
    "11": "Damietta",
    "12": "Dakahlia",
    "13": "Sharqia",
    "14": "Qalyubia",
    "15": "Kafr El-Sheikh",
    "16": "Gharbia",
    "17": "Monufia",
    "18": "Beheira",
    "19": "Ismailia",
    "21": "Giza",
    "22": "Beni Suef",
    "23": "Fayoum",
    "24": "Minya",
    "25": "Assiut",
    "26": "Sohag",
    "27": "Qena",
    "28": "Aswan",
    "29": "Luxor",
    "31": "Red Sea",
    "32": "New Valley",
    "33": "Matrouh",
    "34": "North Sinai",
    "35": "South Sinai",
    "88": "Born outside Egypt",
}

GOVERNORATE_CODES = frozenset(GOVERNORATES)


def calculate_age(born: date, today: date) -> int:
    years = today.year - born.year
    if (today.month, today.day) < (born.month, born.day):
        years -= 1
    return years


def parse_national_id(raw: str, today: Optional[date] = None) -> NationalIdInfo:
    current = today or date.today()
    digits = digits_only(raw)
    masked = mask_national_id(digits)

    if len(digits) != ID_LENGTH:
        return NationalIdInfo(
            format_valid=False,
            issues=["invalid_length"],
            masked=masked,
        )

    issues = []
    warnings = []

    base_year = CENTURY_BASE.get(digits[0])
    if base_year is None:
        issues.append("invalid_century_digit")

    birth_date = None
    age = None

    if base_year is not None:
        try:
            birth_date = date(
                base_year + int(digits[1:3]),
                int(digits[3:5]),
                int(digits[5:7]),
            )
        except ValueError:
            issues.append("invalid_birth_date")

    if birth_date is not None:
        if birth_date > current:
            issues.append("birth_date_in_future")
            birth_date = None
        else:
            age = calculate_age(birth_date, current)
            if age > MAX_AGE_YEARS:
                issues.append("implausible_age")
            elif age > WARNING_AGE_YEARS:
                warnings.append("age_over_100")

    governorate = digits[7:9]
    if governorate not in GOVERNORATE_CODES:
        warnings.append("unknown_governorate_code")

    gender = Gender.MALE if int(digits[12]) % 2 == 1 else Gender.FEMALE

    return NationalIdInfo(
        format_valid=not issues,
        birth_date=birth_date,
        age_years=age,
        gender=gender,
        governorate_code=governorate,
        governorate_name=GOVERNORATES.get(governorate),
        issues=issues,
        warnings=warnings,
        masked=masked,
    )