import hashlib
import hmac
import os

from bankwise.core.errors import ConfigError

PII_KEY_ENV = "BANKWISE_PII_HMAC_KEY"


def mask_national_id(value: str) -> str:
    digits = "".join(character for character in value if character.isdigit())
    if len(digits) < 4:
        return "*" * len(digits)
    return digits[0] + "*" * (len(digits) - 3) + digits[-2:]


def fingerprint(value: str) -> str:
    key = os.environ.get(PII_KEY_ENV)
    if not key:
        raise ConfigError(f"missing environment variable {PII_KEY_ENV}")
    return hmac.new(key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()

MIN_DIGITS_FOR_SENSITIVE_LINE = 11
def mask_sensitive_line(text: str) -> str:
    digit_count = sum(1 for character in text if character.isdigit())
    if digit_count >= MIN_DIGITS_FOR_SENSITIVE_LINE:
        return mask_national_id(text)
    return text
def mask_serial(value: str) -> str:
    if len(value) < 5:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]