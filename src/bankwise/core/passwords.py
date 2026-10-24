"""Password hashing with Argon2id (argon2-cffi). Plaintext passwords are never stored, logged or returned.

This is a primitive, not a user-management system: there is no user table, reset flow or lockout policy here.
A future login handler calls `verify_password_or_dummy`, then asks the token service to issue a token.
Passwords are NFKC-normalised before hashing so visually identical inputs verify identically."""
from __future__ import annotations

import secrets
import unicodedata
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from bankwise.core.errors import InvalidInputError

MIN_PASSWORD_CHARS = 12
MAX_PASSWORD_CHARS = 256
_hasher = PasswordHasher()
_dummy_hash: Optional[str] = None


def _normalise(plain: str) -> str:
    return unicodedata.normalize("NFKC", plain)


def hash_password(plain: str) -> str:
    if not isinstance(plain, str) or not MIN_PASSWORD_CHARS <= len(plain) <= MAX_PASSWORD_CHARS:
        raise InvalidInputError(f"password must be {MIN_PASSWORD_CHARS}-{MAX_PASSWORD_CHARS} characters")
    return _hasher.hash(_normalise(plain))


def verify_password(stored_hash: str, plain: str) -> bool:
    """True only for a matching password. Wrong password, malformed hash, wrong types and oversized input all
    return False; nothing about the failure is distinguishable by the caller."""
    if not isinstance(stored_hash, str) or not isinstance(plain, str) or len(plain) > MAX_PASSWORD_CHARS:
        return False
    try:
        return bool(_hasher.verify(stored_hash, _normalise(plain)))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(stored_hash: str) -> bool:
    """True when the stored hash uses weaker parameters than the current defaults (rehash after a good login)."""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except (InvalidHashError, VerificationError, TypeError):
        return True


def verify_password_or_dummy(stored_hash: Optional[str], plain: str) -> bool:
    """Like `verify_password`, but when the user does not exist (`stored_hash is None`) it still performs a
    full Argon2 verification against a throwaway hash, so response time does not reveal whether the account exists."""
    global _dummy_hash
    if stored_hash is None:
        if _dummy_hash is None:
            _dummy_hash = _hasher.hash(secrets.token_urlsafe(24))
        verify_password(_dummy_hash, plain)
        return False
    return verify_password(stored_hash, plain)
