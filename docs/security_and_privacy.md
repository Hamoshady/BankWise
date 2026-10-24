# Security and privacy

This document separates what is **implemented**, what is **tested**, what still needs an application **entry point**,
and what is a **deployment responsibility**. Nothing here is a claim of production readiness, regulatory
compliance, or proof that a document is genuine. Wiring instructions are in
[security_integration.md](security_integration.md).

**Important:** these are libraries in `src/bankwise/core/`. A control protects an execution path only if that path
calls it. At the time of writing nothing in `src/id_ocr/` or the identity scripts calls them (for example the OCR CLI
writes `--out` files directly) and there is no API, database or login endpoint. "Tested" means the unit tests in
`tests/unit/core/` exercise it with simulated callers, not that a real request path is protected.

## Data classification: what may be logged

| Class | Examples | In logs / audit events |
|---|---|---|
| **Never** (not even redacted, do not pass it to a logger) | raw national ID number, ID images, OCR text, passwords, access tokens, signing/HMAC keys | forbidden |
| **Sensitive** (only as structured fields under a sensitive key, which the redactor replaces) | names, address, date of birth, gender, extracted JSON | redacted automatically **only when passed as structured data** |
| **Allowed** | decision codes (`MANUAL_REVIEW`), counts, timings, error codes, opaque ids (`jti`, file ids, correlation id), user ids (`user-1`), role and permission names | yes |

Free text is the weak spot: an ID number or secret inside a message is masked by pattern, but an Arabic name or
address has no pattern and will pass through an f-string unredacted (a test documents this). Log structured
fields; never pre-format raw OCR text into a message.

## Implemented in code (`src/bankwise/core/`)

| Area | Module | Behaviour |
|---|---|---|
| Configuration | `config.py`, `configs/*.yaml` | Strict validation (unknown keys, wrong types, ranges, duplicate YAML keys, 256 KB file cap) -> `ConfigError`. Overrides: `BANKWISE_ENV`, `BANKWISE_LOG_LEVEL`, `BANKWISE_LOG_FORMAT`, `BANKWISE_CONFIG_DIR`. |
| Environment | `config.parse_environment` | Unset or blank `BANKWISE_ENV` means **production** (fails closed). |
| Secrets | `config.get_secret` | Environment only; wrapped in `Sensitive` (never shown by `str`/`repr`/f-strings/pickle) and registered for log redaction. Production: present, >= 32 characters, no placeholder text, enough character variety. Names: `jwt_secret`, `audit_hmac_key` (required when `audit` is configured), `pii_hmac_key` (reserved, unused). |
| Start-up | `bootstrap.build_security` | Builds everything from config. Production **refuses to start** without strong secrets, a durable audit sink, and `deployment.single_process: true`. Development starts with warnings. |
| Errors | `errors.py` | Stable codes; `str(error)` / `to_public()` carry only a fixed message; diagnostics stay in `.detail`; `AuthenticationError.reason` is internal. `to_public_error()` maps any unexpected exception to a generic one. |
| PII redaction | `pii.py` | Key-based redaction (national ID, names, address, birth date, OCR text, tokens, passwords, camelCase keys too) plus pattern masking of ID numbers (Latin/Arabic-Indic/Persian digits, separators), JWTs, bearer tokens (`Authorization: Bearer x`), API-key shapes, `password=` assignments and registered secret values. Unknown objects, images and bytes never go through `repr`. |
| Logging | `logging.py` | `bankwise` logger only; a filter on the managed handler redacts every record before formatting; JSON/console; UTC; correlation id (untrusted ids replaced); message cap; exceptions without absolute paths or locals; `install_safe_excepthook()` stops uncaught exceptions printing raw messages. |
| Authorization | `security.py`, `configs/rbac.yaml` | RBAC, deny by default; wildcards only over listed permissions; `admin` excludes `identity:view_sensitive`; denials audited; `reveal_sensitive` needs permission + reason and audits **before** releasing the value. |
| Authentication | `auth.py` | HS256-only JWT, algorithm pinned twice; required `iss/aud/sub/jti/iat/nbf/exp`, `typ=access`; **exact** issuer/audience comparison of our own (PyJWT 2.10.0 accepted partial issuers, CVE-2024-53861); lifetime cap; one injectable clock + leeway; signing secret must be >= 32 characters. |
| Token revocation | `auth.py` | `revoke(token)` (logout), `revoke_subject(id)` (role change, suspension, theft): every token issued to that subject up to now is refused; the user logs in again for a token with the new roles. A token issued in the same second as the revocation is also refused. |
| Passwords | `passwords.py` | Argon2id, NFKC normalisation, 12-256 characters, `verify_password` never raises, `password_needs_rehash`, `verify_password_or_dummy` (does the full work for unknown users). |
| Security gate | `guard.py` | `SecurityService`: lockout check -> authenticate -> per-user rate limit -> authorize -> audit. Failed authentications are charged to the client key; successes are not. Token issuance is **fail-closed** on audit failure; denials and revocations are already effective, so a failed audit write is counted in `audit_failures` and logged, never silent. `revoke_subject` needs `admin:manage`. |
| Rate limiting | `rate_limit.py` | Sliding window, exact boundaries, `peek`, keys normalised (NFKC, casefold, trim) so `Alice`/`ALICE ` share a budget, control characters rejected, thread-safe, fails closed when the key table is full. |
| Audit trail | `audit.py` | `AuditEvent` refuses personal data. `HashChainedFileAuditSink`: durable (fsync per record), owner-only, never follows symlinks, HMAC-chained, verified at start-up (start fails if tampered), bounded (`write` raises when full), `verify_audit_file`. Tamper-**evident**, not tamper-proof (see below). `LoggingAuditSink` is operational logging only, not an audit record. |
| Output files | `security.py` | `safe_output_path` rejects absolute/drive/UNC paths, `..`, NUL, control characters, Windows reserved names, NTFS stream and special characters (`: < > " | ? *`), symlink escapes. `write_private_text`: owner-only, no overwrite by default, no symlink at the final component (POSIX), size cap, binary write, half-written file removed on failure. |
| Image checks | `images.py` | JPEG/PNG only, decided from header bytes (no decoder): size cap, pixel-count cap, minimum side; paths and non-bytes rejected. Does not prove the file is a valid image. |
| Identity processing | `integration/identity_processing.py` | OCR only after authentication, lockout, rate limit and `identity:process` authorization; validation; concurrency cap; no PII in logs/audit; result wrapped (`summary` redacted, `sensitive` via `reveal_sensitive`); release only after the audit record is written. `integration/id_ocr_adapter.py` connects it to `IdOcrPipeline` (arrays in memory, whitelist summary, redacted `id_ocr` logging, no downloads/debug crops). Unit-tested with a stand-in pipeline that has the real call signature; the real-OCR test is opt-in and has not been run by the author. |
| Sensitive uploads | `storage.py` | Random opaque ids, owner-only vault (tightened to 0700 or start fails), JPEG/PNG magic bytes, size cap, file-count quota, expiry enforced on read and by `purge_expired()` (also run at start-up), symlinks and hard links never read or overwritten, best-effort overwrite on delete. |

## Tested (by unit tests, in a sandbox with simulated callers)

Every control in the table above has unit tests under `tests/unit/core/`, including negative and boundary cases, and
the tests were mutation-checked (deliberately breaking each control makes a test fail). The suite also runs against
the vulnerable PyJWT 2.10.0 to show the exact issuer check holds. **The results on your machine are what count:**
run the commands in the integration guide.

## Not implemented (do not assume these exist)

* **A login system** (user table, registration, reset, MFA, refresh tokens, sessions). `passwords.py` and `auth.py`
  are the primitives. `Principal` is a claim made by code that has verified credentials; constructing one proves nothing.
* **Tamper-PROOF audit.** The hash chain detects edits, insertions, reordering, deletions in the middle and forgeries
  without the key. It does **not** detect removal of the last records or a rewrite by someone holding the HMAC key or
  the ability to replace the file together with the key. Anchor `last_mac` + record count outside the host (another
  machine, a signed report, a ticket) on a schedule. There is no rotation: a full file stops auditing until an operator
  archives it (verify with the key that wrote it) and starts a new one. One writing process only.
* **Shared state across processes.** Rate limits and revocations live in one process's memory and reset on restart
  (after a restart, revoked tokens are valid again until they expire). Production start-up demands the
  `single_process` declaration for this reason. A shared store behind `RateLimiterBackend` / `RevocationStore` is future work.
* **Rate-limit evasion by changing identity.** Limits are per key; an attacker rotating IP addresses gets fresh budgets.
  A login endpoint also needs a per-username limit; this library cannot know the username before verification.
* **Key rotation tooling.** One JWT secret and one audit key at a time. Changing the JWT secret invalidates every issued
  token (users log in again); there is no `kid`-based overlap. Changing the audit key means archiving the old file and
  keeping the old key to verify it. No HMAC PII fingerprint exists (none is needed yet): if one is added it must use its
  own key (`pii_hmac_key`), be versioned, and never be reused as a token or audit key.
* **Encryption at rest** for stored images and outputs, reliable secure deletion (best effort on SSDs and journaling
  file systems), decoding-level image hardening (the header checks in `images.py` stop oversized and non-JPEG/PNG files only; EXIF stripping and decoder safety belong to the OCR side), OCR timeouts, automatic scheduling of `purge_expired()`.
* **Transport and web hardening** (TLS, CORS, security headers, CSRF, request size limits): API layer, which does not exist.
* **OCR and identity pipeline integration.** `src/id_ocr/` and the identity scripts are unchanged; no claim is made about
  their own logging, `--out` files, `--debug` image directories or `output_cards/`.
* A dependency vulnerability **scan has not been performed**; see the commands below. No secret manager, no
  document-authenticity or liveness checks.

## Deployment responsibilities

* Set `BANKWISE_ENV=production` and provide `BANKWISE_JWT_SECRET` and `BANKWISE_AUDIT_HMAC_KEY` from a secret store
  (not from files in the repository); generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
* Run exactly one application process, or provide shared stores first; then set `deployment.single_process: true`.
* Protect `outputs/` (vault, audit file) with filesystem permissions or ACLs, back it up, and ship `last_mac` offsite.
* Terminate TLS in front of the API; trust proxy headers only from your own proxy.
* Keep the host, Python and dependencies patched; keep `outputs/`, `.env` and image folders out of git and out of
  container images and backups you do not control.

## Rules for developers

1. Log structured fields (`log_event(...)`), never raw OCR payloads, images, tokens or passwords.
2. Keep raw personal values in `Sensitive`; read them only through `reveal_sensitive`.
3. Keep audit metadata to codes, counts, decisions and opaque ids.
4. Write generated files with `safe_output_path` + `write_private_text`; keep uploads in `SecureFileStore` and delete them
   as soon as processing ends.
5. Secrets live in the environment; `.env` is git-ignored; `.env.example` holds names only.
6. Treat a changed role as an event: call `revoke_subject`, do not wait for tokens to expire.

## Checking dependencies and running tests (PowerShell, from the repository root)

```powershell
python -m pip install -r requirements.txt
python -m pip check                              # dependency conflicts (not a vulnerability scan)
python -c "import jwt; print(jwt.__version__)"   # must be >= 2.10.1
python -m pip install pip-audit                  # one-off scanner (a new dev tool, not a project dependency)
python -m pip_audit -r requirements.txt          # known-vulnerability scan: run it and read the output yourself
python -m pytest tests/unit/core -q              # core security tests
python -m pytest -q                              # everything
git check-ignore -v outputs/secure/x.bin outputs/audit/audit.jsonl .env   # each must print the rule that ignores it
```
