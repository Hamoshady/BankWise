# Database and API milestone

The implemented API supports accounts, local identity OCR and private extractive RAG. Chat, face matching,
liveness, complaints, loans, finance, fraud and the Streamlit UI remain later milestones.
No endpoint claims those features are implemented. See [backend_setup.md](backend_setup.md)
for setup, provisioning, migrations, Docker, retention and tests.

## Authentication and permissions

Protected endpoints accept `Authorization: Bearer <access-token>`. Login uses the existing
Argon2 utilities and `SecurityService.issue_token`; token verification/revocation remains
in `TokenService`. No public registration, default account, refresh token, password reset
or role-administration endpoint is provided. A local operator provisions accounts using
the CLI while the API is stopped. Never accept roles or owner IDs from a client.

Login accepts JSON `{ "username": "operator-name", "password": "<password>" }`.
Usernames are NFKC-normalized, trimmed, lowercased, and restricted to 3–64 ASCII letters,
digits, dot, underscore or hyphen (first character alphanumeric). Passwords are never
stored in plaintext; provisioning enforces the existing 12–256-character password policy.
Login allows 1–256 characters so wrong credentials receive a generic authentication error.
Unknown JSON properties are rejected. Failed/unknown/inactive accounts have the same
public authentication error; unknown accounts perform dummy Argon2 verification.

Login attempts consume `auth_attempt` budgets per peer address and normalized username
(only an opaque username digest is used as a limiter key). The client budget is shared with
failed bearer-token authentication. Existing token lockout, expiry, issuer, audience,
signature, revocation and RBAC rules remain effective. Every authenticated API operation
also verifies that the user exists, is active, and still has exactly the token's roles.
Account deactivation/role changes therefore reject old tokens without waiting for expiry.

`identity:process` remains exclusive to `identity_processor`. GET needs `identity:read`
and ownership. To submit and retrieve, provision both `identity_processor` and `operator`.
Admin is not a bypass. No endpoint exposes `result.sensitive` or calls `reveal_sensitive`.

## Endpoints

| Method/path | Input/permission | Success |
|---|---|---|
| GET `/health/live` | Public | 200 `{"status":"live"}` |
| GET `/health/ready` | Public | 200 `{"status":"ready"}`; 503 for unavailable/outdated DB or uninitialized services |
| POST `/api/v1/auth/login` | JSON username/password | 200 access_token, token_type=`bearer`, expires_in (seconds) |
| POST `/api/v1/auth/logout` | Bearer token | 204, token revoked in the existing process-local store |
| POST `/api/v1/identity/verify` | Bearer, `identity:process`, UUID `Idempotency-Key`, multipart front/back | 201 first completed attempt; 200 completed replay; `Location` points to GET |
| GET `/api/v1/identity/requests/{request_id}` | UUID, Bearer, `identity:read`, ownership | 200 minimal request metadata |

Interactive documentation is at `/docs`; the schema is `/openapi.json`. Configure tokens
using its Authorize control after login. Never share screenshots containing tokens.

Identity responses have exactly these fields:

```json
{
  "request_id": "3b440c10-dfb3-45a5-bf6e-84018f73c752",
  "status": "completed",
  "decision": "MANUAL_REVIEW",
  "error_code": null,
  "created_at": "2026-10-30T10:00:00Z",
  "completed_at": "2026-10-30T10:00:02Z",
  "expires_at": "2026-11-29T10:00:00Z",
  "notice": "OCR extraction only. This is not proof that the card is genuine or that the person presenting it is the cardholder."
}
```

`status` is processing/completed/failed/interrupted. `decision` is nullable and restricted
to the existing OCR decisions VERIFIED/REJECTED/RETRY/MANUAL_REVIEW. A completed request
means processing finished, not that the document or presenter was authenticated. GET can
return a failed/interrupted state with its safe error code. Missing, expired and other-owner
resources all return the same 404. Their read denials are audited without revealing ownership.

## Upload contract and synchronous processing

Both parts must be named exactly `front` and `back`, with content type `image/jpeg` or
`image/png`. Extra/duplicate parts and malformed/truncated multipart are rejected. Filenames
are ignored and never used as paths. Images stay in memory; no temporary-file spooling,
database binary storage or debug image output is used.

The configured default is 5 MiB per image, 25 million pixels and a minimum side of 100 pixels.
The transport body limit is twice `max_image_bytes` plus 16 KiB for multipart framing;
it counts actual received bytes, including chunked bodies without Content-Length. Multipart
headers are bounded to four per part and 2 KiB each. Other request bodies are limited to
16 KiB. Eight in-flight HTTP requests are allowed before transport backpressure returns 429.

After bounded body reception, `IdentityProcessingService.process_upload` runs the existing
single security gate, reserves a request through a trusted observer, parses multipart,
and performs existing image checks/concurrency gating before calling the local adapter.
SQL/OCR work runs outside the async event loop. The older `process(...)` interface remains
compatible and delegates to the same workflow. Successful processing must be audited before
the observer may persist its decision. Raw OCR never reaches the observer.

The OCR pipeline also has its own quality/input rules; passing the header checks does not
guarantee that decoding or OCR succeeds. No background queue or OCR cancellation is introduced.

## Idempotency and failures

Use a fresh random UUID `Idempotency-Key` for each new attempt, and reuse it only for a retry
of that attempt. The key is unique per authenticated owner. It is not an authentication
credential and cannot select another user's request. Request IDs are always server-generated;
client `X-Request-ID` is ignored. `X-Request-ID` on responses identifies the current HTTP
exchange; a replay's body/Location identifies the original stored attempt.

A short committed reservation precedes OCR. No connection/transaction remains held by the
reservation during inference. Concurrent retries of a processing request return 409. A completed
retry returns the stored metadata without processing replacement images. Failed attempts replay
their safe failure category without rerunning OCR. A per-image 413 is stored as validation_failed;
its replay returns generic 422. Attempts rejected before reservation do not reserve a key.
All requests, including replays, still pass authentication, authorization and the processing rate limit.
Cached success and GET disclosures require a new audit record before release.

A crash or uncertain database commit can leave `processing` metadata even if OCR already ran.
Do not automatically rerun it. Stop the API, reconcile its request ID with the durable audit
file, and use the operator interrupt command where appropriate. An interrupted request returns
409 on POST replay and is visible through authorized GET. A genuinely new capture uses a new key.
This is retry suppression within the retention window, not exactly-once execution.

If the database fails, no successful response is returned. The audit file and PostgreSQL are
not one atomic transaction: an audit record can exist without a corresponding completed row.
Recovery never interprets that record alone as a saved result.

## Errors

```json
{"error":{"code":"permission_denied","message":"You do not have permission to perform this action."},"request_id":"<server-generated-uuid>"}
```

| Status | Meaning |
|---|---|
| 401 | Missing/invalid/expired/revoked token or invalid credentials; includes WWW-Authenticate |
| 403 | Missing permission |
| 404 | Resource absent, expired or not owned |
| 409 | Existing attempt processing/interrupted, or expired key still reserved |
| 413 | Body/image exceeds the allowed transport size; code is validation_failed |
| 422 | Schema, UUID, multipart or image validation failed |
| 429 | Authentication/processing/read budget or concurrency limit reached; Retry-After when available |
| 500 | OCR/internal/audit failure; generic safe message |
| 503 | Database/dependency unavailable |

Errors never echo Pydantic inputs, passwords, tokens, SQL, connection strings, raw exception
messages or tracebacks. Responses carry Cache-Control: no-store, nosniff and no-referrer.
CORS is disabled; configure an explicit allow-list only when a browser frontend needs it.

## Example authenticated submission

After login, keep the token in a private shell variable `BANKWISE_ACCESS_TOKEN` (do not print it):

```bash
curl --fail-with-body http://127.0.0.1:8000/api/v1/identity/verify \
  -H "Authorization: Bearer $BANKWISE_ACCESS_TOKEN" \
  -H "Idempotency-Key: $(python -c 'import uuid; print(uuid.uuid4())')" \
  -F 'front=@front.png;type=image/png' -F 'back=@back.png;type=image/png'
```

For manual login without placing a password in shell history, use `/docs` or an interactive
client that reads it with `getpass`. Use synthetic card data for examples and tests.

## Private knowledge / RAG

All routes use verified bearer tokens and the existing security service, including
revocation, account checks, RBAC, audit and rate limits. Provision the explicit
`knowledge_user` role (`rag:read`, `rag:write`); existing roles gain no permission.
The authenticated account determines ownership. An owner identifier is never accepted
in the body. A source is a case-sensitive label matching `[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}`,
not a local path or URL. The same source label may exist independently for different users.

| Method and path | Permission | Body / response |
|---|---|---|
| `PUT /api/v1/rag/documents/{source}` | `rag:write` | `{"text":"Savings interest is paid monthly."}` → 200 `{id,source,chunks}` |
| `GET /api/v1/rag/documents` | `rag:read` | 200 array of `{id,source}` for this account only |
| `DELETE /api/v1/rag/documents/{source}` | `rag:write` | 204; 404 for missing or another account's source |
| `POST /api/v1/rag/query` | `rag:read` | `{"query":"When is savings interest paid?","top_k":5}` → 200 `{answer,citations,mode:"extractive"}` |

Each citation includes `source`, `chunk_id`, `score` (hybrid ranking score, not confidence)
and `excerpt`. No matching evidence produces an explicit abstention and empty citations.
Answers quote source sentences; consumers must render them as plain text, never HTML.
Documents cannot execute instructions or invoke tools. This is not a chat/agent endpoint.

Limits: 16 KiB actual HTTP body (including JSON encoding), 8,000 document characters,
1,000 query characters, top_k 1–10, 100 documents / 1,000 chunks per account, 100 chunks
per document, 1,000 characters per chunk. JSON whitespace/escaping can reach the byte
limit before the character limit. Rate limits per account: 10 writes and 30 reads per
60 seconds. Unexpected fields and blank inputs are rejected. The API accepts UTF-8
text/Markdown as JSON text; it does not upload PDF, read server files or fetch URLs.

Repeated PUT replaces the same source atomically, retaining its document id. A lost
response can be retried with the same source/text without duplicate documents; retries
still consume rate budget and write an audit authorization event. Concurrent mutations
for the same owner are serialized. Query bodies and generated answers are not persisted.
401/403/404/409/413/422/429/500/503 use the existing sanitized error envelope; 409 means
corpus quota exceeded. Audit failure prevents disclosure and rolls back writes. Database
failure returns 503 and rolls back. Audit events `rag.write_authorized` and
`rag.delete_authorized` precede commit and attest authorization, **not** successful commit.
`rag.list` and `rag.query` precede response disclosure. No document content or question is
written to audit events.

Storage: migration `0002_rag` adds `rag_documents` (opaque id, owner FK, source, model id,
updated time) and `rag_document_chunks` (document FK, deterministic chunk id, text, vector);
migration `0004_dynamic_embeddings` adds the per-chunk `dimension` and checks it against
the stored array length.
Only authorized knowledge text is stored; no connection to OCR output, ID images or
biometrics exists. Known ID/secret patterns are rejected using existing PII utilities,
and answers/excerpts are redacted defensively. These patterns do not detect names or
addresses: upload only approved non-personal knowledge documents. Chunks and vectors are
retained until source deletion/replacement or account deletion; deletion cascades, but
backup retention remains an operator responsibility. PostgreSQL volume encryption,
backups and database access controls remain deployment responsibilities.

The default API embedding model is the deterministic `hashing-v1-384`, with a cosine
cutoff of 0.15 and lexical-overlap sentence selection. It makes no cloud calls and
downloads no models. Migration `0004_dynamic_embeddings` records each document's model
and each chunk's vector dimension, so a different embedder can be stored without a
schema change; one owner must still use one model/dimension at a time. SQLite remains
available only for the existing standalone RAG interface; the API exclusively uses
PostgreSQL. This bounded corpus implementation scans vectors in-process; it is not a
large-scale semantic index. PDF API uploads and a measured Arabic banking quality
benchmark are not included. Optional trained embeddings and local LLM evidence
selection are described in `semantic_rag.md`.

### Try it

With secrets/database configured as in `backend_setup.md`, run from the repository:

```bash
python -m alembic upgrade head
# Stop the API during provisioning: the audit file has a single writer.
python -m bankwise.database.cli create-user --username knowledge-one --roles knowledge_user
uvicorn bankwise.api.main:create_app --factory --workers 1 --no-proxy-headers --no-access-log
```

The shared API startup still requires the existing local OCR models even when testing
only RAG. Do not bypass that startup check. Open `http://127.0.0.1:8000/docs`, call
`POST /api/v1/auth/login` with the provisioned credentials, then use **Authorize** with
the returned access token. Call PUT above with source `savings-policy`, then query.
Finally list documents and delete `savings-policy` to verify the complete workflow.
No production migration is applied automatically; `upgrade head` preserves account and
identity tables. Startup now calls `Database.ready()` and fails closed when the schema is
missing or not at the Alembic head, so an outdated database cannot serve traffic.
The expected revision is read from the migration scripts instead of a manual constant.
Test migrations only on a disposable database; downgrade removes the
new knowledge tables and their contents.

## Shared customer policies and identity API

Migration `0003_shared_policies` adds `shared` (false by default) and `content_hash`
to knowledge documents without converting or deleting private documents; migration
`0004_dynamic_embeddings` adds per-chunk vector dimensions and relaxes the fixed
`hashing-v1-384` model constraint. The operator imports `knowledge/policies/*.md` with
`python -m bankwise.rag.import_policies DIRECTORY` while the API is stopped. An inactive,
roleless publisher account owns these policies; there is no HTTP publish permission.
Imports are serialized and transactional, audited before commit, and skip unchanged
content. Use `--force` after a chunker or embedding-model change, and `--prune` to
delete shared policies whose files were removed. Pruning is never implicit: an empty
directory is rejected before any deletion.

Principals with role `customer` and no knowledge role query only the shared library.
Existing knowledge_user accounts retain private-query behavior for backward
compatibility, and a principal holding both `customer` and `knowledge_user` keeps the
private scope. `customer` grants rag:read only. Identity processing still requires
identity_processor and retrieval requires identity:read (e.g. operator). Shared policy
contents never contain client transactions or OCR output. Sample policies are
explicitly drafts awaiting approval.

The customer website has been removed. `/` and `/ui/*` return 404; `/docs` remains
API documentation. Front/back uploads continue through `/api/v1/identity/verify`
with the same authentication, permissions and idempotency requirements. OCR is not
proof of ownership; face matching/liveness remain absent.

Arabic matching now uses normalized banking aliases and corpus-weighted lexical/hash
ranking; see [Arabic retrieval details](arabic_rag.md). The 0.15 cutoff applies to the
hybrid score, not pure cosine. Source excerpts remain unchanged.
