"""Minimal audit events.

An `AuditEvent` is validated so it cannot carry personal data: metadata keys must not be sensitive names,
string values must be short printable ASCII (this excludes Arabic names and addresses) and must not contain
ID-number, token or secret patterns. These are guards, not guarantees: callers still must not pass personal data.

Sinks: `InMemoryAuditSink` (bounded, lost on exit; development and tests), `LoggingAuditSink` (operational log
only: NOT an audit record, it can be rotated, filtered or lost) and `HashChainedFileAuditSink` (durable local file,
tamper-EVIDENT; see its docstring for exactly what that does and does not give you). A database-backed append-only
store is future work behind the same `AuditSink` interface."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from enum import Enum
from types import MappingProxyType
from typing import Any, Deque, Dict, List, Mapping, Optional, Protocol, Union

from bankwise.core.errors import BankWiseError, ConfigError, ErrorCode, InvalidInputError
from bankwise.core.logging import get_correlation_id, get_logger, log_event
from bankwise.core.pii import Sensitive, is_sensitive_key, redact, redact_text

MAX_METADATA_ITEMS = 20
MAX_STRING_CHARS = 200
_EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
_ACTOR_RE = re.compile(r"^[A-Za-z0-9_.:@-]{1,64}$")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_CORRELATION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
Scalar = Union[str, int, float, bool, None]


class AuditOutcome(str, Enum):
    SUCCESS = "success"
    DENIED = "denied"
    FAILURE = "failure"


def _reject(message: str) -> InvalidInputError:
    return InvalidInputError(f"audit event rejected: {message}")


@dataclass(frozen=True)
class AuditEvent:
    event_type: str
    outcome: AuditOutcome
    actor_id: Optional[str] = None
    error_code: Optional[str] = None
    metadata: Mapping[str, Scalar] = field(default_factory=dict)
    correlation_id: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, str) or not _EVENT_TYPE_RE.match(self.event_type):
            raise _reject("event_type must look like 'domain.action'")
        if not isinstance(self.outcome, AuditOutcome):
            raise _reject("outcome must be an AuditOutcome")
        if self.actor_id is not None and (
            not isinstance(self.actor_id, str) or not _ACTOR_RE.match(self.actor_id)
            or redact_text(self.actor_id) != self.actor_id
        ):
            raise _reject("actor_id must be a short plain identifier")
        if self.error_code is not None and self.error_code not in {code.value for code in ErrorCode}:
            raise _reject("error_code must be a known ErrorCode value")
        object.__setattr__(self, "metadata", MappingProxyType(self._validated_metadata(self.metadata)))
        correlation = self.correlation_id if self.correlation_id is not None else get_correlation_id()
        if correlation is not None and not _CORRELATION_RE.match(correlation):
            raise _reject("correlation_id must be a short plain token")
        object.__setattr__(self, "correlation_id", correlation)

    @staticmethod
    def _validated_metadata(metadata: Mapping[str, Scalar]) -> Dict[str, Scalar]:
        if not isinstance(metadata, Mapping) or len(metadata) > MAX_METADATA_ITEMS:
            raise _reject(f"metadata must be a mapping with at most {MAX_METADATA_ITEMS} items")
        clean: Dict[str, Scalar] = {}
        for key, value in metadata.items():
            if not isinstance(key, str) or not _KEY_RE.match(key) or is_sensitive_key(key):
                raise _reject("metadata key is not allowed")
            if isinstance(value, str):
                if len(value) > MAX_STRING_CHARS or not value.isascii() or not value.isprintable() \
                        or redact_text(value) != value:
                    raise _reject(f"metadata value for '{key}' is not safe")
            elif isinstance(value, (bool, float)) or value is None:
                pass
            elif isinstance(value, int):
                if redact(value) != value:
                    raise _reject(f"metadata value for '{key}' looks like an identifier")
            else:
                raise _reject(f"metadata value for '{key}' must be a scalar")
            clean[key] = value
        return clean

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type,
            "outcome": self.outcome.value,
            "timestamp": self.timestamp.astimezone(timezone.utc).isoformat(),
            "actor_id": self.actor_id,
            "correlation_id": self.correlation_id,
            "error_code": self.error_code,
            "metadata": dict(self.metadata),
        }


class AuditSink(Protocol):
    def write(self, event: AuditEvent) -> None:
        ...


class InMemoryAuditSink:
    """Bounded in-process list. NOT durable: contents are lost when the process exits."""

    def __init__(self, max_events: int = 1000) -> None:
        self._events: Deque[AuditEvent] = deque(maxlen=max_events)

    def write(self, event: AuditEvent) -> None:
        self._events.append(event)

    @property
    def events(self) -> List[AuditEvent]:
        return list(self._events)


class LoggingAuditSink:
    """Emits each event to the structured application log. Only as durable and trustworthy as the log handler."""

    def __init__(self) -> None:
        self._logger = get_logger("audit")

    def write(self, event: AuditEvent) -> None:
        log_event(self._logger, logging.INFO, "audit_event", **event.to_dict())


# ------------------------------------------------------------------ durable, tamper-evident file sink
GENESIS = "0" * 64
MIN_AUDIT_KEY_CHARS = 32


@dataclass(frozen=True)
class AuditSettings:
    base_dir: str = "outputs/audit"
    filename: str = "audit.jsonl"
    max_bytes: int = 50 * 1024 * 1024

    def __post_init__(self) -> None:
        if not isinstance(self.base_dir, str) or not self.base_dir.strip() or "\x00" in self.base_dir:
            raise ConfigError("audit base_dir must be a non-empty string")
        if not isinstance(self.filename, str) or not re.match(r"^[A-Za-z0-9_.-]{1,64}$", self.filename) \
                or self.filename.startswith("."):
            raise ConfigError("audit filename must be a plain file name")
        if isinstance(self.max_bytes, bool) or not isinstance(self.max_bytes, int) \
                or not 1024 <= self.max_bytes <= 1024 ** 3:
            raise ConfigError("audit max_bytes must be an integer between 1024 and 1073741824")


@dataclass(frozen=True)
class AuditVerification:
    ok: bool
    records: int
    last_mac: Optional[str]
    error: Optional[str] = None


def _canonical(seq: int, event: Dict[str, Any], prev: str) -> bytes:
    return json.dumps({"seq": seq, "event": event, "prev": prev}, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("ascii")


def _mac(key: bytes, seq: int, event: Dict[str, Any], prev: str) -> str:
    return hmac.new(key, _canonical(seq, event, prev), hashlib.sha256).hexdigest()


def _key_bytes(key: Sensitive) -> bytes:
    value = key.reveal() if isinstance(key, Sensitive) else None
    if not isinstance(value, str) or len(value) < MIN_AUDIT_KEY_CHARS:
        raise ConfigError(f"the audit HMAC key must be a Sensitive string of at least {MIN_AUDIT_KEY_CHARS} characters")
    return value.encode("utf-8")


def verify_audit_file(path: Path, key: Sensitive) -> AuditVerification:
    """Check every record's HMAC and the chain (sequence numbers, links). Detects edited, inserted, reordered or
    deleted-in-the-middle records and records forged without the key. It CANNOT detect removal of the last few
    records (truncation) or a rewrite by someone who holds the key: keep `last_mac` and the record count somewhere
    the log writer cannot modify (another host, a ticket, a signed report) and compare periodically."""
    secret = _key_bytes(key)
    prev, count = GENESIS, 0
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return AuditVerification(True, 0, None)
    if raw and not raw.endswith(b"\n"):
        return AuditVerification(False, count, None, "incomplete last record")
    for line in raw.splitlines():
        try:
            record = json.loads(line)
            seq, event, claimed_prev, claimed_mac = record["seq"], record["event"], record["prev"], record["mac"]
            if not isinstance(event, dict) or not isinstance(claimed_mac, str):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return AuditVerification(False, count, prev if count else None, f"record {count + 1} is not valid")
        if seq != count + 1 or claimed_prev != prev:
            return AuditVerification(False, count, prev if count else None, f"chain broken at record {count + 1}")
        if not hmac.compare_digest(claimed_mac, _mac(secret, seq, event, prev)):
            return AuditVerification(False, count, prev if count else None, f"bad MAC at record {count + 1}")
        prev, count = claimed_mac, count + 1
    return AuditVerification(True, count, prev if count else None)


class HashChainedFileAuditSink:
    """Append-only JSON Lines audit file where every record carries an HMAC-SHA256 over (sequence, event,
    previous MAC). Durable: each record is flushed and fsynced before `write` returns. Tamper-EVIDENT, not
    tamper-PROOF: see `verify_audit_file` for what is and is not detectable. The file is created owner-only and
    never follows a symbolic link. The existing file is verified when the sink starts and startup FAILS if it does
    not verify. When `max_bytes` is reached `write` raises (nothing is rotated or dropped silently). Single
    process only: concurrent writers in several processes would corrupt the chain."""

    def __init__(self, path: Path, key: Sensitive, max_bytes: int = 50 * 1024 * 1024) -> None:
        self._key = _key_bytes(key)
        self._key_wrapper = key
        self._path = Path(path)
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.is_symlink():
            raise ConfigError("the audit file must not be a symbolic link")
        check = verify_audit_file(self._path, key)
        if not check.ok:
            raise BankWiseError(f"audit file failed verification: {check.error}")
        self._seq = check.records
        self._prev = check.last_mac or GENESIS
        self._size = self._path.stat().st_size if self._path.exists() else 0

    def write(self, event: AuditEvent) -> None:
        with self._lock:
            seq = self._seq + 1
            payload = event.to_dict()
            mac = _mac(self._key, seq, payload, self._prev)
            line = (json.dumps({"seq": seq, "event": payload, "prev": self._prev, "mac": mac},
                               sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
            if self._size + len(line) > self._max_bytes:
                raise BankWiseError("audit file is full")
            flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            descriptor = os.open(self._path, flags, 0o600)
            try:
                os.write(descriptor, line)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._seq, self._prev, self._size = seq, mac, self._size + len(line)

    def verify(self) -> AuditVerification:
        return verify_audit_file(self._path, self._key_wrapper)
