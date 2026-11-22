# Backend setup and operations

## Local setup

Use a Linux Python 3.11 environment for the tested backend path. Python 3.10 syntax remains
supported by the project, but this milestone's execution tests use 3.11. Keep an existing
working CUDA torch/torchvision installation; do not replace it with CPU wheels locally.

```bash
python -m pip install -e .
cp .env.example .env
```

Fill the private `.env` locally (never commit it). Required values:

| Variable | Purpose |
|---|---|
| BANKWISE_ENV | development for local testing; production otherwise (default fails closed) |
| BANKWISE_JWT_SECRET | Independent random signing secret, at least 32 characters |
| BANKWISE_AUDIT_HMAC_KEY | Independent random audit HMAC key, at least 32 characters |
| BANKWISE_DATABASE_URL | `postgresql+psycopg://USER:URL_ENCODED_PASSWORD@HOST:5432/DB` |
| BANKWISE_CONFIG_DIR | Config directory; set explicitly when not running from a source checkout |
| BANKWISE_OCR_MODEL_DIR | Directory containing preinstalled local EasyOCR weights |
| BANKWISE_OCR_USE_GPU | 1 (default) or 0; choose 0 explicitly for CPU |
| BANKWISE_OCR_ALLOW_CPU_FALLBACK | 0 (default) or 1; never silently changes device |

Optional database settings: BANKWISE_DATABASE_POOL_SIZE (default 5, range 1–20),
BANKWISE_DATABASE_POOL_TIMEOUT_SECONDS and BANKWISE_DATABASE_CONNECT_TIMEOUT_SECONDS
(default 5, range 1–60), BANKWISE_DATABASE_RETENTION_DAYS (default 30, range 1–365).
Use URL escaping for reserved characters in database credentials. Configuration errors never
include the submitted connection URL. SQLAlchemy hides SQL parameter values; SQL echo is disabled.

Generate each secret separately with `python -c 'import secrets; print(secrets.token_urlsafe(48))'`.
The API does not automatically load `.env`; export values in the shell (or let Compose load it):

```bash
set -a
. ./.env
set +a
export BANKWISE_CONFIG_DIR="$PWD/configs"
```

Only source a local file you control. No sample user, password or token is embedded in the project.

## Initialize PostgreSQL and provision a user

Create a dedicated empty PostgreSQL database using your deployment tooling. Then:

```bash
python -m alembic upgrade head
python -m alembic current
python -m bankwise.database.cli create-user --username operator-one --roles identity_processor operator
```

The CLI prompts privately for the password and confirmation. It loads valid role names from
the existing RBAC YAML, hashes the password with the existing Argon2id utility, and audits
authorization of creation before committing. The event is `account.create_authorized`, not
a claim that the database transaction committed. Console success appears only after commit.
The CLI is privileged operator tooling, not a remotely callable API. Protect its shell and DB
credentials. **Run all CLI account/maintenance commands with the API stopped**: the audit sink
requires one process/writer. Use database migration credentials for DDL and a restricted runtime
role for SELECT/INSERT/UPDATE/DELETE on the application tables plus SELECT on alembic_version.

Only Alembic manages the schema. Startup never calls create_all, drops tables or runs migrations.
Review each migration and back up persistent data before applying it. `alembic downgrade base`
deletes these tables and is only demonstrated in disposable tests, never normal startup.

## Start the API

Preinstall OCR model weights through the existing OCR tooling before serving traffic.
The API adapter uses download_models=False and debug_dir=None. Missing weights/device errors
prevent startup rather than downloading models or falling back to fake processing.

```bash
uvicorn bankwise.api.main:create_app --factory --host 127.0.0.1 --port 8000 \
  --workers 1 --no-proxy-headers --no-access-log
```

Open `http://127.0.0.1:8000/docs`. Use `/health/live` for liveness and `/health/ready` for
dependency readiness. A database outage or missing/wrong Alembic revision returns generic 503;
it never causes implicit schema creation. PostgreSQL pool/connection timeouts are bounded.
Local OCR initializes once and its readers are released at shutdown; database pools are disposed.

Production also requires the operator to set `deployment.single_process: true` in app.yaml,
strong secrets and a durable audit configuration, as already enforced by build_security.
The checked-in false value is deliberately preserved. Never use multiple workers/replicas:
rate limits, revocations and the audit writer remain process-local. Revoked tokens can become
valid after restart until expiry; this existing limitation is not solved by storing users.
Serve through TLS and configure any trusted proxy deliberately; arbitrary X-Forwarded-For is
not accepted as client identity. The supplied command disables Uvicorn proxy-header trust.

## Docker Compose

The supplied image is explicitly CPU-only. This does not alter the developer's CUDA environment.
Provide POSTGRES_USER, POSTGRES_DB, URL-safe POSTGRES_PASSWORD and the signing/audit secrets
in the private `.env`. Mount preinstalled OCR weights via BANKWISE_DOCKER_MODEL_DIR (default
`./models`). Configure BANKWISE_ENV and the single-process declaration as above.

```bash
docker compose build api
docker compose up -d db
docker compose run --rm api python -m alembic upgrade head
docker compose run --rm api python -m bankwise.database.cli create-user \
  --username operator-one --roles identity_processor operator
docker compose up -d api
```

Stop an existing API before CLI operations. Compose mounts config/models read-only and uses
named PostgreSQL and private-output volumes. PostgreSQL is not published to the host. API
binds to localhost, runs as UID 10001 and uses one worker. Named volumes survive ordinary
`docker compose down`; do not use `down -v` on data you need. Database and audit backups need
access controls and a coordinated restore procedure. Do not treat a backup as permanent retention.

## Data and retention

The schema contains users, user_roles and identity_requests. Usernames are login aliases,
not names, emails or national IDs. Password hashes are access-restricted authentication data.
Account records remain while the account is needed; account deletion/password reset workflows
are not implemented in this milestone. Role permissions remain in rbac.yaml.

Identity records contain only opaque IDs, owner FK, idempotency UUID, status, allowlisted
decision/error code and timestamps. No JSON OCR response, images, personal fields, face
embeddings, image fingerprints or per-field OCR diagnostics are persisted. These metadata
are still access-controlled: API reads require permission plus ownership, DB credentials are
restricted, and remote database connections/backups require appropriate transport/storage
protection. Encryption is not a substitute for this minimization.

Metadata expire after 30 days by default. Expired records are hidden immediately from API
retrieval. Physical deletion is explicit maintenance, not automatic startup behavior:

```bash
# Stop the API first; terminal expired records only.
python -m bankwise.database.cli purge-expired
# Reconcile an abandoned attempt against audit records before marking it interrupted.
python -m bankwise.database.cli interrupt-request REQUEST_UUID
```

Schedule maintenance operationally, with one audit writer. Processing records are excluded
from purge until reconciled, because expiry alone does not prove OCR stopped. Purging removes
the idempotency reservation; clients must never reuse old keys after the retention window.
Audit-file retention follows the existing policy; this command does not delete or rotate audit
events. The file sink remains tamper-evident with its documented limitations, not tamper-proof.

## Testing

```bash
python -m pytest -q -rs
# Creates its OWN temporary PostgreSQL container with random credentials and tmpfs storage.
# It never accepts a real/shared database URL and deletes only its own container.
BANKWISE_TEST_POSTGRES=1 python -m pytest -q -rs
# Separate opt-in real OCR integration (preinstalled EasyOCR, torch and weights required):
RUN_REAL_OCR=1 ID_OCR_MODEL_DIR=/path/to/models python -m pytest tests/integration/test_real_ocr_pipeline.py -q -rs
```

Backend tests use real PostgreSQL, migrations, credentials/JWTs, RBAC, audit, image decoding
and processing orchestration. A stand-in pipeline replaces inference for deterministic API
tests; that does not establish OCR accuracy. Other tests cover concurrent retries, rollback,
ownership, limits, redaction, malformed uploads and audit/dependency failures.

## Implementation references

Session boundaries follow the [SQLAlchemy session/transaction documentation](https://docs.sqlalchemy.org/en/20/orm/session_basics.html).
The upload implementation deliberately parses bounded bytes in memory: ordinary multipart
file handling can spool uploads, as documented in [Starlette requests](https://starlette.dev/requests/).

### Existing database with unrelated legacy tables

Set `BANKWISE_DATABASE_SCHEMA` to an explicitly provisioned isolated schema (for example
`bankwise_api`), owned by a dedicated database login. The default remains `public` for
compatible installations. Never stamp or run the initial migration over conflicting
legacy tables. The configured schema must exist before migration; no public fallback or
automatic schema creation occurs. See [local workstation setup](local_api.md) for the
non-destructive setup used on this machine and the launcher commands.
