# BankWise: CIB knowledge and PKDD99 research data

## Run

```bash
python3 scripts/local_api.py up
python3 scripts/local_ui.py up
```

Open http://127.0.0.1:8501. Existing local credentials are in
`~/.config/bankwise_ai/demo-logins.json` (private; never commit or paste into logs).
Use `demo_alice` for historical accounts 1 and 2, or `demo_bob` for client 4's accounts.
These are research sample grants, NOT claims that app users own the historical accounts.

The **البيانات البنكية الحقيقية** page offers date filters, pagination, full-period
credit/debit totals, monthly charts, loan history and a research PDF. Historical
amounts remain CZK and dates remain unchanged. A snapshot balance is not a live balance.
The assistant's data-source selector switches read tools between historical data
and the EGP demo. Start a new conversation on switching. Historical actions fail closed.

## Sources and limitations

- CTU source: https://relational.fel.cvut.cz/dataset/Financial
- Eight complete tables, exported directly from the public read-only MariaDB service.
- Exact source columns preserved, including CTU's transformations (birth date and gender).
- Import manifest: `outputs/datasets/berka/manifest.json`, hashes and actual row counts.
- Upstream does not specify an explicit license on the reviewed pages. User authorized
  local research use with attribution; this is not a legal conclusion or permission
  to redistribute. Raw exports/backups remain ignored under `outputs/`.
- No real Egyptian IDs, names, live bank connections or verified KYC are supplied.
- CIB corpus: `knowledge/cib/`, source catalog: `configs/cib_sources.json`.
  These are reviewed Arabic summaries of official public sources, checked 2026-11-15.
  They are not full contracts. Binary downloads returned site protection pages;
  those responses are not accepted as PDFs. The reviewed sources are linked in UI.
- CIB account-fee PDF is multi-column. Fees were NOT inferred from scrambled text.
  The assistant must defer exact unimported tariff values to the official table.
- Account-fee source states 2026-09-01. Undated documents keep effective_date=null.
  Retrieval date is not an effective date. Review the corpus when the bank changes it.
- Old `knowledge/policies/` documents remain legacy fixtures, not the active shared
  corpus. Local import now uses `knowledge/cib/` and `--prune` removes stale shared
  records only for the disabled policy publisher. Private user documents are preserved.

## Data flow and trust

JWT/roles -> LangGraph security -> supervisor -> Info/Transfer/Loan/Account nodes ->
confirmation gate -> response -> audit -> persisted conversation state.
RAG retains multilingual E5 embeddings and uses the configured local Qwen evidence selector.
The current local deployment selects Qwen3.5-4B Q4_K_M through llama.cpp; see local_llm.md.
Natural-language routing uses the existing local LLM with a constrained, validated tool plan.
Service forms use explicit tools. The graph can run up to three tools and one sensitive proposal.
Native per-step LangGraph checkpoints are persisted synchronously in PostgreSQL, independently
of the request transaction. They include tool results, but not raw messages, tokens or OTP.
Conversation state and financial operations also retain transactional persistence. Checkpoints
are diagnostic state, not proof of financial commitment. Financial replay is not exposed.
The Streamlit audit screen shows owner-scoped events and saved graph stages.

Research data are accessed only through grant-scoped SELECT queries. UUID demo account
IDs and integer historical IDs are separate. Mutations, OTP approvals and demo KYC
operate exclusively on demo_* tables. Historical data never satisfy KYC or CIB credit
eligibility. Demo limits are simulator rules and are not advertised as CIB tariffs.

## Reproduce import

Install PyMySQL in an isolated tooling environment and run:

```bash
python scripts/download_berka.py
python3 scripts/local_api.py stop
python3 scripts/local_api.py build
python3 scripts/local_api.py migrate

docker run --rm --network host \
  --env-file "$HOME/.config/bankwise_ai/local-api.env" \
  -v "$PWD/outputs/datasets/berka:/data:ro" \
  bankwise-ai:semantic python -m bankwise.database.import_financial /data \
  --grant demo_alice:1 --grant demo_alice:2 --grant demo_bob:4

python3 scripts/local_api.py import-policies --force --prune
python3 scripts/local_ui.py up
```

Import verifies SHA-256, CSV columns, row counts and foreign keys in one transaction.
Re-import of the same manifest is a no-op; changed data cannot silently overwrite the
historical corpus. Back up the application database before migrations. The local
pre-change backup for this upgrade is `outputs/backups/before-berka.sql` (private).

PDF export intentionally uses the most recent 100 transactions for the selected
account, independent of UI date filters; the UI explicitly labels this scope.

## Verification — 2026-11-16

- Unit suite: 604 passed, including numeric limit relevance and historical isolation.
- Streamlit interaction suite: 8 passed.
- Live Streamlit AppTest: existing demo login, six pages including real charts, CIB source catalog and logout passed against the running API.
- Historical PDF first-page rendering inspected: no clipping or overlapping text.
- CIB Bedaya evidence now separates daily, monthly and total-position limits; numeric-limit retrieval excludes interest-rate and disclaimer distractions. Values are retrieved from the reviewed source, not hardcoded in the response generator.
- PostgreSQL backend integration suite: 85 passed using an isolated disposable database (not the user's database).
- Live `scripts/smoke_research.py`: passed historical authorization, pagination, PDF, persisted graph state, historical-write rejection, six CIB questions and OTP proposal cancellation. Daily/monthly Bedaya limits returned 90000/300000 and maximum position 750000 with official-source citations.
- Total automated checks: 697 passed (604 unit + 85 PostgreSQL integration + 8 Streamlit interaction).
- Both `http://127.0.0.1:8000/health/ready` and `http://127.0.0.1:8501/_stcore/health` returned HTTP 200.
