# Security and privacy: Phase 1 (core infrastructure)

This document separates what is **implemented and tested** from what is **not**. Nothing here is a claim of
production readiness, regulatory compliance, or proof that a document is genuine.

## Implemented and tested (`src/bankwise/core/`, `tests/unit/core/`)

| Area | Module | What it does |
|---|---|---|
| Configuration | `config.py`, `configs/*.yaml` | Loads and validates `app.yaml`, `rbac.yaml`, `logging.yaml`. Unknown keys, wrong types, out-of-range values and duplicate YAML keys raise `ConfigError`. Environment overrides: `BANKWISE_ENV`, `BANKWISE_LOG_LEVEL`, `BANKWISE_LOG_FORMAT`, `BANKWISE_CONFIG_DIR`. |
| Secrets | `config.get_secret` | Secrets come from environment variables only, are returned wrapped in `Sensitive` (never shown by `str`/`repr`/f-strings/pickle) and are registered so log redaction masks them. In production they must be present, at least 32 characters, not placeholder text. |
| Environment | `config.parse_environment` | If `BANKWISE_ENV` is unset or blank the app assumes **production** (fails closed). |
| Errors | `errors.py` | Typed errors with stable codes (`config_invalid`, `validation_failed`, `permission_denied`, `rate_limited`, `internal_error`). `str(error)` and `to_public()` contain only a fixed message; diagnostic text is kept in `.detail` for internal logs. `to_public_error()` maps any unexpected exception to a generic internal error. |
| PII redaction | `pii.py` | Key-based redaction (national ID, names, address, birth date, OCR text, tokens, passwords ...) and pattern-based masking of ID numbers (Latin, Arabic-Indic and Persian digits, with separators), JWTs, bearer tokens, API-key shapes and `password=` style assignments. Unknown objects, images and bytes are never dumped through `repr`. |
| Logging | `logging.py` | Standard `logging` on the `bankwise` logger only. A filter on the managed handler redacts every record before it is formatted. JSON (or console) output, UTC timestamps, correlation id, no duplicate handlers on repeated setup, exceptions logged as type/code/redacted message plus `file.py:line` frames (no absolute paths, no local variables), message length cap. |
| Audit events | `audit.py` | `AuditEvent` validates itself: sensitive metadata keys are refused, string values must be short printable ASCII (this excludes Arabic names and addresses) and free of ID/token patterns. Sinks: `InMemoryAuditSink` (bounded), `LoggingAuditSink`. |
| Authorization | `security.py`, `configs/rbac.yaml` | RBAC with **deny by default**: no principal, unknown role, unknown permission, or no grant means denied. Wildcards (`admin:*`) only match permissions listed in the catalogue. `admin` does not include `identity:view_sensitive`. Denials are audited. |
| Raw data access | `security.reveal_sensitive` | The only sanctioned way to read a `Sensitive` value for a user: needs `identity:view_sensitive`, a reason, and the audit event is written **before** the value is released. |
| Output files | `security.safe_output_path`, `write_private_text` | Rejects absolute/drive/UNC paths, `..`, NUL bytes, Windows reserved names and symlink escapes; files are created owner-only (POSIX) and are not overwritten unless asked. |
| Rate limiting | `rate_limit.py` | Sliding-window limiter with exact boundary behaviour, explicit rejection of missing/invalid keys, thread-safe, fails closed when its key table is full. |

## Not implemented (do not assume these exist)

* **Authentication.** There is no login, token verification, user store or password handling.
  `Principal` is only a *claim* about the caller; creating one proves nothing. Planned design for the future API:
  the credential (for example an `Authorization: Bearer` header) is verified by a dedicated authentication
  step (signature, expiry, issuer and audience checked, algorithm pinned, revocation supported); only that step
  creates a `Principal` from the verified claims and hands it to `Authorizer`. Authorization never reads
  credentials, and the secret comes from `get_secret("jwt_secret")`. Add `jwt_secret` to
  `required_in_production` in `configs/app.yaml` when this is built.
* **Durable or tamper-resistant audit storage.** `InMemoryAuditSink` is lost on exit; `LoggingAuditSink` is only as
  trustworthy as the log destination. Structured JSON is not tamper-proof.
* **Distributed rate limiting.** The limiter is per process, resets on restart, and protects nothing that does
  not call it (there is no API yet). Use a shared store for real enforcement.
* **Detection of names and addresses in free text.** Arabic names and addresses have no pattern. They are protected
  only when passed as structured data under sensitive keys. A message like `log.info(f"text: {raw_ocr_text}")`
  will leak them (a test documents this limit). Never pre-format raw OCR text into a log message.
* **OCR integration.** The OCR package (`src/id_ocr/`) has not been modified or wired to these modules.
  No claim is made about its own logging or output files.
* No encryption at rest, no secret manager, no TLS, no document-authenticity or liveness checks.

## Rules for developers

1. Log structured fields (`log_event(logger, level, "event_name", key=value)`), never raw OCR payloads or images.
2. Hold raw personal values in `Sensitive` where they travel through code; read them only via `reveal_sensitive`.
3. Keep audit metadata to codes, counts and decisions.
4. Write generated files only through `safe_output_path` + `write_private_text`, under a git-ignored directory
   (`outputs/` is already ignored). Output JSON that contains PII is sensitive data.
5. Put secrets in the environment, never in files; `.env` is git-ignored, `.env.example` holds names only.

## Verifying

```powershell
python -m pytest tests/unit/core -q          # new core tests only
python -m pytest -q                          # everything, including the existing OCR tests
```
