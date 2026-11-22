# Arabic retrieval update

Arabic matching now removes diacritics/tatweel, normalizes alef/ya/taa-marbuta forms,
strips the definite article for matching, and maps a bounded banking-domain vocabulary
including Egyptian expressions to canonical terms. Source text and quoted excerpts are
never rewritten. Negation is retained. Aliases are in `src/bankwise/rag/arabic.py`.

Retrieval combines corpus-frequency-weighted query term coverage (75%) and the existing
hash-vector cosine score (25%). At least one normalized term must match: vector hash
collisions alone cannot produce a result. Sentence selection uses the same normalized
terms and excludes Markdown headings. Scores are ranking signals, not confidence or
pure cosine similarity. Existing stored vectors/model IDs remain unchanged; no migration
or reimport is required. Ownership filtering happens before retrieval as before.

This is a local lexical/hashing hybrid, **not a pretrained semantic embedding model or
an LLM**. It does not understand arbitrary paraphrases, infer missing fees, resolve all
negation/scope questions, or evaluate bank policy correctness. Shared policies remain
drafts, and any quoted draft limitation must be respected. Full semantic RAG still needs
an approved local multilingual model, versioned embeddings and a broader quality corpus.

Run `PYTHONPATH=src python scripts/evaluate_arabic_rag.py`. The checked-in 18 cases test
service-source selection across the eight policies and two out-of-domain abstentions.
This is a small development/regression set, not held-out accuracy evidence. Unit tests
also exercise normalization, citations and an unrelated medical query.

The disk was initially unmounted after reboot on 2026-11-01. UDisks mounted the original
volume as `New Volume1` because Docker had created empty directories at `New Volume`.
Code changes are on the original volume/repository, not a copied checkout. The local API
container was recreated with the actual mounted configs path. Run the local launcher
from the real mounted repository. Do not start Docker bind mounts against an unmounted
project path; verify the drive is mounted first.

Validation on 2026-11-01: full suite **738 passed, 5 skipped, 1 warning in 26.22s**
in the existing Docker environment with disposable PostgreSQL integration databases.
Five real-OCR tests require explicit opt-in; one Starlette/httpx deprecation warning.
The 18-case policy benchmark passed. Live authenticated API checks passed for account
closure, money transfer, expense analysis paraphrases, and medical-query abstention.
No commits, pushes, schema changes, or policy imports were performed for this update.
