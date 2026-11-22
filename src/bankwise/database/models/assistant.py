"""Durable assistant state and isolated demo banking tables (amounts in piastres)."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, JSON, String, LargeBinary, UniqueConstraint, FetchedValue, func, text
from sqlalchemy.orm import Mapped, mapped_column

from bankwise.core.identifiers import new_opaque_uuid
from bankwise.database.models import Base


class DemoCustomer(Base):
    __tablename__ = "demo_customers"
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class DemoAccount(Base):
    __tablename__ = "demo_accounts"
    __table_args__ = (CheckConstraint("balance >= 0", name="ck_demo_balance"),
                      CheckConstraint("status IN ('open', 'closed')", name="ck_demo_account_status"),
                      CheckConstraint("account_number ~ '^[0-9]{12}$'", name="ck_demo_account_number"),
                      UniqueConstraint("account_number", name="uq_demo_account_number"))
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_opaque_uuid)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    account_number: Mapped[str] = mapped_column(String(12), nullable=False,
        server_default=text("nextval('demo_account_number_seq')::text"))
    balance: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(16), default="open")


class DemoTransaction(Base):
    __tablename__ = "demo_transactions"
    __table_args__ = (CheckConstraint("amount > 0", name="ck_demo_tx_amount"),
                      UniqueConstraint('reference', name='uq_demo_transaction_reference'),
                      CheckConstraint("reference ~ '^BW-[0-9]{8}-[0-9]{6}$'", name='ck_demo_transaction_reference'))
    reference: Mapped[str] = mapped_column(String(24), nullable=False, server_default=FetchedValue())
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_opaque_uuid)
    operation_id: Mapped[UUID] = mapped_column(unique=True)
    source_id: Mapped[UUID | None] = mapped_column(ForeignKey("demo_accounts.id"))
    destination_id: Mapped[UUID] = mapped_column(ForeignKey("demo_accounts.id"))
    amount: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DemoLoan(Base):
    __tablename__ = "demo_loans"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_opaque_uuid)
    operation_id: Mapped[UUID] = mapped_column(unique=True)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    details: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="pending_review")


class AssistantConversation(Base):
    __tablename__ = "assistant_conversations"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_opaque_uuid)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AssistantOperation(Base):
    __tablename__ = "assistant_operations"
    __table_args__ = (CheckConstraint("attempts BETWEEN 0 AND 5", name="ck_assistant_attempts"),
                      CheckConstraint("status IN ('pending', 'completed', 'cancelled', 'locked', 'expired')",
                                      name="ck_assistant_operation_status"))
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_opaque_uuid)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("assistant_conversations.id"), index=True)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    tool: Mapped[str] = mapped_column(String(40))
    arguments: Mapped[dict] = mapped_column(JSON)
    otp_hash: Mapped[str] = mapped_column(String(512))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    result: Mapped[dict | None] = mapped_column(JSON)


class AssistantAudit(Base):
    __tablename__ = "assistant_audit"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_opaque_uuid)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("assistant_conversations.id"), index=True)
    event: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdentityReview(Base):
    __tablename__ = 'identity_reviews'
    __table_args__ = (CheckConstraint("status IN ('pending','approved','rejected')", name='ck_review_status'),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_opaque_uuid)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey('users.id'), index=True)
    status: Mapped[str] = mapped_column(String(16), default='pending')
    encrypted_payload: Mapped[bytes | None] = mapped_column(LargeBinary)
    reason: Mapped[str | None] = mapped_column(String(40))
    reviewer_id: Mapped[UUID | None] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
