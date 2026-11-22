"""Random UUIDs compatible with the existing conservative audit PII detector."""
from uuid import UUID, uuid4

from bankwise.core.pii import redact_text


def new_opaque_uuid() -> UUID:
    """Resample rare digit-heavy UUIDs; never exempt UUID-shaped text from PII protection."""
    while True:
        value = uuid4()
        if redact_text(str(value)) == str(value):
            return value
