"""Explicit external DTOs; no automatic ORM serialization."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from bankwise.core.errors import InvalidInputError
from bankwise.database.accounts import normalize_username
from bankwise.integration.identity_processing import NOTICE


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=256)

    @field_validator("username")
    @classmethod
    def username_normalized(cls, value):
        try:
            return normalize_username(value)
        except InvalidInputError:
            raise ValueError("invalid username") from None


class TokenResponse(BaseModel):
    kyc_pending: bool = False
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class OtpChallengeResponse(BaseModel):
    """Step 1 result: an opaque challenge id and timing only. No token, phone number or code is returned."""

    challenge_id: UUID
    expires_in: int
    resend_after_seconds: int
    development_otp: bool = False


class OtpVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge_id: UUID
    otp: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")


class OtpResendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge_id: UUID


class IdentityResponse(BaseModel):
    request_id: UUID
    status: Literal["processing", "completed", "failed", "interrupted"]
    decision: Literal["VERIFIED", "REJECTED", "RETRY", "MANUAL_REVIEW"] | None
    error_code: Literal["validation_failed", "rate_limited", "internal_error"] | None
    created_at: datetime
    completed_at: datetime | None
    expires_at: datetime
    notice: str = NOTICE

    @classmethod
    def from_record(cls, row):
        return cls(request_id=row.id, status=row.status, decision=row.decision, error_code=row.error_code,
                   created_at=row.created_at, completed_at=row.completed_at, expires_at=row.expires_at)


class ErrorBody(BaseModel):
    code: str
    message: str
    reason: str | None = None          # OTP challenge reason only; never account information
    retry_after_seconds: int | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
    request_id: str


class HealthResponse(BaseModel):
    status: Literal["live", "ready"]
