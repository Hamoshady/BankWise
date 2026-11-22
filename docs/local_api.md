# Local API using the existing PostgreSQL server

This Linux workstation already has `bankwise-postgres` on localhost port 54329. Its
`public` tables belong to a different implementation. Do not run the new migrations
against `public`, reset that database or use `alembic stamp` to pretend it is compatible.

The current application uses the **same database** with an isolated `bankwise_api`
schema and a dedicated, non-superuser `bankwise_api_runtime` login. Existing tables
remain untouched. This is coexistence, not conversion of old data: the new service does
not consume legacy audit, revocation, image or identity tables. Phase 4 security remains
in effect through the current application services (single process and file audit).
There were no legacy users to transfer at setup time.

`BANKWISE_DATABASE_SCHEMA` defaults to `public` for existing supported installations.
It accepts only non-reserved lowercase SQL identifiers; connection search_path includes
only that schema. A missing schema fails readiness/migration checks instead of falling
back to legacy tables. Provision the schema before applying Alembic; startup never
creates schemas or runs migrations. Both online and offline migrations respect the
configured schema. PostgreSQL's implicit pg_catalog lookup remains enabled.

On this workstation, local secrets live outside the source tree in the owner-only file
`~/.config/bankwise_ai/local-api.env`. Never paste it into chat or commit it. The API is
bound to `127.0.0.1:8000`, uses Docker host networking to reach the existing localhost
PostgreSQL port, and runs in development mode for local testing. It is not a production
or multi-worker deployment. The obsolete Windows `.venv` was removed; this setup uses Docker dependencies.

From the original repository, with a working Python 3 interpreter and Docker, everyday
startup is one command:

```bash
python3 scripts/local_api.py up
```

`up` starts the PostgreSQL container, rebuilds `bankwise-ai:semantic` only when the
source or configs are newer than the image, applies Alembic migrations, recreates
`bankwise-api-local` only when its image or runtime configuration changed, and waits for
`/health/ready`. When everything is current it returns in about a second without
touching the running container. `start` is kept as an alias of `up`, so older notes
cannot accidentally launch a stale image.

Other commands:

```bash
python3 scripts/local_api.py status
python3 scripts/local_api.py logs
python3 scripts/local_api.py stop
python3 scripts/local_api.py up --build      # force an image rebuild
python3 scripts/local_api.py up --migrate    # force migrate + container recreation
python3 scripts/local_api.py up --timeout 600
python3 scripts/local_api.py import-policies # re-index knowledge/policies/*.md
python3 scripts/local_api.py import-policies --force  # re-index unchanged files too
python3 scripts/local_api.py import-policies --prune  # delete policies whose files are gone
```

`import-policies` stops the API, imports the Markdown files, and starts it again. Run it
after every edit under `knowledge/policies/`; the API does not reload policy files by
itself, so otherwise it keeps answering from the previous import.

On a machine whose pyenv default is unset, use
`/home/shady-mohamed/.pyenv/versions/3.11.9/bin/python` instead of `python3`.
The launcher does not fetch models, create a database, or provision users implicitly.
The restart policy is `unless-stopped`.

Account provisioning still requires a stopped API (the audit file has one writer):

```bash
python3 scripts/local_api.py stop
python3 scripts/local_api.py create-user --username another-user --roles knowledge_user
python3 scripts/local_api.py up
```

`create-user` prompts for the password. Existing accounts cannot self-assign roles.
Local bootstrap credentials, when provisioned, are stored in
`~/.config/bankwise_ai/rag-login.json` with owner-only permissions. Use its JSON body in
`POST /api/v1/auth/login`, then copy the returned access token to Swagger **Authorize**.

Open `http://127.0.0.1:8000/docs`; verify readiness at `/health/ready`. Add text using
`PUT /api/v1/rag/documents/savings-policy`, then ask using `POST /api/v1/rag/query`.
The [API contract](api_contract.md#private-knowledge--rag) contains example JSON and limits.

Docker volumes `bankwise-api-models` and `bankwise-api-outputs` retain preinstalled local
OCR weights and private audit/storage outputs. Models are downloaded explicitly during
setup using EasyOCR's own loader/checks; serving runs offline with model downloads
disabled and the model volume read-only. No cloud OCR or alternate reader is introduced.
Do not delete these volumes as a routine restart step. The existing PostgreSQL volume
continues to hold both the old public tables and the isolated new schema.

Source and config changes are handled by `scripts/local_api.py up`: it rebuilds the image
when needed and recreates the container, because starting an existing container keeps
its old image. The semantic image includes a snapshot of the original configs, so config
changes follow the same rebuild path. This avoids startup failures when the project drive
is not mounted. The manual equivalent, only for troubleshooting, is
`docker build -f Dockerfile.semantic -t bankwise-ai:semantic .` followed by
`python3 scripts/local_api.py up --migrate`.

## Verified workstation state

Prepared on 2026-10-31; schema `bankwise_api` is now at Alembic revision
`0004_dynamic_embeddings` (it started at `0002_rag`).
The original public-table row counts were verified unchanged after setup. The runtime
login cannot SELECT from `public.id_card_samples`. No legacy table was renamed or reset.
The local login is `knowledge-one` with only `knowledge_user` permissions; its random
password is in the owner-only login JSON noted above. A non-personal `savings-demo`
document was retained for trying the Arabic question `حساب التوفير`.

Live HTTP checks passed: `/docs`, `/health/live`, `/health/ready`, login, authenticated
PUT ingestion, Arabic query with a citation, unauthenticated query rejection (401), and
logout (204). The temporary test token was revoked. Local OCR reader initialization
completed using the prepared weights; this does not claim real ID-image accuracy testing.

## API-only operation (current)

The customer website and its assets have been removed. `/` and `/ui/*` return 404;
this is expected and does not indicate a failed server. `/docs` remains optional
interactive API documentation. Authentication, OCR, shared policies and semantic
RAG are unchanged; no database migration is needed for this removal.

From the original repository:

```bash
python3 scripts/local_api.py up
python3 scripts/ask_rag_api.py
```

Wait for readiness during local model loading (the launcher already does this). The
terminal client prompts for the
existing username/password (password hidden), retains the token only in memory,
prints answers, mode and citation excerpts, and revokes the token on exit. Enter
`exit` or press Ctrl-C to finish. It calls the same secured API, without model or
security duplication. Expected semantic mode: `semantic_grounded`.

Try `إيه شروط قفل الحساب؟`, `رسوم التحويل كام؟`, then an unrelated question.
Missing fees must not become invented numbers. Current shared policies are drafts.
The local customer account is `knowledge-one`; its password remains in the private
file described above. Do not provision a replacement merely to test the client.

OCR remains available at `POST /api/v1/identity/verify` with multipart `front` and
`back`, a bearer token, and a UUID `Idempotency-Key`; see [API contract](api_contract.md).
In `/docs`, log in, put `access_token` in Authorize, and upload both files to that
endpoint. It returns minimal status metadata, not raw OCR. No customer website is needed.

Policy updates still use the offline [shared-policy importer](../knowledge/README.md).
The current semantic deployment uses revision `0004_dynamic_embeddings` and customer,
identity_processor and operator roles for the local account; the earlier setup notes
above describe historical provisioning, not current roles.

## Direct local RAG without login

Run `python3 scripts/ask_rag.py` from the repository. No login, token, API server,
PostgreSQL, or OCR is required. The prepared semantic Docker image and named model
volume must already exist. It runs offline with no network and read-only mounts for
models and `knowledge/policies/*.md`. Only a temporary in-memory index is built using
the existing standalone RAG store; nothing is written to the API database. The same
E5/Qwen runtime selects evidence and returns sources. Edit local policy files and
restart the command to see changes; API users still need the separate policy import.

Enter `exit` to finish. Model loading takes time on each invocation. Stop the API with
`python3 scripts/local_api.py stop` first if only testing standalone RAG, to avoid
holding two copies of the models in RAM. No automatic service stop occurs.
This local operator tool reads local files, not private customer documents. API auth,
RBAC and audit remain unchanged; the standalone session is not an audited API session.
The optional authenticated API client is now `python3 scripts/ask_rag_api.py`.

## Banking Demo activated — 2026-11-07

The local `bankwise_api` schema has been upgraded to `0005_assistant` and
`BANKWISE_BANKING_MODE=demo` is enabled in the private local environment file.
`bankwise-api-local` is running with the existing semantic RAG models and restart
policy `unless-stopped`. The semantic Docker build now installs the current main
requirements as well as semantic dependencies, including LangGraph and ReportLab.

Two new customer-only accounts were provisioned with simulated approved KYC:
`demo_alice` (10,000 EGP) and `demo_bob` (1,000 EGP). Passwords and account UUIDs
are stored in `~/.config/bankwise_ai/demo-logins.json` with mode 0600. Existing
users were not modified. A pre-migration schema backup is stored privately at
`~/.config/bankwise_ai/before-demo-20261107.sql`.

From the project directory, use the verified system Python (no pyenv setup needed):

```bash
/usr/bin/python3 scripts/ask_assistant_api.py
```

Live checks passed for both logins and balances, a transfer proposal, demo OTP
retrieval, cancellation without balance changes, and a semantic policy answer
with a citation. Test tokens were revoked. The API is ready at
`http://127.0.0.1:8000/docs`; there is no website at `/`.
See [assistant walkthrough](assistant.md) for confirmation and tool commands.
