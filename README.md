# BankWise AI — Research Banking Assistant

CIB official-source knowledge, PKDD99 historical banking data, and an isolated EGP simulation.
See [real-data setup and limitations](docs/real_banking_data.md) for the current workspace.

﻿# BankWise AI - Egyptian National ID OCR

Local OCR pipeline for Egyptian national ID cards using EasyOCR.

## Tests
python -m pytest -q

## Run
python -m id_ocr.cli run --front front.jpg --back back.jpg

## Privacy
Terminal output is redacted by default. Treat unredacted JSON and ID images as sensitive data.

## Local RAG

The `bankwise.rag` package provides a local document-to-answer pipeline:

1. Parse UTF-8 `.txt`/Markdown files (PDF is supported when the optional `pypdf` package is installed).
2. Split documents into overlapping chunks and create local embeddings.
3. Store chunks and vectors in a private SQLite database.
4. Retrieve relevant chunks and return extractive answers with source citations.

```python
from pathlib import Path

from bankwise.rag import RAGService, SQLiteVectorStore

with SQLiteVectorStore(Path("outputs/rag.sqlite3")) as store:
    rag = RAGService(store)
    rag.ingest_file(Path("data/bank_policy.md"))
    result = rag.answer("When is interest paid?")
    print(result.answer)
    print(result.citations)
```

`HashingEmbedder` is dependency-free and suitable for wiring/testing the pipeline, not production
semantic search. For local semantic embeddings, install `sentence-transformers`, download the model
ahead of time, and inject `SentenceTransformerEmbedder` into `RAGService`. The extractive generator
only quotes retrieved document text; replace it with a trusted generator implementation when an
LLM-backed answer is required. Re-ingesting a source atomically replaces its indexed chunks; use
`delete_source(source)` to remove them.



## Database and REST API

The backend adds PostgreSQL/Alembic persistence, login/logout, health checks, and
owner-scoped identity requests through the existing security and local OCR services.
See [backend setup](docs/backend_setup.md) for environment variables, migrations,
user provisioning, Docker and retention; see [API contract](docs/api_contract.md)
for endpoints, schemas and errors. The [roadmap](docs/roadmap.md) tracks later phases.

After configuring secrets, PostgreSQL and preinstalled OCR models:

```bash
python -m pip install -e .
python -m alembic upgrade head
python -m bankwise.database.cli create-user --username operator-one --roles identity_processor operator
uvicorn bankwise.api.main:create_app --factory --workers 1 --no-proxy-headers --no-access-log
```

Run CLI provisioning/maintenance with the API stopped (one audit writer). No default
users or credentials exist. Startup never creates or migrates tables. Interactive
API documentation: `http://127.0.0.1:8000/docs`. The customer website has been removed; operation is API/terminal-only.

Backend integration tests use a disposable PostgreSQL container:

```bash
BANKWISE_TEST_POSTGRES=1 python -m pytest -q -rs
```

Private RAG is available through `/api/v1/rag` for the explicit `knowledge_user`
role. It stores owner-scoped knowledge chunks in PostgreSQL and returns extractive
answers with citations. Run `alembic upgrade head` (latest revision:
`0005_assistant`) first.
See the [RAG contract and try-it steps](docs/api_contract.md#private-knowledge--rag)
for endpoints, limits, retention and the current retrieval-quality limitations.

For the already prepared Linux workstation and its existing PostgreSQL server, everyday
startup is one command:

```bash
python3 scripts/local_api.py up
```

It starts PostgreSQL, rebuilds the image only when source/configs changed, applies
migrations, recreates the container only when needed, and waits for `/health/ready`.
See [local API startup](docs/local_api.md) for the other commands. This uses an isolated
schema inside the existing database and the prepared Docker image; dependencies installed inside Docker. The obsolete Windows `.venv` was removed.

Local pretrained semantic RAG is available as an optional deployment using multilingual
E5 and Qwen evidence selection. See [semantic RAG](docs/semantic_rag.md) for the offline
setup, grounding contract, measured tests, resource requirements and limitations.

For direct local RAG without login on the prepared workstation:

```bash
python3 scripts/ask_rag.py
```

Reads local `knowledge/policies/*.md` with the pretrained semantic model and LLM,
without an API server or database. Enter `exit` to finish. Restart after editing policies.
The existing API remains authenticated; its optional client is `scripts/ask_rag_api.py`.
See [local operation](docs/local_api.md#direct-local-rag-without-login).

The new banking assistant uses a LangGraph supervisor with Info, Transfer, Loan and
Account nodes. RAG is a cited knowledge tool within this workflow. The existing RAG
endpoints remain available for retrieval-only clients. Banking is an isolated local
demo, disabled by default, with persisted confirmation gates and simulated OTPs.
See [assistant setup, API and demo walkthrough](docs/assistant.md).

```bash
python3 scripts/ask_assistant_api.py
```

An Arabic Streamlit workspace is available for the assistant, demo accounts,
transfers, loans, PDF statements, policy search and authorized identity processing:

```bash
/usr/bin/python3 scripts/local_ui.py up
```

Open `http://127.0.0.1:8501` and sign in with your existing credentials.
See [Streamlit setup and usage](docs/streamlit.md).

The local Qwen3.5-4B GGUF / llama.cpp backend, benchmark and rollback are documented
in [local_llm.md](docs/local_llm.md). The current model is shown under each natural-language response.
