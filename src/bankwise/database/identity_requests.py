"""Short committed reservations prevent retries from invoking OCR twice.

No transaction/connection is held during OCR. The file audit and PostgreSQL are separate
stores: an ambiguous commit or process interruption requires operator reconciliation,
never automatic reprocessing. Expired keys remain reserved until explicit retention cleanup.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from bankwise.core.errors import BankWiseError, Conflict, NotFound
from bankwise.database.models import IdentityRequest

DECISIONS = frozenset({"VERIFIED", "REJECTED", "RETRY", "MANUAL_REVIEW"})


class RequestReplay(Exception):
    def __init__(self, record):
        super().__init__("existing request")
        self.record = record


def owned_request(session, request_id, principal):
    row = session.scalar(select(IdentityRequest).where(
        IdentityRequest.id == request_id, IdentityRequest.owner_id == UUID(principal.subject_id),
        IdentityRequest.expires_at > datetime.now(timezone.utc)))
    if row is None:
        raise NotFound()
    return row


class RequestPersistence:
    def __init__(self, session, idempotency_key, retention_days):
        self.session, self.key, self.retention_days = session, idempotency_key, retention_days
        self.request_id = None
        self.record = None

    def started(self, principal, request_id):
        owner = UUID(principal.subject_id)
        now = datetime.now(timezone.utc)
        request_uuid = UUID(request_id)
        with self.session.begin():
            created = self.session.scalar(insert(IdentityRequest).values(
                id=request_uuid, owner_id=owner, idempotency_key=self.key, status="processing",
                created_at=now, expires_at=now + timedelta(days=self.retention_days)
            ).on_conflict_do_nothing(constraint="uq_identity_owner_key").returning(IdentityRequest.id))
            if created is None:
                row = self.session.scalar(select(IdentityRequest).where(
                    IdentityRequest.owner_id == owner, IdentityRequest.idempotency_key == self.key))
                if row is None or row.expires_at <= now:
                    raise Conflict()
                raise RequestReplay(row)
        self.request_id = request_uuid

    def succeeded(self, summary):
        decision = summary.get("decision")
        status = decision.get("status") if isinstance(decision, dict) else None
        if status not in DECISIONS:
            raise BankWiseError("OCR summary has no recognized decision")
        with self.session.begin():
            row = self.session.get(IdentityRequest, self.request_id)
            if row is None or row.status != "processing":
                raise BankWiseError("identity request is no longer processing")
            row.status, row.decision = "completed", status
            row.completed_at = datetime.now(timezone.utc)
        self.record = row

    def failed(self, error):
        self.session.rollback()
        code = error.code.value if isinstance(error, BankWiseError) else "internal_error"
        if code not in {"validation_failed", "rate_limited", "internal_error"}:
            code = "internal_error"
        with self.session.begin():
            self.session.execute(update(IdentityRequest).where(
                IdentityRequest.id == self.request_id, IdentityRequest.status == "processing"
            ).values(status="failed", error_code=code, completed_at=datetime.now(timezone.utc)))
