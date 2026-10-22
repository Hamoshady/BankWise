"""Minimal audit events.

An `AuditEvent` is validated so it cannot carry personal data: metadata keys must not be sensitive names,
string values must be short printable ASCII (this excludes Arabic names and addresses) and must not contain
ID-number, token or secret patterns. These are guards, not guarantees: callers still must not pass personal data.

Sinks here are `InMemoryAuditSink` (bounded, lost on exit; development and tests) and `LoggingAuditSink`
(writes to the structured log). Neither is durable or tamper-resistant. A persistent, append-only store is
future work behind the same `AuditSink` interface."""
from __future__ import annotations

import logging
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Deque, Dict, List, Mapping, Optional, Protocol, Union

from bankwise.core.errors import ErrorCode, InvalidInputError
from bankwise.core.logging import get_correlation_id, get_logger, log_event
from bankwise.core.pii import is_sensitive_key, redact, redact_text

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
