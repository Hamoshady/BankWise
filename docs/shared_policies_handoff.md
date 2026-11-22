# Shared policies and customer OCR interface

Implemented in the original bankwise_ai checkout; no commits or pushes.
Eight editable Markdown draft policies are under knowledge/policies. Operational fees,
limits and eligibility remain explicitly unapproved, not fabricated. Import through
bankwise.rag.import_policies; it serializes imports, skips matching hashes and commits
atomically after audit authorization. Use `--force` after changing the chunker or
embedding model, and `--prune` to delete shared policies whose files were removed.
The initial live import changed 8 records and the second changed 0. Private documents
and legacy public database tables were preserved.

Migration 0003_shared_policies was applied to the isolated bankwise_api schema;
migration 0004_dynamic_embeddings later added per-chunk vector dimensions.
The customer role alone can read shared knowledge only; a principal that also holds
knowledge_user keeps its private scope. Publisher ownership belongs to an
inactive internal account. No client API can publish shared policies. The local account
knowledge-one now has customer + identity_processor + operator; credentials unchanged.
The UI hides policy editing and provides front/back upload through the existing identity
service. Face matching/liveness and actual financial transactions remain out of scope.

Validation: full suite 729 passed, 3 skipped, 1 warning in 25.79s. After adding the final
customer authorization test, targeted RAG integration suite: 23 passed, 1 warning in
9.84s. Skips are real OCR dependency/opt-in tests; warning is Starlette/httpx deprecation.
Live checks passed for shared Arabic query citations, forbidden customer document write,
invalid identity-image rejection and logout. Real identity-card accuracy was not tested.
The serving container is bankwise-api-local; previous stopped version is retained as
bankwise-api-before-shared. Reload the homepage and sign in again after the role change.
