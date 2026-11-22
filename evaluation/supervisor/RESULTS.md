# Qwen3.5 adoption: verified 2026-11-16

Status: **adopted** on the local application, running on CPU through llama.cpp b12359.
The original Qwen2.5 weights and private environment backup are retained for rollback.

| Measurement | Qwen2.5-1.5B FP32 / Transformers | Qwen3.5-4B Q4_K_M / llama.cpp |
|---|---:|---:|
| Exact tool-set matches | 18/24 | 24/24 |
| Median planner latency, seconds | 10.116 | 2.659 |
| Mean planner latency, seconds | 10.392 | 2.638 |

Final warm routing speedup: **3.8x**. All seven policy-question,
negation, hypothetical, quoted-instruction, injection and unsupported-request checks passed.
The known colloquial statement and balance mistakes were corrected in this set.
These are 24 development cases, including an in-prompt regression example, not a
claim of 100% general Arabic accuracy. Both use the same classifier messages and
4 CPU threads. New JSON-schema decoding and prefix caching are part of the pipeline;
this is not an isolated comparison of model weights. Startup and one warmup excluded.

## Live Streamlit timings

Seconds from submitting the chat input through rendering the response with AppTest,
using the actual API and models. This is interaction testing, not a screenshot review.

| Request | Old, seconds | New, seconds |
|---|---:|---:|
| اعرض كشف حسابي | 10.216 | 2.462 |
| ما الحد اليومي لحساب بداية؟ | 16.76 | 21.312 |
| عايز كشف حساب وشروط القرض | 22.286 | 19.197 |

Not every request became faster: the Bedaya policy query took longer in this pass.
An uncached new-model statement request previously took 28.392 seconds. A disabled
prompt cache also caused repeated Supervisor/RAG switching to be slow; the deployed
service now reserves 1024 MiB for prefix caching. Cold requests after restart remain
slower. See `ui-qwen3.5-cache-disabled.json` for that diagnostic run. These are small
single-pass UI measurements, not latency guarantees.

## Validation and scope

- 707 unit/backend integration tests passed, including owner isolation and OTP gates.
- 10 Streamlit interaction unit tests passed.
- 4 actual-model evidence-selection tests passed (facts, negation, unknown fees, override).
- Live UI passed statement, Bedaya answer, combined request, audit tables and restoration.
- Six CIB policy regression questions passed with official source citations.
- Historical grant isolation, pagination, PDF, checkpoint and confirmation cancellation passed.
- Both model and runtime archive SHA256 verified against published metadata.
- LLM model selection is shown under `كيف عالج المساعد طلبك؟` in the UI.

A pre-existing retrieval filter was also corrected: arbitrary uploaded source names
are eligible for semantic retrieval, while canonical source names still narrow topics.
Embeddings, indexed data and financial authorization rules were preserved.
No real financial operations or bank connections were introduced.

Files: `cases.json`, `protocol.json`, both model JSON reports, `comparison.json`,
`ui-current.json`, `ui-qwen3.5.json`, `environment.json`, and `acceptance.json`.
Operation and rollback instructions: [local_llm.md](../../docs/local_llm.md).
