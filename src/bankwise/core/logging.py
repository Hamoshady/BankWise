"""Structured, redacting logging on the standard library `logging` module.

Redaction happens in a filter attached to the handler that `configure_logging` installs, so every record is
redacted before any formatter or output sees it. Handlers that other code attaches to the `bankwise`
logger by hand are NOT covered. Structured JSON is easier to search; it is not tamper-proof."""
from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import re
import sys
import threading
import traceback
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import IO, Any, Dict, Iterator, List, Optional

from bankwise.core.errors import BankWiseError, ConfigError
from bankwise.core.pii import redact, redact_text

LOGGER_NAME = "bankwise"
VALID_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
VALID_FORMATS = ("json", "console")
MAX_FRAMES = 10
_CORRELATION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_correlation_id: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar("bankwise_correlation_id", default=None)


@dataclass(frozen=True)
class LoggingSettings:
    level: str = "INFO"
    format: str = "json"
    max_message_chars: int = 2000

    def __post_init__(self) -> None:
        if self.level not in VALID_LEVELS:
            raise ConfigError(f"logging level must be one of {VALID_LEVELS}")
        if self.format not in VALID_FORMATS:
            raise ConfigError(f"logging format must be one of {VALID_FORMATS}")
        if isinstance(self.max_message_chars, bool) or not isinstance(self.max_message_chars, int) \
                or not 50 <= self.max_message_chars <= 100000:
            raise ConfigError("max_message_chars must be an integer between 50 and 100000")


# ------------------------------------------------------------------ correlation id
def get_correlation_id() -> Optional[str]:
    return _correlation_id.get()


def new_correlation_id() -> str:
    return uuid.uuid4().hex


@contextlib.contextmanager
def correlation_scope(correlation_id: Optional[str] = None) -> Iterator[str]:
    """Bind a correlation id for the current context. A caller-supplied id that is not a short, plain token
    (for example one taken from a request header) is replaced, which prevents log injection."""
    value = correlation_id if isinstance(correlation_id, str) and _CORRELATION_RE.match(correlation_id) \
        else new_correlation_id()
    token = _correlation_id.set(value)
    try:
        yield value
    finally:
        _correlation_id.reset(token)


# ------------------------------------------------------------------ redaction + formatting
def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


class RedactionFilter(logging.Filter):
    def __init__(self, max_message_chars: int = 2000) -> None:
        super().__init__()
        self.max_message_chars = max_message_chars

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, Mapping):
            record.args = redact(args)
        elif isinstance(args, tuple) and args:
            record.args = tuple(redact(item) for item in args)
        try:
            message = record.getMessage()
        except Exception:
            message = "[unformattable log message]"
        record.msg = _truncate(redact_text(message), self.max_message_chars)
        record.args = None
        fields = getattr(record, "fields", None)
        if fields is not None:
            record.fields = redact(fields)
        return True


def describe_exception(exc_info) -> Dict[str, Any]:
    """Type, stable code and redacted detail of an exception, plus 'file.py:line in function' frames
    (base file names only, no absolute paths and no local variable values)."""
    exc_type, exc, tb = exc_info
    info: Dict[str, Any] = {"type": getattr(exc_type, "__name__", "Exception")}
    if isinstance(exc, BankWiseError):
        info["code"] = exc.code.value
        info["detail"] = redact_text(exc.detail)
    elif exc is not None:
        info["message"] = redact_text(str(exc))[:300]
    frames: List[str] = []
    for frame in traceback.extract_tb(tb)[-MAX_FRAMES:]:
        name = re.split(r"[\\/]", frame.filename)[-1]
        frames.append(f"{name}:{frame.lineno} in {frame.name}")
    info["frames"] = frames
    return info


def _utc_timestamp(record: logging.LogRecord) -> str:
    return datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _payload(record: logging.LogRecord) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "ts": _utc_timestamp(record),
        "level": record.levelname,
        "logger": record.name,
        "event": redact_text(record.getMessage()),
        "correlation_id": get_correlation_id(),
    }
    fields = getattr(record, "fields", None)
    if fields:
        payload["fields"] = redact(fields)
    if record.exc_info:
        payload["exception"] = describe_exception(record.exc_info)
    return payload


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(_payload(record), ensure_ascii=True, default=lambda o: f"<{type(o).__name__}>")


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = _payload(record)
        line = f"{data['ts']} {data['level']} {data['logger']} cid={data['correlation_id']} {data['event']}"
        if "fields" in data:
            line += " " + json.dumps(data["fields"], ensure_ascii=True, default=lambda o: f"<{type(o).__name__}>")
        if "exception" in data:
            line += " exc=" + json.dumps(data["exception"], ensure_ascii=True)
        return line.replace("\r", "\\r").replace("\n", "\\n")


# ------------------------------------------------------------------ setup
def configure_logging(settings: Optional[LoggingSettings] = None, stream: Optional[IO[str]] = None,
                      logger_name: str = LOGGER_NAME) -> logging.Logger:
    """Install ONE managed handler on the `bankwise` logger (or on another named logger such as `id_ocr`), never on
    the root logger. Safe to call repeatedly: the previous managed handler is replaced, never duplicated."""
    settings = settings or LoggingSettings()
    logger = logging.getLogger(logger_name)
    for handler in list(logger.handlers):
        if getattr(handler, "_bankwise_managed", False):
            logger.removeHandler(handler)
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler._bankwise_managed = True  # type: ignore[attr-defined]
    handler.addFilter(RedactionFilter(settings.max_message_chars))
    handler.setFormatter(JsonFormatter() if settings.format == "json" else ConsoleFormatter())
    handler.setLevel(settings.level)
    logger.addHandler(handler)
    logger.setLevel(settings.level)
    logger.propagate = False
    return logger


def get_logger(name: str = "") -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    """Log a short, constant event name plus structured fields (redacted by the handler filter)."""
    logger.log(level, event, extra={"fields": fields})


def install_safe_excepthook() -> None:
    """Replace the default uncaught-exception printer, which writes the raw exception message (possibly containing
    an ID number or a name) and traceback to stderr. Uncaught exceptions are logged through the redacting handler
    and only a generic line is printed. If `configure_logging` has not been called, nothing is logged at all
    (the standard fallback handler would print unredacted text) and only the generic line is printed."""
    def hook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        if any(getattr(h, "_bankwise_managed", False) for h in logging.getLogger(LOGGER_NAME).handlers):
            get_logger("uncaught").critical("uncaught exception", exc_info=(exc_type, exc, tb))
            note = "details were written to the application log"
        else:
            note = "logging is not configured, so no details were recorded"
        sys.stderr.write(f"An unexpected error occurred; {note}.\n")

    sys.excepthook = hook
    threading.excepthook = lambda args: hook(args.exc_type, args.exc_value, args.exc_traceback)
