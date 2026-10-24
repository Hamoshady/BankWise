"""Authentication: issue and verify signed access tokens (JWT, HS256), with revocation.

Authentication answers "who is calling?" and produces a `Principal`. It does NOT decide what the caller may do:
that is `security.Authorizer`. Verification is strict:

* only HS256 is accepted (algorithm pinned; 'none' and every other algorithm are rejected);
* iss, aud, sub, jti, iat, nbf, exp are all required, `typ` must be "access", lifetimes are capped;
* time checks use the service clock (one time source, testable), with a small configured leeway;
* revoked token ids (jti) are refused.

ROLE AND REVOCATION MODEL (explicit): roles are carried inside the token, so a role change does NOT apply to tokens
already issued. When a user's roles change, or an account is suspended or suspected stolen, call
`TokenService.revoke_subject(subject_id)`: every token issued to that subject up to that moment is refused and the
user must log in again to get a token with the new roles. A single token is revoked with `revoke(token)` (logout).
Otherwise a token is valid until it expires (keep `access_token_ttl_seconds` short).

Limits you must know: `InMemoryRevocationStore` is per process and lost on restart (revoked tokens become valid
again until they expire); use a shared store when there is more than one process. There is no refresh-token flow,
user store or login endpoint yet."""
from __future__ import annotations

import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Protocol

import jwt

from bankwise.core.errors import AuthenticationError, BankWiseError, ConfigError, InvalidInputError
from bankwise.core.pii import Sensitive
from bankwise.core.security import Principal

MIN_SECRET_CHARS = 32            # an HS256 key should carry at least 256 bits
ALGORITHM = "HS256"
TOKEN_TYPE = "access"
MAX_TOKEN_CHARS = 4096
REQUIRED_CLAIMS = ["exp", "iat", "nbf", "sub", "jti", "iss", "aud"]
_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_JTI_RE = re.compile(r"^[0-9a-f]{32}$")


@dataclass(frozen=True)
class AuthSettings:
    issuer: str
    audience: str
    access_token_ttl_seconds: int = 900
    leeway_seconds: int = 10
    algorithm: str = ALGORITHM

    def __post_init__(self) -> None:
        for label, value in (("issuer", self.issuer), ("audience", self.audience)):
            if not isinstance(value, str) or not _NAME_RE.match(value):
                raise ConfigError(f"auth {label} must be a short plain identifier")
        if self.algorithm != ALGORITHM:
            raise ConfigError(f"auth algorithm must be {ALGORITHM}")
        for label, value, low, high in (("access_token_ttl_seconds", self.access_token_ttl_seconds, 60, 86400),
                                        ("leeway_seconds", self.leeway_seconds, 0, 60)):
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ConfigError(f"auth {label} must be an integer between {low} and {high}")


class RevocationStore(Protocol):
    def revoke(self, jti: str, expires_at: int, now: int) -> None:
        ...

    def is_revoked(self, jti: str, now: int) -> bool:
        ...

    def revoke_subject(self, subject_id: str, cutoff: int, keep_until: int, now: int) -> None:
        ...

    def is_subject_revoked(self, subject_id: str, issued_at: int, now: int) -> bool:
        ...


class InMemoryRevocationStore:
    """Revoked token ids until they would have expired anyway. Per process, lost on restart, bounded: when it is
    full of unexpired entries `revoke` raises instead of silently forgetting a revocation."""

    def __init__(self, max_entries: int = 10000) -> None:
        self._entries: Dict[str, int] = {}
        self._subjects: Dict[str, "tuple[int, int]"] = {}          # subject -> (cutoff, remember until)
        self._max_entries = max_entries
        self._lock = threading.Lock()

    def revoke(self, jti: str, expires_at: int, now: int) -> None:
        with self._lock:
            self._purge(now)
            if jti not in self._entries and len(self._entries) >= self._max_entries:
                raise BankWiseError("revocation store is full")
            self._entries[jti] = expires_at

    def is_revoked(self, jti: str, now: int) -> bool:
        with self._lock:
            self._purge(now)
            return jti in self._entries

    def revoke_subject(self, subject_id: str, cutoff: int, keep_until: int, now: int) -> None:
        """Refuse every token of `subject_id` issued at or before `cutoff`. The rule is remembered until
        `keep_until` (by then every older token has expired anyway)."""
        with self._lock:
            self._purge(now)
            if subject_id not in self._subjects and len(self._subjects) >= self._max_entries:
                raise BankWiseError("revocation store is full")
            previous = self._subjects.get(subject_id)
            if previous is None or cutoff >= previous[0]:
                self._subjects[subject_id] = (cutoff, max(keep_until, previous[1] if previous else 0))

    def is_subject_revoked(self, subject_id: str, issued_at: int, now: int) -> bool:
        with self._lock:
            self._purge(now)
            rule = self._subjects.get(subject_id)
            return rule is not None and issued_at <= rule[0]

    def _purge(self, now: int) -> None:
        for jti in [key for key, expires in self._entries.items() if expires <= now]:
            del self._entries[jti]
        for subject in [key for key, (_, until) in self._subjects.items() if until <= now]:
            del self._subjects[subject]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _fail(reason: str) -> AuthenticationError:
    return AuthenticationError(f"token rejected: {reason}", reason)


class TokenService:
    def __init__(self, settings: AuthSettings, secret: Sensitive, revocations: Optional[RevocationStore] = None,
                 clock: Callable[[], datetime] = _utc_now) -> None:
        if not isinstance(secret, Sensitive) or not isinstance(secret.reveal(), str) or not secret.reveal():
            raise ConfigError("the token signing secret must be a non-empty Sensitive string (use config.get_secret)")
        if len(secret.reveal()) < MIN_SECRET_CHARS:
            raise ConfigError(f"the token signing secret must be at least {MIN_SECRET_CHARS} characters "
                              "(generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\")")
        self._settings = settings
        self._secret = secret
        self._revocations: RevocationStore = revocations if revocations is not None else InMemoryRevocationStore()
        self._clock = clock

    def _now(self) -> int:
        return int(self._clock().timestamp())

    def issue(self, principal: Principal) -> str:
        """Sign an access token for an ALREADY-AUTHENTICATED principal. Call this only after real credentials
        (for example `passwords.verify_password`) have been checked."""
        if not isinstance(principal, Principal):
            raise InvalidInputError("issue() needs a Principal")
        now = self._now()
        claims = {
            "iss": self._settings.issuer, "aud": self._settings.audience, "sub": principal.subject_id,
            "roles": sorted(principal.roles), "typ": TOKEN_TYPE, "jti": uuid.uuid4().hex,
            "iat": now, "nbf": now, "exp": now + self._settings.access_token_ttl_seconds,
        }
        return jwt.encode(claims, self._secret.reveal(), algorithm=ALGORITHM)

    def authenticate(self, token: str) -> Principal:
        claims = self._verified_claims(token)
        return Principal(claims["sub"], frozenset(claims["roles"]))

    def revoke(self, token: str) -> str:
        """Revoke a currently valid token until its natural expiry. Returns its jti (an opaque random id)."""
        claims = self._verified_claims(token)
        self._revocations.revoke(claims["jti"], claims["exp"], self._now())
        return claims["jti"]

    def revoke_subject(self, subject_id: str) -> None:
        """Invalidate every token issued to `subject_id` so far (role change, suspension, suspected theft).
        Tokens issued AFTER this call are unaffected, so the user can log in again. Call it from code that has
        already authorized the action (see `SecurityService.revoke_subject`)."""
        Principal(subject_id, frozenset())                          # validates the identifier
        now = self._now()
        self._revocations.revoke_subject(subject_id, now, now + self._settings.access_token_ttl_seconds
                                         + self._settings.leeway_seconds, now)

    # ------------------------------------------------------------------ internals
    def _verified_claims(self, token: object) -> dict:
        if not isinstance(token, str) or not token:
            raise _fail("missing")
        if len(token) > MAX_TOKEN_CHARS:
            raise _fail("token_too_large")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise _fail("malformed") from None
        if header.get("alg") != ALGORITHM:
            raise _fail("wrong_algorithm")
        try:
            claims = jwt.decode(
                token, self._secret.reveal(), algorithms=[ALGORITHM], audience=self._settings.audience,
                issuer=self._settings.issuer,
                options={"require": REQUIRED_CLAIMS, "verify_exp": False, "verify_nbf": False, "verify_iat": False},
            )
        except jwt.InvalidSignatureError:
            raise _fail("invalid_signature") from None
        except jwt.InvalidAudienceError:
            raise _fail("wrong_audience") from None
        except jwt.InvalidIssuerError:
            raise _fail("wrong_issuer") from None
        except jwt.MissingRequiredClaimError:
            raise _fail("invalid_claims") from None
        except jwt.DecodeError:
            raise _fail("malformed") from None
        except jwt.InvalidTokenError:
            raise _fail("invalid_claims") from None
        self._check_claims(claims)
        return claims

    def _check_claims(self, claims: dict) -> None:
        # Exact issuer/audience comparison of our own, independent of the library version
        # (PyJWT 2.10.0 accepted partial issuer matches: CVE-2024-53861).
        if claims["iss"] != self._settings.issuer:
            raise _fail("wrong_issuer")
        audience = claims["aud"]
        if audience != self._settings.audience and not (
                isinstance(audience, list) and self._settings.audience in audience):
            raise _fail("wrong_audience")
        exp, iat, nbf = claims["exp"], claims["iat"], claims["nbf"]
        if not (_is_int(exp) and _is_int(iat) and _is_int(nbf)):
            raise _fail("invalid_claims")
        now, leeway = self._now(), self._settings.leeway_seconds
        if exp <= now - leeway:
            raise _fail("expired")
        if nbf > now + leeway or iat > now + leeway:
            raise _fail("not_yet_valid")
        if exp - iat > self._settings.access_token_ttl_seconds or exp <= iat:
            raise _fail("invalid_claims")
        roles: List[object] = claims.get("roles")  # type: ignore[assignment]
        if claims.get("typ") != TOKEN_TYPE or not isinstance(roles, list) \
                or not all(isinstance(role, str) and _ROLE_RE.match(role) for role in roles):
            raise _fail("invalid_claims")
        if not isinstance(claims["jti"], str) or not _JTI_RE.match(claims["jti"]):
            raise _fail("invalid_claims")
        try:
            Principal(claims["sub"], frozenset(roles))
        except InvalidInputError:
            raise _fail("invalid_claims") from None
        if self._revocations.is_revoked(claims["jti"], now) \
                or self._revocations.is_subject_revoked(claims["sub"], iat, now):
            raise _fail("revoked")
