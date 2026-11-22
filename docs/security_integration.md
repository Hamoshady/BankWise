# Security integration guide

The security foundation is now connected to the REST API and the local OCR adapter.
The standalone OCR CLI retains its existing behavior. See [api_contract.md](api_contract.md)
and [backend_setup.md](backend_setup.md) for the implemented entry point and deployment.

## Status

| Component | API integration |
|---|---|
| Config, secrets, logging | Application lifespan calls build_security once; redaction enabled |
| JWT authentication and passwords | Login verifies database credentials with existing Argon2 helpers; shared SecurityService issues/verifies tokens |
| RBAC, lockout and rate limits | Existing guard owns processing authorization; GET adds read permission and owner filtering |
| Audit events | Existing durable file sink retained; processing/read/replay/token grants fail closed |
| Identity processing | Existing service and local IdOcrAdapter; synchronous, no serving-time downloads |
| Database | PostgreSQL, SQLAlchemy sessions and Alembic; users, role associations, minimal identity metadata |
| Uploads | Bounded multipart parsed in memory; no temporary-file spooling or raw-image persistence |
| CLI output files | Existing OCR CLI output behavior unchanged; not covered by the API |

## Composition root (tested)

Every entry point calls this once, before it serves anything. It configures redacting logging, validates
configuration and secrets, builds the audit sink and the vault, and **refuses to start in production** if a
guarantee cannot be met (see `core/bootstrap.py`):

```python
from bankwise.core.bootstrap import build_security
from bankwise.core.logging import install_safe_excepthook

install_safe_excepthook()                        # uncaught exceptions no longer print raw messages
runtime = build_security()                       # ConfigError -> do not start
security = runtime.service                       # SecurityService
store = runtime.store                            # SecureFileStore (or None if no `storage` section)
for note in runtime.warnings:                    # development notes; empty in a valid production setup
    print(note)
```

`tests/unit/core/test_bootstrap.py` runs this against real and temporary configs, in development and production.

## Identity processing (OCR behind the security layer)

`IdentityProcessingService.process(authorization_header, client_key, front_bytes, back_bytes, request_id=None)` and
its API-facing `process_upload` method are the intended paths for caller OCR. Both use: per-client lockout -> authentication ->
per-user rate limit (`identity_process`) -> authorization (`identity:process`) -> header-level image validation
(JPEG/PNG only, `max_image_bytes`, `max_pixels`, `min_side_pixels`) -> a concurrency slot (`max_concurrent`). A failure
at any step stops there and the processor is not called. Rejected requests still spend the user's rate-limit budget.

```python
from bankwise.integration.identity_processing import create_identity_service

service = create_identity_service(my_ocr_adapter)    # build_security() inside: ConfigError means do not start
result = service.process(request.headers.get("Authorization"), f"ip:{client_ip}", front_bytes, back_bytes,
                         request_id=request_id)
result.summary    # redacted copy, safe to return to the caller or to log
result.sensitive  # raw OCR mapping in a Sensitive wrapper: only security.reveal_sensitive(...) can read it
result.notice     # always: OCR extraction only, not proof of authenticity or of the holder's identity
```

* **RBAC:** `identity:process` is granted only to the dedicated `identity_processor` role (see `configs/rbac.yaml`). No
  existing role (`operator`, `reviewer`, `auditor`, `admin`) has it, and nobody gets it by default.
* **Privacy:** logs and audit events carry only the request id, outcome, error code, image kind, byte counts and timing.
  A successful result is released only after its audit record has been written (fail-closed). OCR failures become a generic
  `internal_error`; the exception message is never copied anywhere.
* **Adapter** (`integration/id_ocr_adapter.py`, written against the real `IdOcrPipeline.process()`): the pipeline accepts a
  numpy array per side, so the adapter decodes the validated bytes in memory (`cv2.imdecode`, BGR) and passes arrays. **No
  temporary file is written and no path is passed.** The pipeline's `public` JSON (national id, names, address,
  `all_text_lines`, ...) becomes `result.sensitive` (readable only through `reveal_sensitive`); `result.summary` is a
  codes-only WHITELIST projection (decision codes, check states, `name:status` pairs, quality numbers, timings), so a field
  added to the pipeline later cannot leak into it. `create_id_ocr_adapter()` builds the pipeline with `download_models=False`
  (install models beforehand) and `debug_dir=None` (no crops on disk), and `protect_ocr_logging()` puts the redacting handler
  on the `id_ocr` logger at WARNING or above: the pipeline's own INFO line contains a partially masked id.
* The CLI (`id_ocr/cli.py`) and the pipeline are unchanged. The CLI's `--out` file and `--debug-dir` crops are still written
  directly and are not covered by any of this.

```python
from bankwise.integration.id_ocr_adapter import create_id_ocr_adapter
from bankwise.integration.identity_processing import create_identity_service

adapter = create_id_ocr_adapter(model_dir="models", use_gpu=True)     # EngineError here = deployment problem
service = create_identity_service(adapter)                            # ConfigError here = do not start
result = service.process(authorization_header, "ip:203.0.113.7", front_bytes, back_bytes)
```

* **Not provided:** an OCR timeout (a hung run keeps its slot), decoding-level image hardening (only headers are checked),
  persistence of raw results, and any claim that a card is genuine or that its holder is the person presenting it.

### Credential verification interface (implemented by database/accounts.py; no default users exist)

```python
class CredentialVerifier(Protocol):
    def verify(self, username: str, password: str) -> Optional[Principal]:   # None = unknown user OR wrong password
        ...
```

A login handler built on it must: verify with `verify_password_or_dummy(stored_hash_or_None, password)`; rate-limit
with the `auth_attempt` policy per client **and** per (normalised) username; load the roles from the user store at login time
(so a role change plus `revoke_subject` takes effect); return the token from `security.issue_token(principal)`; never
log the password or the token. The user table and operator provisioning CLI now exist. Password reset and remote account management remain separate work.

### Running the real-OCR integration test (opt-in, on your machine)

```powershell
$env:RUN_REAL_OCR = "1"
$env:ID_OCR_MODEL_DIR = "C:\path\to\models"     # optional; models must already be downloaded
$env:ID_OCR_GPU = "1"                                # optional; default CPU
python -m pytest tests/integration -q -s -rs
```

It uses a synthetic card (no real data) and checks the integration contract: gates, in-memory decoding, no PII in logs/audit/
summary, no files written, and that bytes and a file path give the same OCR result. It is skipped, with the reason printed,
unless `RUN_REAL_OCR=1` and easyocr/id_ocr are importable.

## Integration points by phase

**API (implemented).** `api/main.py:create_app` owns the shared lifespan. Dependencies resolve
that runtime and request-scoped sessions. The identity service owns the processing gate; a
trusted observer reserves/persists metadata without receiving raw OCR. Bounded transport
handling, explicit DTOs and sanitized exceptions are described in [api_contract.md](api_contract.md).

**Login (implemented).** `SecurityService.login` applies client and normalized-account budgets
before `Accounts.verify` uses the real/dummy password verifier and loads persisted roles.
Token issuance is audited and fail-closed. Active account and exact role-set checks supplement
token validation. Local provisioning is documented in [backend_setup.md](backend_setup.md).

**Shared security storage (future, beyond current metadata persistence).** (a) a database-backed `AuditSink` (append-only table, write-only credentials) if the file sink is not enough; (b) a persistent `RevocationStore` (the existing four-method protocol);
(c) a shared `RateLimiterBackend`; (d) store only `fingerprint`-style references, never raw national IDs, where a
lookup key is needed (an HMAC with `pii_hmac_key` is the planned primitive; it is not implemented yet).

**Optional future persisted uploads (not used by this API).** `file_id = store.save(image_bytes)`; pass the opaque id between steps; call
`store.delete(file_id)` as soon as processing ends; call `store.purge_expired()` at start-up and on a timer. Decode
and size-limit the image in the vision step before OCR.

**OCR CLI `--out` (existing code, not yet changed).** To adopt the secure helpers, replace the direct write with:

```python
from bankwise.core.security import safe_output_path, write_private_text

target = safe_output_path("outputs", args.out)       # InvalidInputError on traversal / absolute paths
write_private_text(target, json_text)                # refuses to overwrite; pass overwrite=True deliberately
```

This changes behaviour (output is confined to `outputs/`, existing files are not silently replaced), so it is a
decision for the CLI owner. Do the same for any `--debug` directory that stores card images: those are personal
data and should go through `SecureFileStore` or at least be deleted after use.

## Dependencies added in this phase

* `PyJWT>=2.10.1` (token signing/verification). **Not** 2.10.0: that release has CVE-2024-53861 (partial issuer
  match). If `requirements.txt` still says `PyJWT>=2.8`, change it (command below). Our own exact issuer/audience check
  protects you even then, but do not rely on that alone.
* `argon2-cffi>=23.1` (Argon2id password hashing).
* Dev-only, not in `requirements.txt`: `pip-audit` for the vulnerability scan.

## Configuration added

`configs/app.yaml`: optional `auth`, `storage` (now with `max_files`), `audit` and `deployment` sections;
`secrets.required_in_production` lists `jwt_secret`, and `audit_hmac_key` is required whenever `audit` is present.
`.env.example` names `BANKWISE_JWT_SECRET` and `BANKWISE_AUDIT_HMAC_KEY`.

## Remaining work that needs source files outside this task

These live in the OCR/identity code and scripts, which this security work does not modify. To wire them, run
`git grep -n -E "output_cards|imwrite|write_text|open\(.*[\"']w|json\.dump|--out|--debug|--show-pii" -- src scripts`
and send the matching **file names and line numbers** (not their contents): each hit is either a place that writes
personal data to disk (replace with `safe_output_path` + `write_private_text`, or `SecureFileStore`, with the owner
of that code) or harmless.
