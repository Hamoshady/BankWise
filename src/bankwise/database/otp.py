"""Persistence for 2FA login challenges.

Every method uses the request's scoped session and commits explicitly. Verification takes a
``SELECT ... FOR UPDATE`` lock so two concurrent requests can never consume the same challenge; status changes
(including attempt counting and expiry) are committed before the error is raised to the caller. The plaintext
code is never read from here. On any raised error the surrounding ``Database.session()`` rolls the session back.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from bankwise.core.errors import OtpError, RateLimitExceeded
from bankwise.core.identifiers import new_opaque_uuid
from bankwise.core.otp import OTP_MAX_ATTEMPTS, OTP_RESEND_COOLDOWN_SECONDS, OTP_TTL_SECONDS, verify_otp
from bankwise.core.security import Principal
from bankwise.database.models import OtpChallenge, User


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class OtpChallengeView:
    """Public, non-sensitive view of a newly created challenge (no code, no phone number)."""

    challenge_id: UUID
    flow_id: UUID
    expires_in: int
    resend_after_seconds: int


@dataclass(frozen=True)
class OtpResult:
    token: str
    subject_id: str


class OtpChallengeStore:
    def __init__(self, session: Session, clock: Callable[[], datetime] = _utc_now) -> None:
        self._session = session
        self._clock = clock

    def start(self, user_id: UUID, phone_e164: str, digest: str, *, now: Optional[datetime] = None) -> OtpChallengeView:
        """Create a challenge for a new login attempt, retiring any still-pending challenge for this user."""
        now = now or self._clock()
        self._session.execute(update(OtpChallenge).where(
            OtpChallenge.user_id == user_id, OtpChallenge.status == "pending"
        ).values(status="superseded").execution_options(synchronize_session=False))
        row = OtpChallenge(user_id=user_id, flow_id=new_opaque_uuid(), phone_e164=phone_e164,
                           otp_hash=digest, status="pending", attempts=0, created_at=now,
                           expires_at=now + timedelta(seconds=OTP_TTL_SECONDS))
        self._session.add(row)
        self._session.flush()
        self._session.commit()
        return OtpChallengeView(row.id, row.flow_id, OTP_TTL_SECONDS, OTP_RESEND_COOLDOWN_SECONDS)

    def owner_of(self, challenge_id: UUID) -> Optional[UUID]:
        """Look up the owner of a challenge for rate-limit keying. Returns nothing for unknown ids."""
        return self._session.scalar(select(OtpChallenge.user_id).where(OtpChallenge.id == challenge_id))

    def resend(self, user_id: UUID, challenge_id: UUID, digest: str, *, now: Optional[datetime] = None) -> OtpChallengeView:
        """Replace the current challenge with a fresh code, enforcing the resend cooldown."""
        now = now or self._clock()
        error: Optional[BaseException] = None
        view: Optional[OtpChallengeView] = None
        row = self._session.scalar(select(OtpChallenge).where(
            OtpChallenge.id == challenge_id, OtpChallenge.user_id == user_id).with_for_update())
        if row is None or row.status != "pending":
            error = OtpError(reason="invalid_challenge")
        else:
            remaining = OTP_RESEND_COOLDOWN_SECONDS - (now - row.created_at).total_seconds()
            if remaining > 0:
                error = RateLimitExceeded("otp resend cooldown", remaining)
            else:
                self._session.execute(update(OtpChallenge).where(
                    OtpChallenge.flow_id == row.flow_id, OtpChallenge.status == "pending"
                ).values(status="superseded").execution_options(synchronize_session=False))
                fresh = OtpChallenge(user_id=user_id, flow_id=row.flow_id, phone_e164=row.phone_e164,
                                     otp_hash=digest, status="pending", attempts=0, created_at=now,
                                     expires_at=now + timedelta(seconds=OTP_TTL_SECONDS))
                self._session.add(fresh)
                self._session.flush()
                view = OtpChallengeView(fresh.id, fresh.flow_id, OTP_TTL_SECONDS, OTP_RESEND_COOLDOWN_SECONDS)
        self._session.commit()
        if error is not None:
            raise error
        return view  # type: ignore[return-value]

    def verify(self, challenge_id: UUID, code: str, issue: Callable[[Principal], str],
               *, now: Optional[datetime] = None, development_mock: bool = False) -> OtpResult:
        """Validate, consume and issue the *full* token atomically. `issue` is fail-closed (audit failures
        raise), so a token can never exist without its audit record, and a challenge can never be reused."""
        now = now or self._clock()
        error: Optional[BaseException] = None
        token: Optional[str] = None
        subject_id: Optional[str] = None
        row = self._session.scalar(select(OtpChallenge).where(
            OtpChallenge.id == challenge_id).with_for_update())
        if row is None:
            error = OtpError(reason="invalid_challenge")
        elif row.status == "consumed":
            error = OtpError(reason="challenge_consumed")
        elif row.status in {"superseded", "expired", "blocked"}:
            error = OtpError(reason=row.status)
        elif row.status != "pending":
            error = OtpError(reason="invalid_challenge")
        elif row.expires_at <= now:
            row.status = "expired"
            error = OtpError(reason="expired")
        elif row.attempts >= OTP_MAX_ATTEMPTS:
            row.status = "blocked"
            error = OtpError(reason="exhausted")
        else:
            user = self._session.get(User, row.user_id)
            if user is None or not user.active or (not development_mock and not user.phone_verified_at) or (user.phone_e164 or "") != row.phone_e164:
                error = OtpError(reason="invalid_challenge")
            elif not verify_otp(row.otp_hash, code):
                row.attempts += 1
                if row.attempts >= OTP_MAX_ATTEMPTS:
                    row.status = "blocked"
                    error = OtpError(reason="exhausted")
                else:
                    error = OtpError(reason="invalid_otp")
            else:
                principal = Principal(str(user.id), frozenset(r.role for r in user.roles))
                token = issue(principal)
                row.status = "consumed"
                row.consumed_at = now
                subject_id = principal.subject_id
        self._session.commit()
        if error is not None:
            raise error
        return OtpResult(token, subject_id)  # type: ignore[arg-type]
