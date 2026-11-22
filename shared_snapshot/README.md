# Shared project data snapshot

Included at the project owner’s request: a PostgreSQL custom-format dump, runtime outputs, runtime configuration and supplied ID images. This contains real personal data and secrets.

The database dump is from `bankwise-postgres`, database `bankwise_ai`. Restore into a separate empty database using `pg_restore --no-owner --no-privileges -U USER -d TARGET database.dump`; do not restore over an existing team database. The target user needs permission to create the dump’s objects.

`runtime.env` preserves the running application settings and encryption keys. Host paths, database credentials/URL, model paths and LLM URL must be adjusted for the receiving machine. Keep the identity encryption key to read the encrypted records.

Login passwords are stored as hashes, not recoverable plaintext. Use known credentials or reset passwords through an authorized workflow.

The external model weights, Docker images and Windows ZIP are not part of this source snapshot. See the project setup and model documentation.
