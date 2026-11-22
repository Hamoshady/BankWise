# RAG integration handoff — 2026-10-31

Original checkout: `bankwise_ai`, branch `feature/database-api-local`.
No commits, staging, push or later project phases performed. The initial RAG tests did
not modify the workstation database; subsequent local setup is recorded below.

Implemented owner-private PostgreSQL storage (migration `0002_rag`), atomic replacement,
list/delete/query endpoints, existing JWT/RBAC/rate/audit integration, bounded inputs and
corpus size, known PII/secret pattern rejection, rollback and safe errors. The existing
standalone SQLite interface remains compatible. API retrieval uses hashing-v1-384 by
default and extractive citations, including corrected reuse of citation numbers and
abstention when no sentence has lexical overlap. Migration `0004_dynamic_embeddings`
stores the model on the document and the vector dimension on each chunk, so another
embedder can be persisted without a fixed-384 constraint. See `api_contract.md` for
startup and example requests.

Changed RAG integration files:
- `src/bankwise/rag/{store,postgres_store,service,retriever,generator}.py`
- `src/bankwise/database/models/{__init__,rag}.py`
- `src/bankwise/database/migrations/versions/0002_rag.py`, `database/session.py`
- `src/bankwise/api/{main.py,routers/rag.py}`
- `configs/{app,rbac}.yaml`
- `tests/integration/backend/test_rag_api.py`, `tests/unit/test_rag.py`
- README and API, roadmap, security/privacy and this handoff documentation.

Validation on a disposable PostgreSQL 16 Docker container (never the existing service):

```bash
BANKWISE_TEST_POSTGRES=1 /tmp/bankwise-rag-check/bin/python -m pytest -q -rs --tb=short -p no:cacheprovider
```

Actual result: **716 passed, 3 skipped, 1 warning in 23.76s**. Includes all 70 backend
integration tests, of which 20 test the RAG API. Migration checks verify upgrade/downgrade
and metadata consistency. New tests cover real retrieval, Arabic text, ownership, revoked
tokens, RBAC before embedding, limits, audit/database rollback, concurrent replacement,
vector constraints, sensitive patterns and citation reuse.

Three skips relate to real OCR: EasyOCR is absent in this isolated test environment and
two model smoke tests require explicit opt-in. One warning is the existing Starlette
TestClient/httpx deprecation. Real OCR inference was not validated in this run.
`git -c core.whitespace=cr-at-eol diff --check` passed; existing CRLF files were preserved.

Limitations: no trained semantic model in the API, no LLM generation, no PDF API uploads,
no measured banking-domain retrieval benchmark. This is a functional, tested extractive
RAG integration, not a claim of production semantic accuracy. The existing API still
needs local OCR model assets at startup. No real/shared database was migrated during
this work; operators must run `python -m alembic upgrade head` on the configured database.


## Subsequent local startup repair

The existing database's public schema is an incompatible legacy implementation, so
local deployment now uses an isolated `bankwise_api` schema in the **same** database,
with a dedicated non-superuser login. No legacy data was migrated, renamed or deleted.
Database schema selection is environment-backed and tested; Alembic and readiness
fail safely when it is absent. Added `scripts/local_api.py` and `docs/local_api.md`
for restarting and maintaining the Docker service. Secrets are stored outside Git in
owner-only local files. Local CPU OCR weights are provisioned explicitly; serving
does not download them. The live API was verified with login, ingestion and an Arabic
RAG question returning a citation. See `local_api.md` for the current setup and commands.

Final regression run after schema support and offline migration coverage:
`BANKWISE_TEST_POSTGRES=1 /tmp/bankwise-rag-check/bin/python -m pytest -q -rs --tb=short -p no:cacheprovider`
→ **727 passed, 3 skipped, 1 warning in 29.01s**. Skips and warning are the same
real-OCR opt-ins/missing EasyOCR in the isolated host test environment and Starlette
TestClient/httpx deprecation described above. The serving Docker image does have
EasyOCR and completed local reader warmup. Original branch: `feature/database-api-local`.

## Arabic web UI

Added an Arabic RTL workspace at `/`, served from packaged static assets by the existing
API. Login, policy PUT/list/delete, queries and logout use the same security gates.
Tokens remain in page memory, content is rendered with textContent, and a restrictive
CSP permits only same-origin assets/requests. Existing policy replacement is explicit
full-text replacement with confirmation, not retrieval of an editable original.

Validation: 728 passed, 3 skipped, 1 warning in 24.24s, including the UI shell/security
headers test. Browser inspection verified the Arabic login page, and live API health,
login, Arabic retrieval and unauthorized rejection were checked after deployment.
A live test's assumption that savings-demo must rank first was corrected to check citation
source membership in the authenticated user library: the user's other stored policies may also match the question.
Docker was rebuilt; the prior stopped container is retained as bankwise-api-before-gui.
No schema migration, commit or push was performed for the UI.
