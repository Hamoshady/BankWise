# Policy latency verification

Measured with Streamlit AppTest against the live local API and Qwen3.5-4B Q4_K_M, using six policy questions twice. This exercises the UI code and real backend; it is not a visual browser review. Timings are single-run observations, not performance guarantees.

| Question | First before (s) | First after (s) | Repeat before (s) | Repeat after (s) |
|---|---:|---:|---:|---:|
| ما الحد اليومي لحساب بداية؟ | 47.557 | 6.455 | 7.251 | 2.381 |
| كم الحد الشهري لحساب بداية؟ | 7.174 | 6.186 | 7.758 | 2.465 |
| ما أقصى رصيد لحساب بداية؟ | 17.992 | 16.535 | 18.815 | 2.501 |
| ما الحد الأدنى لفتح حساب Classic بالجنيه؟ | 17.263 | 16.191 | 16.875 | 2.588 |
| ما الحد الأدنى للسن للقرض الشخصي؟ | 19.045 | 16.935 | 18.304 | 2.562 |
| ما شروط إغلاق حساب CIB؟ | 17.289 | 16.285 | 19.75 | 2.52 |

All 12 answers and complete citation objects match the baseline exactly. Each response completed through the local LLM supervisor and selected search_knowledge_base, with official CIB source links. This regression set does not establish accuracy for all possible questions.

The first-after-readiness improvement moves prefix preparation to API startup. Restart-to-ready took 63.64 seconds, including preparation. Unseen questions can still take approximately 16–17 seconds. Repeated-question mean fell from 14.792 to 2.503 seconds.

Implementation: cache sentence embeddings by content; cache only validated evidence sentence IDs for identical question/instructions/evidence. Re-read visible corpus and reconstruct current citations on every request. Invalidate on content/visibility changes. Keep ranking thresholds, candidate count, grounding, model and citations unchanged. Equivalent shorter selection instructions and best-effort prefix preparation reduce inference work.

Validation recorded during implementation: 717 unit/backend integration tests and 4 real-model semantic tests passed. Includes cache invalidation, authorization visibility, negation, injection, abstention and warmup behavior. Local API readiness, llama health and Streamlit health were subsequently verified HTTP 200.

Raw evidence: before.json, after.json, profile-before.json, profile-after.json, startup-after.json. after-cache-only.json is an intermediate run before startup preparation.
