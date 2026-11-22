"""SecurityService: the single place an API layer calls to authenticate, rate-limit, authorize and audit.

It is framework-agnostic (plain strings in, `Principal` or a typed error out). The API and
identity service share this instance; no router implements competing security gates.
Order for `guard()`:

    client lockout check -> authenticate -> per-user rate limit -> authorize -> return Principal

* Failed authentications are charged to the CLIENT key (`auth_attempt` policy); once that budget is spent the
  client is refused (even with a valid token) until the window passes. Successful requests are not charged.
* `client_key` is chosen by the caller (for example 'ip:203.0.113.7'). Do not derive it from X-Forwarded-For
  unless a trusted proxy sets that header.
* Audit write failures are logged and never turn a denial into a success (or an error page into a leak)."""
from __future__ import annotations

import logging
import hashlib
from typing import Callable, Optional

from bankwise.core.audit import AuditEvent, AuditOutcome, AuditSink
from bankwise.core.auth import TokenService
from bankwise.core.errors import AuthenticationError, BankWiseError, ErrorCode, RateLimitExceeded
from bankwise.core.rate_limit import RateLimiterBackend, normalise_key
from bankwise.core.security import Authorizer, Principal

_log = logging.getLogger("bankwise.security")
AUTH_POLICY = "auth_attempt"
MAX_HEADER_CHARS = 8192
SUBJECT_ADMIN_PERMISSION = "admin:manage"


def parse_bearer(authorization_header: Optional[str]) -> str:
    """Extract the token from an `Authorization: Bearer <token>` header value, or raise AuthenticationError."""
    if not isinstance(authorization_header, str) or not authorization_header.strip():
        raise AuthenticationError("no authorization header", "missing")
    if len(authorization_header) > MAX_HEADER_CHARS:
        raise AuthenticationError("authorization header too large", "malformed")
    parts = authorization_header.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthenticationError("authorization header is not a bearer token", "malformed")
    return parts[1]


class SecurityService:
    def __init__(self, tokens: TokenService, authorizer: Authorizer, limiter: RateLimiterBackend,
                 sink: AuditSink, auth_policy: str = AUTH_POLICY,
                 principal_validator: Optional[Callable[[Principal], None]] = None) -> None:
        self._tokens = tokens
        self._authorizer = authorizer
        self._limiter = limiter
        self._sink = sink
        self._auth_policy = auth_policy
        self._principal_validator = principal_validator
        self.audit_failures = 0            # operators should alert when this grows

    # ------------------------------------------------------------------ public API
    def limit_public_attempt(self, client_key: str) -> None:
        """Bound unauthenticated registration before password hashing."""
        self._limiter.enforce(self._auth_policy, 'signup:' + client_key)

    def authenticate(self, authorization_header: Optional[str], client_key: str) -> Principal:
        state = self._limiter.peek(self._auth_policy, client_key)
        if not state.allowed:
            self._audit("auth.locked_out", AuditOutcome.DENIED, error_code=ErrorCode.RATE_LIMITED.value)
            raise RateLimitExceeded("client locked out after repeated authentication failures",
                                    state.retry_after_seconds)
        try:
            principal = self._tokens.authenticate(parse_bearer(authorization_header))
            if self._principal_validator is not None:
                self._principal_validator(principal)
            return principal
        except AuthenticationError as error:
            self._limiter.acquire(self._auth_policy, client_key)
            self._audit("auth.failed", AuditOutcome.FAILURE, error_code=error.code.value,
                        metadata={"reason": error.reason})
            raise

    def guard(self, authorization_header: Optional[str], permission: str, client_key: str,
              rate_policy: Optional[str] = None) -> Principal:
        principal = self.authenticate(authorization_header, client_key)
        if rate_policy is not None:
            try:
                self._limiter.enforce(rate_policy, principal.subject_id)
            except RateLimitExceeded:
                self._audit("rate_limit.exceeded", AuditOutcome.DENIED, actor_id=principal.subject_id,
                            error_code=ErrorCode.RATE_LIMITED.value, metadata={"policy": rate_policy})
                raise
        self._authorizer.require(principal, permission, self._sink)
        return principal

    def issue_token(self, principal: Principal) -> str:
        """For login, AFTER credentials were verified. Audited, and fail-closed: if the audit
        record cannot be written the token is NOT returned (issuance without a record would break the guarantee)."""
        token = self._tokens.issue(principal)
        self._audit("auth.token_issued", AuditOutcome.SUCCESS, strict=True, actor_id=principal.subject_id)
        return token

    def login(self, username: str, password: str, client_key: str,
              verifier: Callable[[str, str], Optional[Principal]]) -> Principal:
        """Step 1 of two-factor login: verify credentials and return the identity, but ISSUE NO TOKEN.

        A fully privileged token is created only by `issue_token` after OTP verification. Both budgets precede
        Argon2 work: login attempts (including successes) use the same client budget as failed bearer
        authentication, and the account budget uses an opaque digest, never the username in logs/audit metadata.
        """
        username = normalise_key(username)
        account_key = "login:" + hashlib.sha256(username.encode("utf-8")).hexdigest()
        for key in (client_key, account_key):
            try:
                self._limiter.enforce(self._auth_policy, key)
            except RateLimitExceeded:
                self._audit("auth.locked_out", AuditOutcome.DENIED, error_code=ErrorCode.RATE_LIMITED.value)
                raise
        principal = verifier(username, password)
        if principal is None:
            self._audit("auth.failed", AuditOutcome.FAILURE, error_code=ErrorCode.AUTHENTICATION_FAILED.value,
                        metadata={"reason": "invalid_credentials"})
            raise AuthenticationError()
        return principal

    def limit_otp(self, client_key: str, subject_id: str, policy: str) -> None:
        """Rate-limit OTP generation/resend by caller and by account (opaque digest, never a phone number)."""
        account_key = "otp:" + hashlib.sha256(normalise_key(subject_id).encode("utf-8")).hexdigest()
        for key in (client_key, account_key):
            try:
                self._limiter.enforce(policy, key)
            except RateLimitExceeded:
                self._audit("rate_limit.exceeded", AuditOutcome.DENIED, actor_id=subject_id,
                            error_code=ErrorCode.RATE_LIMITED.value, metadata={"policy": policy})
                raise

    def limit_otp_challenge(self, client_key: str, challenge_id: str, policy: str) -> None:
        """Bound OTP verification across challenges from one client and for one specific challenge."""
        challenge_key = "challenge:" + hashlib.sha256(normalise_key(challenge_id).encode("utf-8")).hexdigest()
        for key in (client_key, challenge_key):
            try:
                self._limiter.enforce(policy, key)
            except RateLimitExceeded:
                self._audit("rate_limit.exceeded", AuditOutcome.DENIED,
                            error_code=ErrorCode.RATE_LIMITED.value, metadata={"policy": policy})
                raise

    def revoke_subject(self, actor: Optional[Principal], subject_id: str) -> None:
        """Invalidate every token of `subject_id` (role change, suspension, suspected theft). Needs
        `admin:manage`. The revocation takes effect even if the audit write fails (that failure is counted and logged)."""
        self._authorizer.require(actor, SUBJECT_ADMIN_PERMISSION, self._sink)
        self._tokens.revoke_subject(subject_id)
        self._audit("auth.subject_revoked", AuditOutcome.SUCCESS, actor_id=actor.subject_id,
                    metadata={"target": subject_id})

    def revoke(self, authorization_header: Optional[str]) -> None:
        """Log out: revoke the presented token. Audited."""
        token = parse_bearer(authorization_header)
        principal = self._tokens.authenticate(token)
        self._tokens.revoke(token)
        self._audit("auth.token_revoked", AuditOutcome.SUCCESS, actor_id=principal.subject_id)

    def record_access(self, event_type: str, subject_id: str) -> None:
        """Audit a protected metadata disclosure before releasing it (same fail-closed policy)."""
        self._audit(event_type, AuditOutcome.SUCCESS, strict=True, actor_id=subject_id)

    def record_denial(self, event_type: str, subject_id: str) -> None:
        """A denial remains effective even when its audit write fails; failures are counted/logged."""
        self._audit(event_type, AuditOutcome.DENIED, actor_id=subject_id,
                    error_code=ErrorCode.PERMISSION_DENIED.value)

    # ------------------------------------------------------------------ internals
    def _audit(self, event_type: str, outcome: AuditOutcome, strict: bool = False, **fields) -> None:
        """Write an audit record. Denials and revocations are already effective when this runs, so a failed write
        is counted and logged (never swallowed silently). Operations that GRANT something use strict=True."""
        try:
            self._sink.write(AuditEvent(event_type, outcome, **fields))
        except Exception as error:                      # any sink failure; programming errors still show in logs
            self.audit_failures += 1
            _log.error("audit write failed for %s (%s)", event_type, type(error).__name__)
            if strict:
                raise BankWiseError(f"audit write failed for {event_type}") from None
