"""Processing an ID-card image pair behind the security layer.

`IdentityProcessingService.process()` is the ONLY intended way for application code to run OCR on a caller's
images. OCR runs only after, in this order: authentication -> per-client lockout -> per-user rate limit ->
authorization (`identity:process`) -> image validation -> concurrency slot. A failure at any step stops there and
the processor is never called.

What it does NOT do:
* It does not read files: callers pass image BYTES. There is no path parameter, so no path can be injected.
* It is not an entry point. The API calls this with the received Authorization header;
  database/login responsibilities remain outside the OCR service.
* The OCR result is NOT proof that the card is genuine or that the holder is the person presenting it.
* It cannot time out or kill a stuck OCR run, and it validates image headers, not pixel data.

Privacy: logs and audit events carry only the request id (correlation id), outcome, error codes, image kind, byte
counts and timing. The OCR result is returned wrapped: `summary` is a redacted copy, `sensitive` is a `Sensitive`
wrapper that can be read only through `security.reveal_sensitive` (permission + reason + audit)."""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Protocol, Tuple

from bankwise.core.audit import AuditEvent, AuditOutcome, AuditSink
from bankwise.core.bootstrap import build_security
from bankwise.core.config import IdentityProcessingSettings
from bankwise.core.errors import BankWiseError, ConfigError, ErrorCode, RateLimitExceeded
from bankwise.core.guard import SecurityService
from bankwise.core.images import ImageInfo, ImageRejected, inspect_image
from bankwise.core.logging import correlation_scope, get_correlation_id, log_event
from bankwise.core.pii import Sensitive, redact
from bankwise.core.security import Principal

PROCESS_PERMISSION = "identity:process"
NOTICE = ("OCR extraction only. This is not proof that the card is genuine or that the person presenting it is the "
          "cardholder.")
_TYPE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,60}$")
_stdlib_log = logging.getLogger("bankwise.identity")


@dataclass(frozen=True)
class ProcessorOutput:
    """`raw` is the full result (contains personal data); `summary` is a projection that the processor itself built
    from codes and states only. The service still redacts `summary` once more before exposing it."""

    raw: Mapping[str, Any]
    summary: Mapping[str, Any]


class IdentityProcessor(Protocol):
    """What the OCR side must provide (see `integration/id_ocr_adapter.py` for the real implementation). It may return
    a `ProcessorOutput` or a plain mapping (then the summary is the generically redacted mapping). It must not log or
    persist the images or the result, and must not read any path the caller chose."""

    def process(self, front_image: bytes, back_image: bytes) -> "ProcessorOutput | Mapping[str, Any]":
        ...


@dataclass(frozen=True)
class ProcessingResult:
    request_id: str
    summary: Dict[str, Any]          # redacted copy: safe to log or return to a low-privilege caller
    sensitive: Sensitive             # the raw OCR mapping; read only via security.reveal_sensitive
    notice: str = NOTICE


class ProcessingObserver(Protocol):
    """Trusted application hooks, never client-supplied; no raw output is passed to persistence.

    started runs after the single security gate and may reject/replay a reserved attempt.
    succeeded runs only after the existing strict processing audit has completed.
    """
    def started(self, principal: Principal, request_id: str) -> None: ...
    def succeeded(self, summary: Mapping[str, Any]) -> None: ...
    def failed(self, error: Exception) -> None: ...


def _type_name(error: BaseException) -> str:
    name = type(error).__name__
    return name if _TYPE_NAME_RE.match(name) else "Exception"


class IdentityProcessingService:
    def __init__(self, security: SecurityService, sink: AuditSink, settings: IdentityProcessingSettings,
                 processor: IdentityProcessor) -> None:
        self._security = security
        self._sink = sink
        self._settings = settings
        self._processor = processor
        self._slots = threading.BoundedSemaphore(settings.max_concurrent)
        self.audit_failures = 0

    def process(self, authorization_header: Optional[str], client_key: str, front_image: object, back_image: object,
                request_id: Optional[str] = None) -> ProcessingResult:
        """`request_id` (or the id of an enclosing `correlation_scope`) identifies the request in logs and audit
        records; an untrusted value is replaced by a fresh random one."""
        return self.process_upload(authorization_header, client_key, lambda: (front_image, back_image), request_id)

    def process_upload(self, authorization_header: Optional[str], client_key: str,
                       load_images: Callable[[], Tuple[object, object]], request_id: Optional[str] = None,
                       observer: Optional[ProcessingObserver] = None) -> ProcessingResult:
        """Authenticate/authorize once before multipart parsing and OCR; preserve process() callers."""
        with correlation_scope(request_id or get_correlation_id()) as cid:
            principal = self._security.guard(authorization_header, PROCESS_PERMISSION, client_key,
                                             rate_policy=self._settings.rate_policy)
            if observer is not None:
                observer.started(principal, cid)
            try:
                try:
                    front, back = load_images()
                except BankWiseError as error:
                    self._audit_failure(principal, error.code, {"stage": "upload"})
                    raise
                result = self._run(principal, cid, front, back)
                if observer is not None:
                    observer.succeeded(result.summary)
                return result
            except Exception as error:
                if observer is not None:
                    observer.failed(error)
                raise

    def extract_own_upload(self, authorization_header, client_key, load_images):
        """Disclose only this caller's current upload; no stored-result IDs or reviewer access.

        Raw fields are released after both processing authorization and a strict
        disclosure audit. Images/fields are not persisted by this path.
        """
        principal = self._security.guard(authorization_header, 'identity:read_own_upload', client_key)
        result = self.process_upload(authorization_header, client_key, load_images)
        self._security.record_access('identity.own_upload_disclosed', principal.subject_id)
        return self._extracted_fields(result)

    def extract_application_upload(self, application_id, load_images):
        """Internal use only: caller must authenticate a short-lived onboarding session and rate-limit it."""
        front, back = load_images()
        principal = Principal(str(application_id), frozenset())
        result = self._run(principal, str(application_id), front, back)
        self._security.record_access("onboarding.ocr_disclosed", str(application_id))
        return self._extracted_fields(result)

    @staticmethod
    def _extracted_fields(result):
        raw = result.sensitive.reveal()
        names = ('first_name', 'other_names', 'address_line1', 'address_line2',
                 'national_id', 'birth_date', 'gender', 'job', 'religion',
                 'marital_status', 'issue_date', 'expiry_date')
        fields = {name: raw.get(name) if isinstance(raw.get(name), str) else None for name in names}
        validation = raw.get('validation', {})
        identity = validation.get('national_id', {}) if isinstance(validation, dict) else {}
        decoded = identity.get('decoded') or {}
        derived = {}
        if identity.get('status') == 'valid_structure' and isinstance(decoded, dict) and isinstance(decoded.get('birth_date'), str):
            derived['birth_date_from_national_id'] = decoded['birth_date']
        return {'request_id': result.request_id, 'fields': fields, 'derived': derived,
                'summary': result.summary, 'notice': NOTICE}

    # ------------------------------------------------------------------ steps after the gates
    def _run(self, principal: Principal, request_id: str, front: object, back: object) -> ProcessingResult:
        infos: Dict[str, ImageInfo] = {}
        for side, data in (("front", front), ("back", back)):
            try:
                infos[side] = inspect_image(data, self._settings.max_image_bytes, self._settings.max_pixels,
                                            self._settings.min_side_pixels)
            except ImageRejected as error:
                self._audit_failure(principal, ErrorCode.VALIDATION_FAILED, {"stage": "validation", "side": side,
                                                                              "reason": error.reason})
                raise
        if not self._slots.acquire(blocking=False):
            self._audit_failure(principal, ErrorCode.RATE_LIMITED, {"stage": "capacity"})
            raise RateLimitExceeded("OCR capacity exhausted", retry_after_seconds=1.0)
        started = time.monotonic()
        try:
            try:
                output = self._processor.process(bytes(front), bytes(back))    # type: ignore[arg-type]
                if isinstance(output, ProcessorOutput):
                    raw, summary_source = output.raw, output.summary
                else:
                    raw = summary_source = output
                if not isinstance(raw, Mapping) or not isinstance(summary_source, Mapping):
                    raise TypeError("processor result is not a mapping")
            except ImageRejected as error:                  # the image passed the header checks but cannot be decoded
                self._audit_failure(principal, ErrorCode.VALIDATION_FAILED,
                                    {"stage": "decode", "reason": error.reason})
                raise
            except Exception as error:                  # third-party OCR/torch/cv2 can raise anything
                self._audit_failure(principal, ErrorCode.INTERNAL_ERROR,
                                    {"stage": "ocr", "error_type": _type_name(error)})
                raise BankWiseError(f"ocr failed: {_type_name(error)}") from None
        finally:
            self._slots.release()
        elapsed_ms = int((time.monotonic() - started) * 1000)
        result = ProcessingResult(request_id, redact(summary_source), Sensitive(dict(raw)))
        metadata = {"stage": "ocr", "front_type": infos["front"].kind, "back_type": infos["back"].kind,
                    "front_bytes": infos["front"].size_bytes, "back_bytes": infos["back"].size_bytes,
                    "duration_ms": elapsed_ms}
        # Fail-closed: a result is released only if its audit record was written.
        self._write(AuditEvent("identity.process", AuditOutcome.SUCCESS, actor_id=principal.subject_id,
                               metadata=metadata), strict=True)
        log_event(_stdlib_log, logging.INFO, "identity_process_finished", outcome="success", duration_ms=elapsed_ms,
                  front_bytes=infos["front"].size_bytes, back_bytes=infos["back"].size_bytes)
        return result

    # ------------------------------------------------------------------ audit helpers
    def _audit_failure(self, principal: Principal, code: ErrorCode, metadata: Dict[str, Any]) -> None:
        self._write(AuditEvent("identity.process", AuditOutcome.FAILURE, actor_id=principal.subject_id,
                               error_code=code.value, metadata=metadata), strict=False)
        log_event(_stdlib_log, logging.WARNING, "identity_process_failed", outcome="failure", error_code=code.value,
                  **{k: v for k, v in metadata.items() if k in ("stage", "reason", "side", "error_type")})

    def _write(self, event: AuditEvent, strict: bool) -> None:
        try:
            self._sink.write(event)
        except Exception as error:                      # any sink failure is counted and logged, never silent
            self.audit_failures += 1
            _stdlib_log.error("audit write failed for %s (%s)", event.event_type, type(error).__name__)
            if strict:
                raise BankWiseError(f"audit write failed for {event.event_type}") from None


def create_identity_service(processor: IdentityProcessor, directory: Optional[Path] = None,
                            environ: Optional[Mapping[str, str]] = None,
                            root: Optional[Path] = None) -> IdentityProcessingService:
    """Build the security layer with `build_security()` (production refuses to start unless its guarantees hold)
    and attach the processor. Raises ConfigError when the processing section, its rate-limit policy, or the
    `identity:process` permission is missing, so a misconfiguration fails at start-up instead of denying every request."""
    runtime = build_security(directory, environ, root)
    settings = runtime.config.identity_processing
    if settings is None:
        raise ConfigError("app.yaml must define an 'identity_processing' section")
    if settings.rate_policy not in runtime.config.rate_limits:
        raise ConfigError(f"app.yaml rate_limits must define '{settings.rate_policy}'")
    if PROCESS_PERMISSION not in runtime.authorizer.policy.permissions:
        raise ConfigError(f"rbac.yaml must define the '{PROCESS_PERMISSION}' permission")
    return IdentityProcessingService(runtime.service, runtime.audit_sink, settings, processor)
