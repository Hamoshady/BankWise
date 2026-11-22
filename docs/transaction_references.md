# Public transaction references

`bankwise_api.demo_transactions.reference` is a unique, non-null public identifier such as `BW-20261120-000001`. The date is the transaction's `created_at` converted to Africa/Cairo; the six-digit counter starts at 1 each day. This is a BankWise demo format, not a real-bank standard.

Migration `0012_transaction_references` backfills old transactions by their original dates, ordered by creation time and UUID for ties. It changes no amounts, balances, timestamps, UUIDs or transaction links. A PostgreSQL BEFORE INSERT trigger assigns references for all new transfers and admin deposits. An atomic per-day counter prevents duplicates under concurrency; exceeding 999999 transactions on a day fails atomically rather than wrapping or truncating. Rolled-back transactions do not consume a committed reference.

Statements return `reference` alongside legacy `id`; deposit receipts include it alongside `transaction_id`. Transfer confirmation returns it and resolves it for replays of receipts saved before the migration. UI transaction tables, confirmation/deposit receipts and PDF statements display references. UUIDs remain internal keys, and existing operations remain idempotent.

Validation: PostgreSQL migration/backfill tests compare all pre-existing monetary fields and IDs; concurrent insert tests validate distinct counters, including Cairo date boundaries; legacy receipt replay creates no second transaction. UI tests verify transfer and deposit receipt references. A synthetic PDF was extracted and checked for the public number/reference.

```sql
SELECT reference, amount / 100.0 AS amount_egp, created_at
FROM bankwise_api.demo_transactions
ORDER BY created_at DESC;
```
