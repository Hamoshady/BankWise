"""PII and secret redaction.

Two mechanisms, with different guarantees:

* Key-based: any value stored under a sensitive key (national_id, first_name, address, ...) is
  replaced wholesale. This is what protects names and addresses, which cannot be recognised by pattern.
* Pattern-based (`redact_text`): national-ID-like digit runs, JWTs, bearer tokens, API-key shapes,
  `password=...` assignments and registered secret values are masked inside free text.
  Free-text Arabic names and addresses are NOT detected by pattern: never put raw OCR text in a log message.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Set

REDACTED = "[REDACTED]"
REDACTED_ID = "[REDACTED_ID]"
MAX_DEPTH = 20
MIN_REGISTERED_SECRET_LENGTH = 8

_SENSITIVE_EXACT = frozenset({
    "national_id", "nid", "id_number", "id_no",
    "name", "first_name", "last_name", "middle_name", "full_name", "father_name", "mother_name",
    "other_names", "cardholder_name", "holder_name", "customer_name", "applicant_name",
    "address", "birth_date", "date_of_birth", "dob", "decoded",
    "text", "raw_text", "ocr_text", "text_rows", "text_rows_raw", "lines", "raw_lines",
    "raw_reads", "candidates", "image", "image_bytes", "card_image",
    # fields of the OCR result schema (id_ocr public JSON)
    "all_text_lines", "job", "serial", "gender", "religion", "marital_status",
})
_SENSITIVE_SUBSTRINGS = (
    "password", "passwd", "secret", "token", "api_key", "apikey", "authorization", "credential",
    "private_key", "cookie", "hmac", "national_id", "birth", "ocr_text", "raw_text",
)

_NID_SEPARATED = re.compile(r"(?<!\d)(?:\d[ \-\u00a0]?){13}\d(?!\d)")
_LONG_DIGITS = re.compile(r"\d{11,}")
_MASKED_ID = re.compile(r"\d?[*xX\u2022]{6,}\d{0,4}")        # an already-masked id such as 2***********67 still narrows it down
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_KEY_SHAPES = re.compile(r"AIza[0-9A-Za-z_-]{30,}|\bsk-[A-Za-z0-9_-]{16,}|\bgh[pousr]_[A-Za-z0-9]{20,}")
_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization)\b(\s*[=:]\s*)"
    r"(?:(?:Bearer|Basic)\s+)?(\"[^\"]*\"|'[^']*'|\S+)"
)

_registered_secrets: Set[str] = set()


class Sensitive:
    """Wrapper that never shows its value in str/repr/format/pickle. Raw access is the explicit `reveal()`;
    callers that must expose a value to a user go through `security.reveal_sensitive`, which authorizes and audits."""

    __slots__ = ("_value",)

    def __init__(self, value: Any) -> None:
        self._value = value

    def reveal(self) -> Any:
        return self._value

    def __repr__(self) -> str:
        return f"Sensitive({REDACTED})"

    def __str__(self) -> str:
        return REDACTED

    def __format__(self, spec: str) -> str:
        return REDACTED

    def __reduce__(self):
        raise TypeError("Sensitive values cannot be pickled")

    def __copy__(self) -> "Sensitive":
        return self

    def __deepcopy__(self, memo) -> "Sensitive":
        return self


def register_secret_value(value: str) -> None:
    """Make `redact_text` mask this exact value wherever it appears (called when a secret is loaded)."""
    if isinstance(value, str) and len(value) >= MIN_REGISTERED_SECRET_LENGTH:
        _registered_secrets.add(value)


def clear_registered_secrets() -> None:
    _registered_secrets.clear()


def is_sensitive_key(key: object) -> bool:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(key))          # NationalID -> National_ID
    normalized = re.sub(r"[^a-z0-9]+", "_", spaced.lower()).strip("_")
    if normalized in _SENSITIVE_EXACT:
        return True
    if normalized.startswith("address") or normalized.endswith("_address"):
        return True
    return any(part in normalized for part in _SENSITIVE_SUBSTRINGS)


def redact_text(text: str) -> str:
    if not isinstance(text, str):
        return text
    for secret in sorted(_registered_secrets, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    text = _ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    text = _JWT.sub(REDACTED, text)
    text = _BEARER.sub(f"Bearer {REDACTED}", text)
    text = _KEY_SHAPES.sub(REDACTED, text)
    text = _NID_SEPARATED.sub(REDACTED_ID, text)
    text = _MASKED_ID.sub(REDACTED_ID, text)
    return _LONG_DIGITS.sub(REDACTED_ID, text)


def redact(value: Any, _depth: int = 0) -> Any:
    """Return a JSON-safe copy of `value` that is safe to log. Unknown objects become '<TypeName>' so that
    images, arrays and domain objects are never dumped through their repr."""
    if isinstance(value, Sensitive):
        return REDACTED
    if value is None or isinstance(value, (bool, float)):
        return value
    if isinstance(value, int):
        return REDACTED_ID if len(str(abs(value))) >= 11 else value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(value)} bytes>"
    if _depth >= MAX_DEPTH:
        return "[MAX_DEPTH]"
    if isinstance(value, Mapping):
        return {
            redact_text(str(key)): (REDACTED if is_sensitive_key(key) else redact(item, _depth + 1))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact(item, _depth + 1) for item in value]
    return f"<{type(value).__name__}>"
