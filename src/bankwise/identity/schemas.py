from datetime import date
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

from bankwise.core.pii import mask_serial
from bankwise.identity.vision.schemas import VisionStatus

REDACTED = "<redacted>"
REDACTED_TEXT_FIELDS = ("first_name", "other_names", "religion", "job", "marital_status")


class Gender(str, Enum):
    MALE = "male"
    FEMALE = "female"


class NationalIdInfo(BaseModel):
    format_valid: bool
    birth_date: Optional[date] = None
    age_years: Optional[int] = None
    gender: Optional[Gender] = None
    governorate_code: Optional[str] = None
    governorate_name: Optional[str] = None
    issues: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    masked: str = ""
    ambiguous: bool = False


class ConsistencyCheck(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class SideStatus(BaseModel):
    status: VisionStatus
    reasons: List[str] = Field(default_factory=list)


class IdReport(BaseModel):
    front_image: SideStatus
    back_image: SideStatus
    reader_called: bool = False
    reader: str = ""
    front_id: Optional[NationalIdInfo] = None
    back_id: Optional[NationalIdInfo] = None
    first_name: Optional[str] = None
    other_names: Optional[str] = None
    full_name: Optional[str] = None
    address_lines: List[str] = Field(default_factory=list)
    address: Optional[str] = None
    birth_date: Optional[date] = None
    birth_date_source: Optional[str] = None
    age_years: Optional[int] = None
    gender: Optional[Gender] = None
    religion: Optional[str] = None
    job: Optional[str] = None
    marital_status: Optional[str] = None
    serial: Optional[str] = None
    issue_year_month: Optional[str] = None
    expiry_date: Optional[date] = None
    front_text_lines: List[str] = Field(default_factory=list)
    back_text_lines: List[str] = Field(default_factory=list)
    checks: List[ConsistencyCheck] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    missing_fields: List[str] = Field(default_factory=list)
    unread_optional_fields: List[str] = Field(default_factory=list)
    needs_review: bool = False
    review_reasons: List[str] = Field(default_factory=list)

    def redacted(self) -> dict:
        data = self.model_dump(mode="json")
        for key in ("full_name", "address"):
            if data.get(key):
                data[key] = f"{REDACTED}:{len(data[key].split())}_words"
        for key in REDACTED_TEXT_FIELDS:
            if data.get(key):
                data[key] = REDACTED
        for key in ("address_lines", "front_text_lines", "back_text_lines"):
            if data.get(key):
                data[key] = f"{REDACTED}:{len(data[key])}_lines"
        if data.get("serial"):
            data["serial"] = mask_serial(data["serial"])
        if data.get("birth_date"):
            data["birth_date"] = REDACTED
        for key in ("front_id", "back_id"):
            info = data.get(key)
            if info and info.get("birth_date"):
                info["birth_date"] = REDACTED
        return data