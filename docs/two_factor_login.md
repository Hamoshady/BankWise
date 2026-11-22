# Two-factor login (username + password + OTP)

Login is a two-step flow. The subsequently added reviewed signup workflow is documented in `customer_onboarding.md`; existing banking transactions and session/logout behavior remain supported.
The project keeps its token-based architecture: a fully privileged JWT is issued **only** after OTP verification.

## Flow

1. `POST /api/v1/auth/login` with `{"username", "password"}`
   - verifies the password (Argon2id), the active flag and the account's **previously verified phone** when using real SMS;
   - rate-limits by client and by an opaque account digest (existing `auth_attempt` policy);
   - generates a random 6-digit code, stores only its Argon2id digest in a new `otp_challenges` row, sends the
     code by SMS, and returns `{"challenge_id", "expires_in": 300, "resend_after_seconds": 60}`.
   - **No access token, phone number or code is returned.** Failure (bad password, unknown user, inactive user or
     user without a verified phone) is always the same generic `401 authentication_failed`.
2. `POST /api/v1/auth/login/otp/verify` with `{"challenge_id", "otp"}`
   - locks the challenge row (`SELECT ... FOR UPDATE`), checks status, 5-minute expiry and attempt count;
   - verifies the digest, marks the challenge `consumed`, and only then calls the existing audited
     `SecurityService.issue_token()`;
   - returns the normal `TokenResponse` (`access_token`, `expires_in`, `kyc_pending`). Replaying the same
     challenge returns `401 otp_failed / challenge_consumed`.
3. `POST /api/v1/auth/login/otp/resend` with `{"challenge_id"}`
   - enforces a 60-second cooldown and the `otp_resend` rate limit, supersedes all earlier codes in the flow,
     returns a new `challenge_id`. Old codes are rejected with reason `superseded`.

The Streamlit UI (`ui/app.py`) shows a dedicated OTP screen after step 1 (remaining time, Verify, Resend with
cooldown, back to login) and stores the token only after step 2 succeeds. Logout and expiry behavior are unchanged.

**Transport:** all authentication requests must be served over HTTPS (TLS terminated at your ingress; no plaintext
HTTP in production). As before, run Uvicorn with `--no-proxy-headers` unless a trusted proxy is configured, so a
spoofed `X-Forwarded-For` cannot change the rate-limit client key.

OTP error reasons are fixed challenge outcomes (`invalid_otp`, `expired`, `exhausted`, `blocked`,
`challenge_consumed`, `superseded`, `invalid_challenge`); they never describe the account. Codes, phone numbers,
passwords and secrets are never written to API responses, the database (plaintext) or logs/audit files.

## Database

New Alembic revision **`0009_two_factor_login`** (safe, non-destructive):

- `users.phone_e164` and `users.phone_verified_at` — nullable, **no backfill**; a partial-style unique index
  (`uq_users_phone_e164`) prevents two accounts sharing a verified number;
- `otp_challenges` — `id`, `user_id`, `flow_id`, `phone_e164`, `otp_hash` (digest only), `status`, `attempts`
  (0–5), `created_at`, `expires_at`, `consumed_at`, plus indexes and check constraints.

Existing users, roles, balances and all other tables are untouched. Apply with `alembic upgrade head`.

### Enrolling a verified phone (operator, API stopped)

For legacy users using real SMS, a verified destination can be enrolled out of band once per account. New reviewed signup verifies it through its own OTP workflow:

```bash
python -m bankwise.database.cli set-phone --username alice --phone +201000000123
```

This writes an E.164 number and `phone_verified_at`, and records an `auth.phone_enrolled` audit event. With real SMS an account
without a verified phone cannot complete login (and gets the same generic error as a bad password).

## Environment variables / SMS provider

| Variable | Required | Purpose |
| --- | --- | --- |
| `BANKWISE_SMS_URL` | production only | HTTPS webhook that delivers the SMS |
| `BANKWISE_SMS_API_KEY` | production only | Bearer token for the webhook (registered for log redaction) |

The gateway receives `POST` JSON:

```json
{"to": "+201000000123", "message": "Your BankWise verification code is 123456", "reference": "<challenge id>"}
```

Non-2xx responses become a generic `503`; the code and API key are never logged. Any other provider can be wired
by implementing `bankwise.core.sms.SmsProvider` and returning it from `build_sms_provider`.

**Development mock:** with `BANKWISE_ENV=development` and no `BANKWISE_SMS_URL`, `MockSmsProvider` keeps codes in
process memory only. Production refuses to start if no real provider is configured, so there is no fixed OTP and
no production endpoint that reveals codes.

## Testing locally

1. Set `BANKWISE_ENV=development`, a database URL and the JWT/audit secrets; run `alembic upgrade head`.
2. Start the API: `uvicorn bankwise.api.main:create_app --factory --no-proxy-headers`.
3. Enrol a phone for an existing user with the `set-phone` command above.
4. Complete login (the dev-only OTP read is intentionally not available in production):

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"<password>"}'
# -> {"challenge_id":"...","expires_in":300,"resend_after_seconds":60}

curl -s http://127.0.0.1:8000/api/v1/auth/dev/otp/<challenge_id>
# -> {"challenge_id":"...","otp":"123456"}   (development only)

curl -s -X POST http://127.0.0.1:8000/api/v1/auth/login/otp/verify \
  -H 'Content-Type: application/json' \
  -d '{"challenge_id":"<challenge_id>","otp":"123456"}'
# -> the usual access_token response; use it as Authorization: Bearer <token>
```

5. Run the tests:

```bash
pytest tests/unit                                  # OTP primitives, providers, no-token step 1
BANKWISE_TEST_POSTGRES=1 pytest tests/integration/backend   # full 2FA flow on disposable PostgreSQL
```

The integration suite covers: credentials→OTP (no token), bad credentials, correct/incorrect/expired OTP,
5-attempt lockout, replay, pre-OTP dashboard denial, resend supersession, concurrent single use, no secret
leakage, and preservation of existing user records.

## Development OTP button

When the backend uses its development mock, login/resend return `development_otp: true`.
The UI displays an `OTP` button to retrieve the current challenge code, explicitly stating
that no SMS was sent. No token is issued by that button; normal verification, five-minute
expiry, five attempts and single-use checks still apply. Codes are not saved in UI session state.
Mock login also permits existing users without a verified phone, without enrolling any number
or changing phone verification state. This is a flow simulation, not proof of phone ownership.
With a real provider the button is hidden and verified-phone requirements remain enforced;
production cannot use the mock and the development code endpoint returns 404.
