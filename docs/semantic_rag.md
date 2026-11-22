# Local semantic RAG and evidence-selecting LLM

For the optional Qwen3.5-4B GGUF backend and rollback instructions, see [local_llm.md](local_llm.md).
The description below documents the original Transformers backend.

This optional mode adds real pretrained models, without cloud inference:

- Retrieval: [intfloat/multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small), MIT, 384 dimensions. Use `query: ` and `passage: ` prefixes even for Arabic, attention-mask mean pooling and L2 normalization, as prescribed by its model card.
- Evidence selection: [Qwen/Qwen2.5-1.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct), Apache 2.0, CPU float32 weights. Dynamic int8 was rejected after it failed the Arabic evidence-selection checks. The original weights remain safetensors; remote code is disabled.

Provisioning resolves repository commit hashes and writes them to the local volume's
manifest. Runtime accepts only those repository names, pinned revision-shaped IDs and
local files. It never calls Hugging Face or downloads models while serving.

## Grounding contract

The LLM receives a bounded list of retrieved policy sentences, not OCR images/results,
credentials or bank transaction data. It returns only sentence IDs. The server validates
JSON shape, bounds and count and renders the corresponding **exact original sentences**
with server-generated citations. Unsupported model IDs/prose/malformed outputs yield
abstention. It cannot introduce a number, fee or claim from its free-form output.
This deliberately does not provide unrestricted paraphrasing or conversational reasoning.

This is not a mathematical guarantee of correct answers: a model can select a real but
irrelevant or misleading passage, and policies themselves can be wrong/incomplete.
Negation and surrounding context matter. Draft policies remain drafts. Evaluate intent,
relevance, refusal and source fidelity separately; do not advertise zero hallucinations.

Semantic cosine cutoff 0.74 is a conservative development default, not a
probability; override it with `BANKWISE_RAG_THRESHOLD` (0 < value <= 1) when the
corpus or model changes. No lexical-overlap gate is used, allowing actual
paraphrase retrieval.
Normalized banking terms reorder candidate sentences only. The local LLM is asked to
abstain if evidence is insufficient, including missing personal balances or unapproved fees.

## Data and resource lifecycle

The semantic runtime keeps hash-embedding API documents and private/shared ownership
unchanged, and still derives E5 vectors in memory instead of persisting them. Migration
`0004_dynamic_embeddings` only removes the fixed-384 schema limit, so persisting another
model is possible without a further schema change.
The semantic runtime reads the already authorized chunk set and computes trained vectors.
A bounded, process-local cache keyed by chunk ID and text SHA-256 (within one pinned model
runtime) avoids repeated encoding; updates/deletes are observed through the database on
every request. Entries outside the current authorized chunk view are evicted before retrieval. Cache entries alone can never make a document retrievable. Restart rebuilds
this derived cache; it is not another persistent database or ORM.

One shared model instance is loaded at application startup and closed at shutdown.
Only one semantic request runs at a time; others receive 429. Queries remain bounded by
API schemas, document/corpus limits and existing role/rate/audit checks. Local LLM output
is capped at 64 tokens with a 60-second generation budget; model/service errors return
sanitized 503. Invalid model output does not become invented prose. No prompts are logged.

## Setup and rollback

Build the ordinary image first, then `docker build -f Dockerfile.semantic -t bankwise-ai:semantic .`.
Run `scripts/download_rag_models.py` explicitly inside the semantic image with a writable
`bankwise-rag-models` volume mounted at `/rag-models`; this setup operation requires network
and several GB of storage. Serving mounts that volume read-only. Keep model weights out
of Git. `requirements-semantic.txt` contains the optional runtime dependencies.

Set `BANKWISE_RAG_MODE=semantic` and `BANKWISE_RAG_MODEL_DIR=/rag-models` in the private
local API environment file. Recreate the stopped API container to apply the new image
and settings. The launcher mounts both OCR and RAG model volumes. The old lexical mode
remains available with `BANKWISE_RAG_MODE=lexical`; no schema downgrade is required.
Do not reuse a previous stopped container without checking its environment/image.

Evaluate real models explicitly with `PYTHONPATH=src python scripts/evaluate_semantic_rag.py`
inside the prepared semantic container, mounting the repository and read-only model volume.
The small checked-in corpus is development regression evidence, not a production benchmark.

Development observations: the initial E5 chunk ranking selected the intended service
for 16/16 direct Arabic policy questions, but only 4/8 more indirect service paraphrases
in a separate small set. An initial 0.80 cutoff also admitted an unrelated medical query;
0.82 rejects the tested medical/weather examples but cannot guarantee rejection of all
out-of-domain questions. No claims of production-grade intent accuracy are made. The
LLM evidence selection is evaluated separately after provisioning; source fidelity alone
is not answer relevance. The validation set is checked in for reproducibility.

The finite generation grammar permits only JSON arrays of existing evidence IDs or
an empty array. The current runtime offers one highest-ranked sentence for validation,
so the only accepted selections are that sentence or abstention. Server validation
remains an independent check. Trained sentence similarity and weighted normalized
term coverage rank candidates without the old equal-overlap hard filter.
Citations include the full original retrieved chunk so nearby caveats can be reviewed.
This reduces, but does not eliminate, risks from irrelevant or incomplete context.

## Verified deployment and test results (2026-11-01)

Pinned retrieval revision: `614241f622f53c4eeff9890bdc4f31cfecc418b3`.
Pinned Qwen revision: `989aa7980e4cf806f80c7fef2b1adb7bc71aa306`.
Tested optional packages: transformers 4.57.6, huggingface_hub 0.36.2, safetensors 0.8.0.

With both `BANKWISE_TEST_POSTGRES=1` and `BANKWISE_TEST_SEMANTIC=1`, the full suite
passed **762 tests**, with **5 real-OCR opt-in skips** and **1 Starlette/httpx warning**
in 64.57s. This includes real-model tests for a provided fee, negation, unapproved fees,
and an instruction-override attempt. Synthetic monetary values were in memory only,
never imported into the application's shared policies.

The 20-case policy development evaluation initially matched 19 expected outcomes;
the remaining injection attempt selected an irrelevant but real quote. Results are
preserved in `evaluation/rag/semantic_development_results.json`. An additional narrow
input guard now refuses obvious Arabic/English instruction-override requests. This is
not a complete prompt-injection detector; exact-source output validation remains the
main boundary. The corrected case was verified by unit tests, real-model tests and
live HTTP. Do not present this development set as independent accuracy evidence.

Live API checks confirmed `mode=semantic_grounded`, citations from actual policy files,
medical-query abstention, and instruction-override abstention. Measured local response
times for two in-domain questions were about 8 and 15 seconds; refusals about 0.01–0.03s.
The service runs on CPU, and can consume several GB of RAM. Startup loads both OCR and
RAG models before readiness succeeds. No real ID-image accuracy evaluation was performed.

The local runtime environment is enabled for semantic mode and offline model loading.
The prior lexical container is retained as `bankwise-api-before-semantic`; its config
backup is the owner-only `~/.config/bankwise_ai/local-api.before-semantic.env`.
The image now embeds configs, and runtime mounts only Docker named volumes, so a missing
external project drive does not break the running server at reboot. Rebuild/recreate
when editing configs; policy changes still use the documented operator import.


## Policy acceptance hardening (2026-11-02)

The v1 fictional policies replace draft placeholders with explicit demonstration rules;
see `knowledge/POLICY_DECISIONS.md`. These are not real bank terms. The acceptance
suite checks actual answer content, numeric values, negation, citations and refusal,
not just the retrieved source label.

The current retrieval cutoff is 0.74 by default (a development threshold, not
confidence), configurable with `BANKWISE_RAG_THRESHOLD`.
Highest-scoring chunks are retained without forcing one chunk per source. Candidate
sentences use 40% trained semantic similarity plus 60% weighted query-term coverage;
rare terms and conservative Arabic aliases distinguish the requested attribute.
The LLM now validates only the highest-ranked sentence, returning its ID or abstaining.
This deliberately limits answers to one evidence sentence: ask compound questions
separately. Invalid output remains fail-closed; the model's free text is never exposed.
Earlier multi-candidate selection descriptions above are historical, not the current
answer-selection behavior. Broad paraphrases, incomplete questions and adversarial
inputs still require further evaluation; passing development examples is not a
production accuracy guarantee.

Chunking now prefers sentence/paragraph boundaries and aligns overlap to boundaries
where possible. Oversized sentences remain bounded, so arbitrarily long input can
still be split; short policy sentences are recommended. No database schema changed.
Existing indexed documents require re-ingestion to use new chunk boundaries; run the
importer with `--force` instead of deleting rows manually.

The chunk- and sentence-ranking fixes from 2026-11-02 also cover two Arabic
morphology gaps: minimum/maximum synonyms (`أقل`/`أدنى`, `أكبر`/`أقصى`) and
`متاح`/`متاحة` now normalize to the same term. This removed the previous
`loan_range` and `statement_formats` acceptance failures; the checked-in
acceptance suite is expected to pass 21/21.
