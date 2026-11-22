# Public account numbers

Demo bank accounts now have an immutable-by-API `account_number`: a unique, non-null string of exactly 12 ASCII digits. This is an application-specific demo number, not an IBAN or a real bank identifier.

Migration `0011_account_numbers` backfills every existing account (including closed accounts) and installs a PostgreSQL sequence default starting at 100100000001. Concurrent inserts get distinct numbers. Sequence values may have gaps after rollbacks; numbers are not reused. The sequence does not cycle and cannot exceed 12 digits.

`demo_accounts.id` remains the internal UUID primary key. Existing account ownership, balances, transaction rows and UUID foreign keys are preserved. No transactions are created by the migration. Account balances remain stored as integer piastres.

Statements, beneficiary lists, account management, transfer confirmation, admin deposits and PDF statements display the public number. Transfer/read/closure argument fields retain their existing API names (`account_id`, `beneficiary_id`) and accept either a 12-digit public number or a legacy UUID. The frontend sends public numbers for customer account operations. Ownership, KYC, locks, limits and OTP requirements apply equally to both forms; knowing an account number grants no authority. Admin deposit requests retain internal UUID references but display public numbers.

In the local deployment:

```sql
SELECT u.username, a.account_number, a.balance / 100.0 AS balance_egp,
       a.status
FROM bankwise_api.demo_accounts a
JOIN bankwise_api.users u ON u.id = a.owner_id
ORDER BY u.username, a.account_number;
```

Deploy using the normal migration/API startup workflow, followed by the UI update. Refresh/re-login in existing browser sessions to discard cached account lists. Previously stored operation arguments remain executable through UUID compatibility.
