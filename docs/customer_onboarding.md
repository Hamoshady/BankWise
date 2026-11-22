# Reviewed signup and demo deposits

The current registration flow replaces the old username/password-only `/auth/signup` endpoint,
which now returns 404 to prevent bypass. Existing users, financial records and the old identity
review workflow remain supported. Schema revision `0010_onboarding` only adds three tables.

## Customer journey

1. Start or resume with an Egyptian mobile at `/onboarding/phone`, verify the signup-only OTP.
   Codes use shared cryptographic primitives: random six digits, 300 seconds, five attempts,
   60-second resend cooldown, rate limits, digest storage and single-use verification.
   Separate tables and mock-inbox namespaces prevent use of a signup code for login/transactions.
2. A random 30-minute application session authorizes only application endpoints. The database
   stores its SHA-256 digest. Verify the same phone again to recover a draft after a browser refresh.
3. Upload front/back images with explicit consent. Reuse the local OCR processor, image limits,
   concurrency gate and disclosure audit; no card content is sent to RAG or the chat model.
4. Review/correct identity fields and fill KYC. Original OCR and corrected data are distinct.
   The only offered product is a personal EGP sandbox account with a **demo age rule of 21+**,
   not a claim about any real bank's legal eligibility. Birth date/gender must agree with the
   ID structure; expiry must be valid. This does not prove card authenticity.
5. Create credentials with only the `applicant` role. No DemoCustomer or DemoAccount exists yet.
   Applicants can sign in with password/OTP to follow their application, but cannot call banking,
   assistant, knowledge or the legacy identity review endpoints.
6. Submit, review, request more information / reject / approve. Approval atomically changes roles,
   creates the customer and exactly one zero-balance account; no funds are created. Replayed approval
   cannot create another account. Role changes invalidate old sessions; log in again after activation.

The development OTP button explicitly says no SMS was sent. Simulation never marks phone ownership
as verified. Real SMS requires the configured provider; mock endpoints are unavailable in production.
All onboarding/deposit endpoints are limited to `BANKWISE_BANKING_MODE=demo`.

## Staff and money

`identity_reviewer` reviews applications. A separate `demo_treasurer` permission enables simulated
funding; it was assigned to the local `identity_officer` staff account for this installation.
The broad `admin` role can also review and deposit. Ordinary customers cannot deposit through this API.

The UI previews the account, amount and reason, then asks for explicit confirmation. The backend
locks customer/account records, checks activation and open status, stores a credit in `demo_transactions`
and a linked `demo_deposits` receipt with actor/reason, and updates the balance in the same transaction.
Amounts are positive, at most EGP 1,000,000 per demo deposit and at most two decimal places.
A stable operation UUID makes retries idempotent; changed payloads using that UUID are rejected.
Deposits appear in the customer's ordinary statement as `demo_deposit`.

## Privacy, retention and existing records

Identity/KYC/contact data and images are Fernet-encrypted with the existing private review key.
Keyed HMAC fingerprints enforce phone/identity uniqueness without storing an unkeyed ID hash.
Preserve the review key across restarts. Pending application evidence expires seven days after its
latest image upload (initial drafts expire seven days after creation). A periodic API maintenance
job purges expired draft/pending evidence within about one minute while the API is running, and on
startup. Images and original OCR are removed on approval/rejection; active encrypted profile data is
retained. Cancellation clears application data. Rejected payloads are purged on their expiry date.

Duplicate identity checks cover identities enrolled through this new workflow. Legacy verified users
whose national IDs were deliberately never retained cannot be matched retroactively; their approved
profiles need a separate authorized identity-enrolment migration if cross-legacy matching is required.
Their accounts/balances are not reset or silently reapproved. No government identity checks, selfie,
liveness, real account creation or live SMS delivery are claimed by this sandbox.

## Validation

Disposable PostgreSQL integration tests cover the complete journey, applicant isolation, OTP expiry,
attempt limits and purpose separation, resumption tokens, duplicate identities, correction/rejection,
zero-balance activation, concurrent duplicate deposits, ledger consistency and unchanged legacy funds.
Streamlit tests cover signup steps and explicit deposit preview/confirmation.

---

## Historical implementation notes (superseded registration flow)


The local banking sandbox exposes POST /api/v1/auth/signup only when banking_demo
is enabled. The request accepts username and password only (12–256 characters).
Roles are fixed server-side to customer and identity_processor; a zero-balance
account and unverified customer are created atomically. Registration is rate
limited before Argon2 hashing, audited, and rejects duplicate usernames. Existing
accounts are preserved. This is not public production banking onboarding.

POST /api/v1/identity/extract requires identity:read_own_upload plus the existing
identity:process gate. It processes only the authenticated caller's current image
pair; it accepts no owner ID or stored-result ID. A strict disclosure audit must
succeed before a whitelist of extracted fields is returned. Images and extracted
text are not persisted, indexed into RAG, or sent to the chat model. Responses
inherit the transport no-store policy. The old /verify metadata-only contract is
unchanged. Reading a card does not update KYC or prove authenticity.

The UI keeps the result only in the user's session, provides a clear-result button,
and clears session data on logout. Upload widgets retain selected images within
the active UI session. Missing fields are shown as unreadable, not synthesized.
Refreshing or signing out may discard the result; this is intentional.

Sign-up and sign-in are separate form states. The workspace greets the customer by
username. A single persistent sidebar label identifies the banking simulation;
transaction forms use plain language and retain explicit approval. The OTP remains
an in-app test code, not SMS. New customers need separately reviewed KYC before
sensitive banking actions; successful OCR alone never approves them.

Validation (2026-11-17): 719 unit/backend tests passed before the final onboarding
redirect adjustment; the final API suite passed all 44 tests (including two added
security regressions), the UI suite passed 13 tests, and the real EasyOCR pipeline
passed 3 synthetic-image integration tests. These check integration and privacy,
not accuracy on the user's card. Live Streamlit AppTest verified the signup form,
personal sign-in, named greeting, identity upload controls and logout against the
running API. No visual browser review or real personal-card evaluation was performed.

## Manual identity review (2026-11-17)

The subsequent review workflow is now implemented. Customer GET /identity/status
is authoritative on each dashboard visit; it supersedes stale login KYC flags.
Unapproved customers see their pending/rejected state instead of account errors.
POST /identity/reviews accepts a new front/back upload, processes it, and stores
the result and images encrypted using BANKWISE_REVIEW_KEY (a dedicated Fernet key,
kept in the private environment file, never in source control). UI consent is
explicit. Extraction alone still stores no card evidence.

The separately provisioned identity_reviewer role can list pending requests,
inspect evidence, and approve or reject through /identity/reviews/{id}/decision.
Self-review is forbidden. A transaction locks the request and customer, records
an audit event, updates KYC and clears encrypted evidence. Approval creates no
funds and does not establish official identity. Rejected customers can resubmit.
Pending evidence expires after seven days; status/list/submission accesses purge
expired payloads. An offline service does not run scheduled deletion. Keep the
review key available across restarts or pending evidence cannot be decrypted.

Reviewer credentials are in the local private reviewer-login.json file alongside
local-api.env. Do not commit them. See evaluation/identity_review/RESULTS.md for
current validation, including the authorized private-card OCR experiment.
