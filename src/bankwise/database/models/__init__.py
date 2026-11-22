"""Minimal account and identity-attempt metadata. Never store OCR payloads here."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from bankwise.core.identifiers import new_opaque_uuid


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("username ~ '^[a-z0-9][a-z0-9_.-]{2,63}$'", name="ck_users_username"),
        CheckConstraint("password_hash LIKE '$argon2id$%'", name="ck_users_password_hash"),
        Index("uq_users_phone_e164", "phone_e164", unique=True),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_opaque_uuid)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    # OTP destination. Set by an operator after out-of-band verification; login never accepts a phone number.
    phone_e164: Mapped[str | None] = mapped_column(String(20))
    phone_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    roles: Mapped[list[UserRole]] = relationship(back_populates="user", cascade="all, delete-orphan", lazy="selectin")


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (CheckConstraint("role ~ '^[a-z][a-z0-9_]{0,31}$'", name="ck_user_roles_role"),)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), primary_key=True)
    user: Mapped[User] = relationship(back_populates="roles")


class IdentityRequest(Base):
    __tablename__ = "identity_requests"
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_identity_owner_key"),
        CheckConstraint("status IN ('processing', 'completed', 'failed', 'interrupted')", name="ck_identity_status"),
        CheckConstraint("decision IN ('VERIFIED', 'REJECTED', 'RETRY', 'MANUAL_REVIEW')", name="ck_identity_decision"),
        CheckConstraint("error_code IN ('validation_failed', 'rate_limited', 'internal_error')", name="ck_identity_error"),
        CheckConstraint("(status = 'processing' AND completed_at IS NULL AND decision IS NULL AND error_code IS NULL) OR "
                        "(status = 'completed' AND completed_at IS NOT NULL AND decision IS NOT NULL AND error_code IS NULL) OR "
                        "(status IN ('failed', 'interrupted') AND completed_at IS NOT NULL AND decision IS NULL AND error_code IS NOT NULL)",
                        name="ck_identity_result"),
        CheckConstraint("expires_at > created_at AND (completed_at IS NULL OR completed_at >= created_at)", name="ck_identity_dates"),
        Index("ix_identity_owner_created", "owner_id", "created_at"),
        Index("ix_identity_expires", "expires_at"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_opaque_uuid)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    idempotency_key: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    decision: Mapped[str | None] = mapped_column(String(16))
    error_code: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

class OtpChallenge(Base):
    """One 2FA login attempt. The code is only ever stored as an Argon2id digest in `otp_hash`."""
    __tablename__ = "otp_challenges"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'consumed', 'superseded', 'expired', 'blocked')",
                        name="ck_otp_challenge_status"),
        CheckConstraint("attempts BETWEEN 0 AND 5", name="ck_otp_challenge_attempts"),
        CheckConstraint("expires_at > created_at AND (consumed_at IS NULL OR consumed_at >= created_at)",
                        name="ck_otp_challenge_dates"),
        Index("ix_otp_challenge_user_status", "user_id", "status"),
        Index("ix_otp_challenge_flow", "flow_id"),
        Index("ix_otp_challenge_expires", "expires_at"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_opaque_uuid)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # Groups challenges created while resending within the same login attempt.
    flow_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    phone_e164: Mapped[str] = mapped_column(String(20), nullable=False)
    otp_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# Register knowledge tables in the same metadata used by Alembic.
from bankwise.database.models.rag import RagDocument, RagChunk  # noqa: E402,F401
from bankwise.database.models.assistant import (DemoCustomer, DemoAccount, DemoTransaction, DemoLoan,
    AssistantConversation, AssistantOperation, AssistantAudit, IdentityReview)  # noqa: E402,F401
# Register historical Core tables for schema tooling and Alembic metadata comparisons.
from bankwise.database import financial_schema  # noqa: E402,F401
from bankwise.database import graph_schema  # noqa: E402,F401
from bankwise.database.models.onboarding import OnboardingApplication, SignupChallenge, DemoDeposit  # noqa: E402,F401
