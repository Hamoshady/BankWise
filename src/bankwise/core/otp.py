"""One-time password primitives for the two-factor login flow.

An OTP is a short-lived, single-purpose secret. Only a protected digest is ever persisted; the plaintext code
exists in memory just long enough to be handed to the SMS provider. Argon2id is reused from the project's
password layer (no new dependency) so a stolen database does not reveal the codes.

These are primitives, not a challenge store: see ``bankwise.database.otp.OtpChallengeStore`` for expiry,
attempt counting, single use and concurrency control.
"""
from __future__ import annotations

import re
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from bankwise.core.errors import InvalidInputError

OTP_DIGITS = 6
OTP_TTL_SECONDS = 300              # 5 minutes, as required by the login policy
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_COOLDOWN_SECONDS = 60
_CODE_RE = re.compile(r"^[0-9]{6}$")
_hasher = PasswordHasher()


def generate_otp() -> str:
    """A cryptographically secure, uniformly random 6-digit code (leading zeroes included)."""
    return f"{secrets.randbelow(10 ** OTP_DIGITS):0{OTP_DIGITS}d}"


def hash_otp(code: str) -> str:
    """Return a salted Argon2id digest. Never store or log the plaintext `code`."""
    if not isinstance(code, str) or not _CODE_RE.match(code):
        raise InvalidInputError("otp must be six digits")
    return _hasher.hash(code)


def verify_otp(digest: str, code: str) -> bool:
    """Constant-work verification. Bad inputs, malformed digests and wrong codes all return False."""
    if not isinstance(digest, str) or not isinstance(code, str) or not _CODE_RE.match(code):
        return False
    try:
        return bool(_hasher.verify(digest, code))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
