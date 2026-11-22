"""End-to-end two-factor login tests against the real API, PostgreSQL and JWT service.

Reuses the repository's real-security API fixture. The development-only mock SMS inbox is used solely to read
back a delivered code; production never exposes it.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import select

from bankwise.database.models import OtpChallenge, User
from test_api import PASSWORD, api, enrol_phone, full_login_response, password_hash


LOGIN = "/api/v1/auth/login"
VERIFY = "/api/v1/auth/login/otp/verify"
RESEND = "/api/v1/auth/login/otp/resend"


def challenge_for(api, username="alice", password=PASSWORD, phone="+201055500001"):
    enrol_phone(api, username, phone)
    response = api.client.post(LOGIN, json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def delivered(api, challenge_id):
    response = api.client.get("/api/v1/auth/dev/otp/" + challenge_id)
    assert response.status_code == 200, response.text
    return response.json()["otp"]


def test_credentials_only_create_a_challenge_no_token_and_no_dashboard_access(api):
    body = challenge_for(api)
    assert set(body) == {"challenge_id", "expires_in", "resend_after_seconds", "development_otp"}
    assert body["expires_in"] == 300 and body["resend_after_seconds"] == 60
    assert "access_token" not in body and "otp" not in body and "phone" not in repr(body)

    # The opaque challenge id is not a credential: protected routes still reject it.
    denied = api.client.get("/api/v1/identity/status", headers={"Authorization": "Bearer " + body["challenge_id"]})
    assert denied.status_code == 401

    with api.db.session() as session:
        row = session.get(OtpChallenge, UUID(body["challenge_id"]))
        assert row.status == "pending" and row.attempts == 0
        assert row.otp_hash.startswith("$argon2id$")
        assert delivered(api, str(row.id)) not in row.otp_hash   # digest, never the code


def test_bad_credentials_and_inactive_or_unenrolled_accounts_are_indistinguishable(api):
    unknown = api.client.post(LOGIN, json={"username": "no-such-user", "password": PASSWORD})
    wrong = api.client.post(LOGIN, json={"username": "alice", "password": "wrong-password"})
    with api.db.session() as session, session.begin():
        user = session.scalar(select(User).where(User.username == "alice"))
        user.phone_e164 = None
        user.phone_verified_at = None
    api.client.app.state.dev_sms = False
    unenrolled = api.client.post(LOGIN, json={"username": "alice", "password": PASSWORD})
    with api.db.session() as session, session.begin():
        user = session.scalar(select(User).where(User.username == "alice"))
        user.active = False
    inactive = api.client.post(LOGIN, json={"username": "alice", "password": PASSWORD})

    for response in (unknown, wrong, unenrolled, inactive):
        assert response.status_code == 401
        assert response.json()["error"] == {"code": "authentication_failed", "message": "Authentication failed."}
    with api.db.session() as session:
        assert session.scalar(select(OtpChallenge)) is None


def test_correct_otp_issues_full_session_and_cannot_be_replayed(api):
    body = challenge_for(api)
    code = delivered(api, body["challenge_id"])
    verified = api.client.post(VERIFY, json={"challenge_id": body["challenge_id"], "otp": code})
    assert verified.status_code == 200, verified.text
    token = verified.json()["access_token"]
    # A protected endpoint accepts the post-OTP token (and rejects the pre-OTP challenge id above).
    assert api.client.post("/api/v1/auth/logout", headers={"Authorization": "Bearer " + token}).status_code == 204

    replay = api.client.post(VERIFY, json={"challenge_id": body["challenge_id"], "otp": code})
    assert replay.status_code == 401
    assert replay.json()["error"]["reason"] == "challenge_consumed"
    with api.db.session() as session:
        row = session.get(OtpChallenge, UUID(body["challenge_id"]))
        assert row.status == "consumed" and row.consumed_at is not None


def test_wrong_otp_is_rejected_but_does_not_block_a_later_correct_code(api):
    body = challenge_for(api)
    code = delivered(api, body["challenge_id"])
    wrong = "000000" if code != "000000" else "111111"
    response = api.client.post(VERIFY, json={"challenge_id": body["challenge_id"], "otp": wrong})
    assert response.status_code == 401 and response.json()["error"]["reason"] == "invalid_otp"
    with api.db.session() as session:
        assert session.get(OtpChallenge, UUID(body["challenge_id"])).attempts == 1
    assert api.client.post(VERIFY, json={"challenge_id": body["challenge_id"], "otp": code}).status_code == 200


def test_expired_otp_and_five_wrong_attempts_are_blocked(api):
    expired = challenge_for(api)
    with api.db.session() as session, session.begin():
        row = session.get(OtpChallenge, UUID(expired["challenge_id"]))
        row.created_at = datetime.now(timezone.utc) - timedelta(seconds=600)
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    response = api.client.post(VERIFY, json={"challenge_id": expired["challenge_id"],
                                             "otp": delivered(api, expired["challenge_id"])})
    assert response.status_code == 401 and response.json()["error"]["reason"] == "expired"
    with api.db.session() as session:
        assert session.get(OtpChallenge, UUID(expired["challenge_id"])).status == "expired"

    blocked = challenge_for(api, "bob", phone="+201055500002")
    code = delivered(api, blocked["challenge_id"])
    wrong = "000000" if code != "000000" else "111111"
    reasons = []
    for _ in range(5):
        response = api.client.post(VERIFY, json={"challenge_id": blocked["challenge_id"], "otp": wrong})
        assert response.status_code == 401
        reasons.append(response.json()["error"]["reason"])
    assert reasons == ["invalid_otp"] * 4 + ["exhausted"]
    assert api.client.post(VERIFY, json={"challenge_id": blocked["challenge_id"], "otp": code}).json()["error"]["reason"] == "blocked"
    with api.db.session() as session:
        row = session.get(OtpChallenge, UUID(blocked["challenge_id"]))
        assert row.status == "blocked" and row.attempts == 5


def test_resend_has_a_cooldown_and_supersedes_the_previous_code(api):
    first = challenge_for(api)
    cid1, code1 = first["challenge_id"], delivered(api, first["challenge_id"])
    early = api.client.post(RESEND, json={"challenge_id": cid1})
    assert early.status_code == 429 and "retry-after" in early.headers

    with api.db.session() as session, session.begin():
        row = session.get(OtpChallenge, UUID(cid1))
        row.created_at = datetime.now(timezone.utc) - timedelta(seconds=90)
    resent = api.client.post(RESEND, json={"challenge_id": cid1})
    assert resent.status_code == 200, resent.text
    cid2, code2 = resent.json()["challenge_id"], delivered(api, resent.json()["challenge_id"])
    assert cid2 != cid1 and code2 != code1

    with api.db.session() as session:
        assert session.get(OtpChallenge, UUID(cid1)).status == "superseded"
    rejected = api.client.post(VERIFY, json={"challenge_id": cid1, "otp": code1})
    assert rejected.status_code == 401 and rejected.json()["error"]["reason"] == "superseded"
    assert api.client.post(VERIFY, json={"challenge_id": cid2, "otp": code2}).status_code == 200


def test_concurrent_verification_consumes_the_challenge_only_once(api):
    body = challenge_for(api)
    code = delivered(api, body["challenge_id"])
    def verify():
        return api.client.post(VERIFY, json={"challenge_id": body["challenge_id"], "otp": code}).status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = sorted(executor.submit(verify).result(timeout=10) for _ in range(2))
    assert statuses == [200, 401]


def test_otp_and_password_never_appear_in_responses_database_or_audit(api):
    body = challenge_for(api)
    code = delivered(api, body["challenge_id"])
    verified = api.client.post(VERIFY, json={"challenge_id": body["challenge_id"], "otp": code})
    assert verified.status_code == 200
    with api.db.session() as session:
        stored = repr(session.execute(select(OtpChallenge)).all())
    audit = (api.root / "outputs/audit/audit.jsonl").read_text()
    combined = verified.text + stored + audit
    assert code not in combined and PASSWORD not in combined and "otp_hash" not in audit


def test_existing_users_and_metadata_survive_the_two_factor_flow(api):
    with api.db.session() as session:
        before = sorted((u.id, u.username, u.password_hash, frozenset(r.role for r in u.roles))
                        for u in session.scalars(select(User)))

    verified = full_login_response(api, "alice", PASSWORD)
    assert verified.status_code == 200

    with api.db.session() as session:
        after = sorted((u.id, u.username, u.password_hash, frozenset(r.role for r in u.roles))
                       for u in session.scalars(select(User)))
        assert before == after
        assert session.scalar(select(OtpChallenge)) is not None
        assert session.scalar(select(User).where(User.username == "alice")).active is True


def test_login_never_accepts_a_supplied_phone_number(api):
    response = api.client.post(LOGIN, json={"username": "alice", "password": PASSWORD,
                                            "phone": "+201000000999"})
    assert response.status_code == 422                       # extra="forbid" on LoginRequest
    assert "201000000999" not in response.text
    with api.db.session() as session:
        assert session.scalar(select(OtpChallenge)) is None


def test_unknown_challenge_and_malformed_otp_are_rejected(api):
    unknown = api.client.post(VERIFY, json={"challenge_id": "00000000-0000-0000-0000-000000000000",
                                            "otp": "123456"})
    assert unknown.status_code == 401 and unknown.json()["error"]["reason"] == "invalid_challenge"
    body = challenge_for(api)
    malformed = api.client.post(VERIFY, json={"challenge_id": body["challenge_id"], "otp": "12ab56"})
    assert malformed.status_code == 422
    assert api.client.get("/api/v1/auth/dev/otp/" + str(UUID(int=0))).status_code == 404


def test_development_login_without_phone_does_not_verify_phone(api):
    with api.db.session() as session, session.begin():
        user = session.scalar(select(User).where(User.username == "alice"))
        user.phone_e164 = None
        user.phone_verified_at = None
    response = api.client.post(LOGIN, json={"username": "alice", "password": PASSWORD})
    assert response.status_code == 200
    body = response.json()
    assert body["development_otp"] is True
    code = delivered(api, body["challenge_id"])
    assert api.client.post(VERIFY, json={"challenge_id": body["challenge_id"], "otp": code}).status_code == 200
    with api.db.session() as session:
        user = session.scalar(select(User).where(User.username == "alice"))
        assert user.phone_verified_at is None
    api.client.app.state.dev_sms = False
    assert api.client.get("/api/v1/auth/dev/otp/" + body["challenge_id"]).status_code == 404
