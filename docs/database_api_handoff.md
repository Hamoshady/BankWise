# Database + API handoff — 2026-10-31

Implementation is complete for the approved Database + API milestone, with the operational
limitations below. Changes were transferred into the original `bankwise_ai` checkout on
`feature/database-api-local`, based on inspected local `0ed5167`.
They are intentionally uncommitted at the owner's request. Nothing was pushed or merged.
Existing edits were preserved during transfer, including the RAG implementation and its tests.
README additions were combined. The previous worktree remains available as a recovery copy.

## Delivered

- PostgreSQL/SQLAlchemy users, role associations and identity-attempt metadata.
- Initial Alembic migration, bounded connection pool, explicit transactions and rollback.
- Operator user provisioning, expired-metadata cleanup and interrupted-attempt reconciliation.
- FastAPI lifespan/dependencies, liveness/readiness, login/logout and identity POST/GET.
- Existing JWT/RBAC/lockout/rate limits/audit/OCR integration preserved.
- Ownership checks, bounded in-memory uploads, safe errors and idempotent retry suppression.
- Packaging, CPU Docker image, persistent Compose volumes and deployment/API documentation.

## Final verification

Pre-transfer verification from the feature worktree using Python 3.11.9:

```bash
BANKWISE_TEST_POSTGRES=1 /tmp/bankwise-final-check/bin/python -m pytest -q -rs -p no:cacheprovider --tb=short
```

**687 passed, 3 skipped, 1 warning in 20.13 seconds.** This includes the existing suite
and 62 added tests. PostgreSQL tests create a random-credential, tmpfs-backed disposable
container; no shared database is reset. All test containers were removed by their fixture.
An initialization race discovered in final checking was fixed by waiting for PostgreSQL's
TCP listener, not the temporary socket-only initdb server; Compose uses the same check.

Skips: one real-OCR integration module because EasyOCR is absent in the isolated host test
venv; two opt-in real-model smoke tests. No existing test was deleted, weakened or newly
skipped. Real OCR inference was **not** verified in this milestone environment; weights
were not available. The Docker image does include EasyOCR and CPU PyTorch.

Warning: Starlette TestClient deprecates its httpx adapter in favor of httpx2. This is a
third-party test-client warning; it was reported rather than suppressed.

Additional checks passed:

- Fresh Alembic initialization, upgrade repeatability, downgrade/re-upgrade and model drift check.
- PostgreSQL unique/FK/check constraints and transaction failure/rollback tests.
- Docker image build (`bankwise-ai:database-api`), non-root UID 10001 imports and `pip check`.
- Docker startup with missing OCR weights fails safely without enabling model downloads.
- Compose configuration validation and `git diff --check`.
- Source parsing with Python 3.10 grammar (execution verification is Python 3.11).

## Run after configuring the local environment

See [backend_setup.md](backend_setup.md) for required secrets, PostgreSQL URL, OCR weights,
production single-process declaration, Docker setup and retention operations.

```bash
python -m pip install -e .
python -m alembic upgrade head
python -m bankwise.database.cli create-user --username operator-one --roles identity_processor operator
uvicorn bankwise.api.main:create_app --factory --workers 1 --no-proxy-headers --no-access-log
```

Run provisioning/maintenance while the API is stopped. There are no default credentials.
The API does not automatically source `.env`. Open `/docs` after successful startup.
The full endpoint and error contract is in [api_contract.md](api_contract.md).

## Limits and next milestone

- Complete deployment verification still needs local OCR weights and an opt-in real-OCR run.
- One worker/process only; existing revocation and rate-limit state is lost on restart.
- No OCR cancellation, refresh-token/password-reset or remote account-management workflow.
- File audit and database writes are not atomic; ambiguous attempts require reconciliation.
- The supplied container is CPU-only; the developer's local CUDA environment is untouched.
- Later identity/face/liveness, RAG, banking services, agent and Streamlit UI are separate
  stages in [roadmap.md](roadmap.md); this handoff does not claim they are implemented.
- No further design approval is needed for this completed milestone. Integration/push/merge
  remains deferred; no commits were created at the owner's request.

## Files changed

- `.dockerignore`
- `.env.example`
- `.gitignore`
- `Dockerfile`
- `README.md`
- `alembic.ini`
- `compose.yaml`
- `docs/api_contract.md`
- `docs/backend_setup.md`
- `docs/roadmap.md`
- `docs/security_and_privacy.md`
- `docs/security_integration.md`
- `pyproject.toml`
- `requirements.txt`
- `src/bankwise/api/__init__.py`
- `src/bankwise/api/config.py`
- `src/bankwise/api/deps.py`
- `src/bankwise/api/errors.py`
- `src/bankwise/api/main.py`
- `src/bankwise/api/routers/__init__.py`
- `src/bankwise/api/routers/auth.py`
- `src/bankwise/api/routers/health.py`
- `src/bankwise/api/routers/identity.py`
- `src/bankwise/api/schemas/__init__.py`
- `src/bankwise/api/transport.py`
- `src/bankwise/core/bootstrap.py`
- `src/bankwise/core/errors.py`
- `src/bankwise/core/guard.py`
- `src/bankwise/core/identifiers.py`
- `src/bankwise/database/__init__.py`
- `src/bankwise/database/accounts.py`
- `src/bankwise/database/cli.py`
- `src/bankwise/database/config.py`
- `src/bankwise/database/identity_requests.py`
- `src/bankwise/database/migrations/env.py`
- `src/bankwise/database/migrations/script.py.mako`
- `src/bankwise/database/migrations/versions/0001_accounts_identity_accounts_and_identity_metadata.py`
- `src/bankwise/database/models/__init__.py`
- `src/bankwise/database/session.py`
- `src/bankwise/integration/id_ocr_adapter.py`
- `src/bankwise/integration/identity_processing.py`
- `tests/integration/backend/conftest.py`
- `tests/integration/backend/test_api.py`
- `tests/integration/backend/test_database.py`
- `tests/unit/test_api_transport.py`
- `tests/unit/test_database_config.py`
- `tests/unit/test_identifiers.py`
- `docs/database_api_handoff.md` (this report)

## Verification after transfer to the original checkout

The combined Database/API and existing RAG suite passed on 2026-10-31:
**695 passed, 3 skipped, 1 warning in 19.59 seconds** with disposable PostgreSQL enabled.
The same OCR skips and test-client warning described above remain. All 11 existing RAG
source/test files were verified unchanged byte-for-byte, as were the other unrelated
local modifications. README contains both the original RAG instructions and backend setup.
No commits, pushes or additional feature stages were performed.
