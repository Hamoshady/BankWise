# Bankwise AI implementation roadmap

The repository, not the original directory checklist, is the source of truth.
Keep the current local `id_ocr` implementation; do not restore cloud/VLM readers.
This roadmap describes planned work, not implemented placeholder features.

| Phase | Scope | State |
|---|---|---|
| Existing foundation | Config, PII protection, JWT/RBAC, audit, rate limits, image checks, local OCR adapter | Existing Phase 4 |
| Database + API | PostgreSQL/Alembic, users/login, role associations, health, identity request persistence and retrieval | Transferred to original checkout on feature/database-api-local; uncommitted |
| Identity completion | Face comparison, liveness, combined decision criteria and evaluation | Planned; select local models/data and approve biometric handling before implementation |
| RAG | Document ingestion, chunking, embeddings, retrieval, grounded answers and source citations | Implemented: private/shared PostgreSQL knowledge, local multilingual E5 semantic retrieval, Qwen evidence selection with citations, and standalone no-login policy CLI. Policy approval and broader Arabic acceptance evaluation remain |
| Banking services | Complaint tickets, loan calculations, financial categorization/analysis, fraud indicators | Planned; define contracts/data and evaluate each module independently |
| Agent | Tools around implemented services, orchestration, conversation state and guardrails | Planned; cannot bypass backend permissions or turn model output into privileged actions |
| Interface | Terminal/API operation | Customer website removed at owner request; standalone RAG CLI needs no login. API authentication remains |
| End-to-end delivery | Integration/evaluation, deployment, model cards and presentation | Planned; tests accompany every preceding phase |

The one-month target requires prioritizing usable end-to-end features and agreeing measurable
acceptance criteria. In particular, biometric/fraud performance cannot be claimed merely from
creating modules. Evaluate with suitable authorized datasets and document limitations.

The Database + API milestone intentionally excludes queues, multi-worker shared security
storage, public registration, password recovery, raw OCR/biometric persistence, future-domain
tables and frontend implementation. Follow-on work extends the current interfaces and migrations.

## OCR and RAG integration boundary

Both services already share the FastAPI application and its security lifecycle:
`/api/v1/identity/verify` processes front/back images using Phase 4, while
`/api/v1/rag/query` answers policy questions from authorized knowledge. General policy
questions do not require identity capture. The standalone `scripts/ask_rag.py` reads
local policy files without an API account, database or OCR dependency.

Do not index card images, national IDs, raw OCR output or biometric data in the policy
library, or include them in the LLM prompt. OCR reads/validates a document; it is not
proof that the caller owns it. Face matching/liveness remain planned.

If a future banking operation requires verified identity, its backend service must
check server-side identity state for the authenticated owner, expiry and the approved
verification criteria before acting. An agent may coordinate the service calls but
must not decide permissions from LLM text or a client-provided success flag. This
financial-operation integration is not implemented and is not required to answer
shared policy questions. No new OCR-to-RAG payload coupling is needed for this milestone.
