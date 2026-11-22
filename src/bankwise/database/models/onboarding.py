"""Encrypted application drafts, signup-only OTP challenges and demo deposit receipts."""
from datetime import datetime
from uuid import UUID
from sqlalchemy import String, ForeignKey, DateTime, LargeBinary, CheckConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from bankwise.database.models import Base
from bankwise.core.identifiers import new_opaque_uuid

class OnboardingApplication(Base):
    __tablename__ = 'onboarding_applications'
    __table_args__ = (CheckConstraint("status IN ('draft','pending_review','needs_information','rejected','active','cancelled','expired')", name='ck_onboarding_status'),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_opaque_uuid)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey('users.id'), unique=True)
    phone_digest: Mapped[str] = mapped_column(String(64), unique=True)
    identity_digest: Mapped[str | None] = mapped_column(String(64), unique=True)
    session_digest: Mapped[str | None] = mapped_column(String(64))
    session_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    phone_verified: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(24), default='draft')
    encrypted_data: Mapped[bytes | None] = mapped_column(LargeBinary)
    reason: Mapped[str | None] = mapped_column(String(40))
    reviewer_id: Mapped[UUID | None] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class SignupChallenge(Base):
    __tablename__ = 'signup_challenges'
    __table_args__ = (CheckConstraint('attempts BETWEEN 0 AND 5', name='ck_signup_attempts'),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_opaque_uuid)
    application_id: Mapped[UUID] = mapped_column(ForeignKey('onboarding_applications.id', ondelete='CASCADE'), index=True)
    otp_hash: Mapped[str] = mapped_column(String(512))
    attempts: Mapped[int] = mapped_column(default=0)
    used: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

class DemoDeposit(Base):
    __tablename__ = 'demo_deposits'
    id: Mapped[UUID] = mapped_column(primary_key=True)  # client idempotency key
    transaction_id: Mapped[UUID] = mapped_column(ForeignKey('demo_transactions.id'), unique=True)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey('users.id'))
    note: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
