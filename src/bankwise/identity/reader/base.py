from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional, Protocol

import numpy as np

from bankwise.identity.schemas import Gender


@dataclass(frozen=True)
class IdVote:
    value: Optional[str]
    ambiguous: bool
    support: int
    reads: int


@dataclass
class FrontRead:
    first_name: Optional[str]
    other_names: Optional[str]
    address: Optional[str]
    birth_date: Optional[date]
    national_id: IdVote
    address_lines: List[str] = field(default_factory=list)
    serial: Optional[str] = None
    lines: List[str] = field(default_factory=list)
    unreadable: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    review_reasons: List[str] = field(default_factory=list)


@dataclass
class BackRead:
    national_id: IdVote
    gender: Optional[Gender]
    issue_year_month: Optional[str]
    expiry_date: Optional[date]
    job: Optional[str] = None
    religion: Optional[str] = None
    marital_status: Optional[str] = None
    lines: List[str] = field(default_factory=list)
    unreadable: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    review_reasons: List[str] = field(default_factory=list)


class IdFieldReader(Protocol):
    name: str

    def read_front(self, card: np.ndarray) -> FrontRead:
        ...

    def read_back(self, card: np.ndarray) -> BackRead:
        ...