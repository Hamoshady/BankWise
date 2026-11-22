# Local Qwen3.5 with llama.cpp

This optional backend replaces both the Supervisor's language model and the RAG
sentence selector. E5 embeddings, indexed documents, accounts, OTP checks and graph
persistence do not change. The former Qwen2.5 weights remain available for rollback.

Pinned artifact metadata and checksums are in `configs/local_llm_manifest.json`.
The GGUF is 3,032,091,584 bytes; the CPU runtime archive is 23,311,084 bytes. Only
these files are required for text use: no vision projector or draft model download.
Weights live outside Git in `~/.local/share/bankwise/models/`; the binary lives in
`~/.local/share/bankwise/llama-b12359/llama-b12359/`.

The downloaded CPU archive comes from:
https://github.com/ggml-org/llama.cpp/releases/download/b12359/llama-b12359-bin-ubuntu-x64.tar.gz

The model is pinned to:
https://huggingface.co/ggml-org/Qwen3.5-4B-GGUF/resolve/8d43d974efc48399cb58957ad49c8b26bf5a109c/Qwen3.5-4B-Q4_K_M.gguf

## Start, inspect, stop

```bash
python3 scripts/local_llm.py verify
python3 scripts/local_llm.py up
python3 scripts/local_llm.py status
python3 scripts/local_llm.py logs
python3 scripts/local_llm.py stop
```

`up` installs/enables a user systemd unit with restart-on-failure. It never downloads
weights. It binds only to 127.0.0.1:8081, with one slot, 8K context, four CPU threads,
no GPU offload, no reasoning, a 1 GiB RAM prompt-cache budget and no web UI or prompt logs.
The prompt cache retains matching prefixes across Supervisor/RAG requests in RAM only.
The API now prepares common planner and evidence-selection prefixes before readiness.
This moves work into startup (63.64 seconds restart-to-ready in the policy run).
If preparation fails or llama.cpp restarts independently, the next request can still be slower.
The user service starts at user login. API Docker restarts may precede it at boot;
startup fails closed until the configured model is available. `local_api.py up`
starts the service first whenever its private environment config selects llama.cpp.

Set this line in `~/.config/bankwise_ai/local-api.env` to opt in:

```dotenv
BANKWISE_LLAMA_URL=http://127.0.0.1:8081
```

Then run `python3 scripts/local_api.py up`. The semantic runtime loads only E5 locally
and delegates LLM inference to llama.cpp. No cloud model calls or implicit fallback.
The local adapter checks model identity, rejects redirects, constrains JSON output,
disables thinking and rejects incomplete output. Normal tool validation and OTP
authorization remain mandatory. Retrieval selection still accepts only IDs of
existing evidence sentences; the LLM cannot supply uncited policy prose.

To roll back, remove `BANKWISE_LLAMA_URL` from the private environment and run
`python3 scripts/local_api.py up`, then `python3 scripts/local_llm.py stop`.
This loads the preserved Qwen2.5-1.5B FP32 model. No database migration or reindexing
is required. Do not delete either model until rollback is no longer needed.

## Evaluation

`evaluation/supervisor/cases.json` contains the same 24 questions for both pipelines.
`protocol.json` records the adoption criteria. Benchmark runs do not execute tools,
read customer data, or mutate bank accounts. Each timed call includes planning and
validation; startup and one warmup are excluded. llama.cpp may reuse prompt prefixes,
so performance compares complete deployed pipelines, not only model architectures.
One regression prompt also appears in the planner's few-shot examples; this small
suite is development evidence, not a general Egyptian Arabic accuracy estimate.

Run from the semantic Docker environment, mounting source, the read-only
`bankwise-rag-models` volume and host networking:

```bash
PYTHONPATH=src python scripts/benchmark_supervisor.py --backend current --output evaluation/supervisor/qwen2.5-1.5b.json
PYTHONPATH=src python scripts/benchmark_supervisor.py --backend llama --output evaluation/supervisor/qwen3.5-4b.json
```

Stop the ordinary API while benchmarking to avoid duplicate model memory and CPU
contention. Do not run both benchmarks simultaneously. See `scripts/smoke_streamlit.py`
for live UI checks, and `scripts/smoke_research.py` for historical data and CIB policy
regressions. Only local demo login credentials are read; none are printed.

## Policy latency optimization

Sentence embeddings use a content-keyed LRU cache (1,000 sentences). Selection
uses an LRU cache (256 entries) of validated sentence IDs, keyed by the exact
question, instructions and ranked evidence. Every request still reads the current
visible corpus and reconstructs citations; changed or hidden evidence cannot
return a previous cached answer. Invalid selections and abstentions are not cached.
The llama.cpp path uses equivalent shorter selection instructions with unchanged
examples, candidate budget and thresholds. Startup prefix preparation is best
effort and executes no banking tools. No policy answers are prefilled.

See [policy measurements](../evaluation/policy_latency/RESULTS.md) for the live
Streamlit AppTest comparison and startup tradeoff.
