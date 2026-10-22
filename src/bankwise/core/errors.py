"""Typed application errors with stable codes and safe public messages.

`str(error)` and `to_public()` only ever contain the fixed public message. Diagnostic text goes in `detail`,
which is for internal logs (and is passed through PII redaction by `internal_summary`)."""
from __future__ import annotations

from enum import Enum
from typing import Dict, Optional

from bankwise.core.pii import redact_text


class ErrorCode(str, Enum):
    CONFIG_INVALID = "config_invalid"
    VALIDATION_FAILED = "validation_failed"
    PERMISSION_DENIED = "permission_denied"
    RATE_LIMITED = "rate_limited"
    INTERNAL_ERROR = "internal_error"


class BankWiseError(Exception):
    code = ErrorCode.INTERNAL_ERROR
    public_message = "An internal error occurred."

    def __init__(self, detail: str = "") -> None:
        super().__init__(self.public_message)
        self._detail = detail

    @property
    def detail(self) -> str:
        return self._detail

    def to_public(self) -> Dict[str, str]:
        return {"code": self.code.value, "message": self.public_message}

    def internal_summary(self) -> Dict[str, str]:
        return {"code": self.code.value, "type": type(self).__name__, "detail": redact_text(self._detail)}


class ConfigError(BankWiseError):
    code = ErrorCode.CONFIG_INVALID
    public_message = "The application configuration is invalid."


class InvalidInputError(BankWiseError):
    code = ErrorCode.VALIDATION_FAILED
    public_message = "The request is invalid."


class PermissionDenied(BankWiseError):
    code = ErrorCode.PERMISSION_DENIED
    public_message = "You do not have permission to perform this action."


class RateLimitExceeded(BankWiseError):
    code = ErrorCode.RATE_LIMITED
    public_message = "Too many requests. Please try again later."

    def __init__(self, detail: str = "", retry_after_seconds: Optional[float] = None) -> None:
        super().__init__(detail)
        self.retry_after_seconds = retry_after_seconds

    def to_public(self) -> Dict[str, object]:
        data: Dict[str, object] = dict(super().to_public())
        if self.retry_after_seconds is not None:
            data["retry_after_seconds"] = int(self.retry_after_seconds) + 1
        return data


def to_public_error(error: BaseException) -> Dict[str, object]:
    """Public representation of ANY exception: unexpected ones are mapped to a generic internal error,
    never exposing their message, type, traceback or paths."""
    if isinstance(error, BankWiseError):
        return dict(error.to_public())
    return {"code": ErrorCode.INTERNAL_ERROR.value, "message": BankWiseError.public_message}
