from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import io
import json
import shutil
import threading
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import cv2
from fastapi.testclient import TestClient
import numpy as np
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from bankwise.api.main import create_app
from bankwise.core.audit import verify_audit_file
from bankwise.core.logging import LoggingSettings, configure_logging
from bankwise.core.passwords import hash_password
from bankwise.core.pii import Sensitive
from bankwise.core.security import Principal
from bankwise.database.models import IdentityRequest, User, UserRole
from bankwise.database.accounts import normalize_username
from bankwise.integration.id_ocr_adapter import IdOcrAdapter

ROOT = Path(__file__).resolve().parents[3]

PASSWORD = "Synthetic-test-password-482!"
SECRET = "Synthetic-JWT-test-key-9K2a-4t7R-8v1X-6p0Z"
AUDIT_KEY = "Synthetic-audit-test-key-3L8b-6r5V-2m1H-9q0T"


def enrol_phone(api, username, phone="+201055500001"):
    """Simulate the operator's out-of-band phone verification (signup itself is unchanged)."""
    with api.db.session() as session, session.begin():
        user = session.scalar(select(User).where(User.username == normalize_username(username)))
        assert user is not None
        user.phone_e164 = phone
        user.phone_verified_at = datetime.now(timezone.utc)


def full_login_response(api, username, password):
    """Complete both login steps through the public API using the development-only mock SMS inbox."""
    enrol_phone(api, username)
    challenge = api.client.post('/api/v1/auth/login', json={'username': username, 'password': password})
    assert challenge.status_code == 200, challenge.text
    challenge_id = challenge.json()['challenge_id']
    delivered = api.client.get('/api/v1/auth/dev/otp/' + challenge_id)
    assert delivered.status_code == 200, delivered.text
    verified = api.client.post('/api/v1/auth/login/otp/verify',
                               json={'challenge_id': challenge_id, 'otp': delivered.json()['otp']})
    assert verified.status_code == 200, verified.text
    return verified


def full_login(api, username, password):
    return full_login_response(api, username, password).json()['access_token']


class Pipeline:
    def __init__(self):
        self.calls = 0
        self.error = None
        self.entered = self.release = None

    def process(self, *, front, back):
        self.calls += 1
        assert isinstance(front, np.ndarray) and isinstance(back, np.ndarray)
        if self.entered is not None:
            self.entered.set()
            assert self.release.wait(5)
        if self.error:
            raise self.error
        return SimpleNamespace(public={"national_id": "29901010101010", "full_name": "PRIVATEPERSON",
            "all_text_lines": ["PRIVATEPERSON"], "decision": {"status": "MANUAL_REVIEW"}})


@pytest.fixture(scope="module")
def password_hash():
    return hash_password(PASSWORD)


@pytest.fixture
def api(migrated_database, tmp_path, password_hash):
    db = migrated_database
    directory = tmp_path / "configs"
    shutil.copytree(ROOT / "configs", directory)
    users = {}
    with db.session() as session, session.begin():
        for i, (name, roles) in enumerate((("alice", ["identity_processor", "operator"]),
                            ("bob", ["identity_processor", "operator"]), ("reader", ["operator"]),
                            ("processor", ["identity_processor"]))):
            u = User(username=name, password_hash=password_hash, phone_e164=f"+2010000000{i}",
                     phone_verified_at=datetime.now(timezone.utc), roles=[UserRole(role=r) for r in roles])
            session.add(u)
            session.flush()
            users[name] = Principal(str(u.id), frozenset(roles))
    pipeline = Pipeline()
    env = {"BANKWISE_ENV": "development", "BANKWISE_JWT_SECRET": SECRET,
           "BANKWISE_AUDIT_HMAC_KEY": AUDIT_KEY, "BANKWISE_DATABASE_URL": db.settings.url.reveal()}
    app = create_app(directory=directory, environ=env, root=tmp_path, processor_factory=lambda: IdOcrAdapter(pipeline))
    ok, data = cv2.imencode(".png", np.full((400, 640, 3), 180, dtype=np.uint8))
    assert ok
    with TestClient(app) as client:
        def headers(who="alice", key=None):
            return {"Authorization": "Bearer " + app.state.runtime.service.issue_token(users[who]),
                    "Idempotency-Key": str(key or uuid4())}
        def submit(who="alice", headers_override=None, files=None):
            return client.post("/api/v1/identity/verify", headers=headers_override or headers(who),
                files=files if files is not None else {"front": ("front.png", data.tobytes(), "image/png"),
                                                      "back": ("back.png", data.tobytes(), "image/png")})
        yield SimpleNamespace(client=client, app=app, db=db, pipeline=pipeline, headers=headers,
                              submit=submit, users=users, image=data.tobytes(), root=tmp_path, env=env)


def test_health_and_openapi(api):
    assert api.client.get("/health/live").json() == {"status": "live"}
    assert api.client.get("/health/ready").json() == {"status": "ready"}
    schema = api.client.get("/openapi.json").json()
    assert "/api/v1/auth/login" in schema["paths"]
    assert "/api/v1/identity/verify" in schema["paths"]


def test_readiness_failure_is_sanitized_but_liveness_stays_live(api, monkeypatch):
    def broken():
        raise RuntimeError("postgresql://password@privatehost secret")
    monkeypatch.setattr(api.app.state.database, "ready", broken)
    response = api.client.get("/health/ready")
    assert response.status_code == 503
    assert "privatehost" not in response.text and "password" not in response.text
    assert api.client.get("/health/live").status_code == 200


def test_login_logout_and_revocation(api):
    response = full_login_response(api, " ALICE ", PASSWORD)
    token = response.json()["access_token"]
    header = {"Authorization": "Bearer " + token}
    assert response.headers["cache-control"] == "no-store"
    assert api.client.post("/api/v1/auth/logout", headers=header).status_code == 204
    result = api.submit(headers_override=dict(header, **{"Idempotency-Key": str(uuid4())}))
    assert result.status_code == 401 and result.headers["www-authenticate"] == "Bearer"
    assert api.pipeline.calls == 0


@pytest.mark.parametrize("username,password", [("alice", "wrong"), ("unknown", PASSWORD)])
def test_bad_credentials_have_same_generic_error(api, username, password):
    r = api.client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 401
    assert r.json()["error"] == {"code": "authentication_failed", "message": "Authentication failed."}


def test_login_lockout_also_blocks_valid_bearer_and_spoofed_forwarded_ip(api):
    header = api.headers()
    for _ in range(5):
        assert api.client.post("/api/v1/auth/login", json={"username": "alice", "password": "wrong"}).status_code == 401
    r = api.submit(headers_override=dict(header, **{"X-Forwarded-For": "1.2.3.4"}))
    assert r.status_code == 429 and "retry-after" in r.headers
    assert api.pipeline.calls == 0


def test_username_budget_applies_across_clients(api):
    security = api.app.state.runtime.service
    from bankwise.core.errors import AuthenticationError, RateLimitExceeded
    for i, username in enumerate(("alice", "ALICE", " Alice ", "ＡＬＩＣＥ", "alice")):
        with pytest.raises(AuthenticationError):
            security.login(username, "wrong", f"ip:client-{i}", api.app.state.accounts.verify)
    with pytest.raises(RateLimitExceeded):
        security.login("alice", PASSWORD, "ip:new-client", api.app.state.accounts.verify)


def test_api_schema_errors_do_not_echo_secrets_or_input(api):
    for body in ({"username": "alice", "password": "x" * 257},
                 {"username": "alice", "password": PASSWORD, "roles": ["admin"]}):
        r = api.client.post("/api/v1/auth/login", json=body)
        assert r.status_code == 422
        assert PASSWORD not in r.text and "roles" not in r.text


def test_unknown_disabled_and_changed_role_tokens_are_denied(api):
    header = api.headers()
    with api.db.session() as session, session.begin():
        session.get(User, UUID(api.users["alice"].subject_id)).active = False
    assert api.submit(headers_override=header).status_code == 401
    with api.db.session() as session, session.begin():
        u = session.get(User, UUID(api.users["alice"].subject_id))
        u.active = True
        u.roles = [UserRole(role="operator")]
    assert api.submit(headers_override=header).status_code == 401
    assert api.pipeline.calls == 0


def test_permission_denied_before_multipart_or_ocr(api):
    r = api.client.post("/api/v1/identity/verify", headers=api.headers("reader"), content=b"malformed")
    assert r.status_code == 403 and api.pipeline.calls == 0
    with api.db.session() as session:
        assert session.scalar(select(IdentityRequest)) is None


@pytest.mark.parametrize("authorization", ["", "Bearer bad.token", "Basic abc"])
def test_authentication_required(api, authorization):
    r = api.submit(headers_override={"Authorization": authorization, "Idempotency-Key": str(uuid4())})
    assert r.status_code == 401 and api.pipeline.calls == 0


def test_identity_success_persists_only_metadata_and_is_owner_scoped(api):
    log = io.StringIO()
    configure_logging(LoggingSettings(), log)
    r = api.submit()
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "completed" and body["decision"] == "MANUAL_REVIEW"
    assert "not proof" in body["notice"]
    route = r.headers["location"]
    assert api.client.get(route, headers=api.headers()).status_code == 200
    assert api.client.get(route, headers=api.headers("bob")).status_code == 404
    assert api.client.get("/api/v1/identity/requests/" + str(uuid4()), headers=api.headers()).status_code == 404
    with api.db.session() as session:
        row = session.get(IdentityRequest, UUID(body["request_id"]))
        assert str(row.owner_id) == api.users["alice"].subject_id
        assert row.decision == "MANUAL_REVIEW"
        persisted = repr(session.execute(text("SELECT * FROM identity_requests")).all())
    audit_file = api.root / "outputs/audit/audit.jsonl"
    assert verify_audit_file(audit_file, Sensitive(AUDIT_KEY)).ok
    combined = r.text + log.getvalue() + audit_file.read_text() + persisted
    for secret in ("29901010101010", "PRIVATEPERSON", PASSWORD, SECRET, "all_text_lines", "password_hash"):
        assert secret not in combined
    assert api.pipeline.calls == 1
    assert not list((api.root / "outputs/secure").iterdir())


def test_read_requires_permission_even_for_owner(api):
    r = api.submit("processor")
    assert r.status_code == 201
    assert api.client.get(r.headers["location"], headers=api.headers("processor")).status_code == 403


def test_idempotent_replay_returns_same_request_without_ocr(api):
    header = api.headers()
    first = api.submit(headers_override=header)
    second = api.submit(headers_override=header)
    assert first.status_code == 201 and second.status_code == 200, second.text
    assert first.json() == second.json()
    assert api.pipeline.calls == 1


def test_idempotency_key_is_scoped_to_owner(api):
    key = uuid4()
    first = api.submit(headers_override=api.headers("alice", key))
    second = api.submit(headers_override=api.headers("bob", key))
    assert first.status_code == second.status_code == 201
    assert first.json()["request_id"] != second.json()["request_id"]
    assert api.pipeline.calls == 2


def test_concurrent_retry_does_not_duplicate_processing(api):
    api.pipeline.entered, api.pipeline.release = threading.Event(), threading.Event()
    header = api.headers()
    with ThreadPoolExecutor() as executor:
        first = executor.submit(api.submit, headers_override=header)
        assert api.pipeline.entered.wait(5)
        try:
            r = api.submit(headers_override=header)
            assert r.status_code == 409, r.text
        finally:
            api.pipeline.release.set()
        assert first.result(timeout=5).status_code == 201
    assert api.pipeline.calls == 1


def test_rate_limit_is_charged_once_per_submission(api):
    for _ in range(5):
        assert api.submit().status_code == 201
    r = api.submit()
    assert r.status_code == 429 and api.pipeline.calls == 5


@pytest.mark.parametrize("files", [
    {"front": ("a.png", b"bad", "image/png"), "back": ("b.png", b"bad", "image/png")},
    {"front": ("a.gif", b"GIF89", "image/gif")},
    {"front": ("a.png", b"bad", "image/png"), "owner_id": ("id", b"bob", "image/png")},
])
def test_invalid_uploads_are_rejected_without_ocr(api, files):
    assert api.submit(files=files).status_code == 422
    assert api.pipeline.calls == 0


def test_oversized_total_body_and_image_are_rejected(api):
    r = api.client.post("/api/v1/identity/verify", headers=dict(api.headers(), **{"Content-Length": "999999999"}))
    assert r.status_code == 413
    r = api.submit(files={"front": ("a.png", b"x" * (5 * 1024 * 1024 + 1), "image/png"),
                          "back": ("b.png", api.image, "image/png")})
    assert r.status_code == 413, r.text
    assert api.pipeline.calls == 0


def test_truncated_multipart_is_rejected(api):
    r = api.client.post("/api/v1/identity/verify", headers=dict(api.headers(), **{
        "Content-Type": "multipart/form-data; boundary=test"}),
        content=b'--test\r\nContent-Disposition: form-data; name="front"\r\nContent-Type: image/png\r\n\r\ntruncated')
    assert r.status_code == 422 and api.pipeline.calls == 0


def test_ocr_failure_is_safe_and_retry_does_not_rerun(api):
    api.pipeline.error = RuntimeError("PRIVATEPERSON 29901010101010 password=secret")
    header = api.headers()
    first = api.submit(headers_override=header)
    second = api.submit(headers_override=header)
    assert first.status_code == second.status_code == 500
    assert "PRIVATEPERSON" not in first.text + second.text
    assert api.pipeline.calls == 1
    with api.db.session() as session:
        row = session.scalar(select(IdentityRequest))
        assert row.status == "failed" and row.error_code == "internal_error" and row.decision is None


def test_processing_audit_failure_prevents_success_and_persistence_of_decision(api, monkeypatch):
    header = api.headers()
    original = api.app.state.runtime.audit_sink.write
    def broken(event):
        if event.event_type == "identity.process":
            raise OSError("PRIVATEPERSON audit disk")
        original(event)
    monkeypatch.setattr(api.app.state.runtime.audit_sink, "write", broken)
    r = api.submit(headers_override=header)
    assert r.status_code == 500 and "PRIVATEPERSON" not in r.text
    assert api.app.state.identity.audit_failures == 1
    with api.db.session() as session:
        row = session.scalar(select(IdentityRequest))
        assert row.status == "failed" and row.decision is None


def test_database_failure_before_reservation_prevents_ocr(api, monkeypatch):
    from bankwise.database.identity_requests import RequestPersistence
    def broken(*args):
        raise OperationalError("INSERT secret", {"password": PASSWORD}, Exception("PRIVATEPERSON"))
    monkeypatch.setattr(RequestPersistence, "started", broken)
    r = api.submit()
    assert r.status_code == 503 and "PRIVATEPERSON" not in r.text and PASSWORD not in r.text
    assert api.pipeline.calls == 0


def test_database_failure_after_ocr_is_not_fake_success(api, monkeypatch):
    from bankwise.database.identity_requests import RequestPersistence
    def broken(*args):
        raise OperationalError("UPDATE secret", {}, Exception("PRIVATEPERSON"))
    monkeypatch.setattr(RequestPersistence, "succeeded", broken)
    r = api.submit()
    assert r.status_code == 503 and "PRIVATEPERSON" not in r.text
    with api.db.session() as session:
        row = session.scalar(select(IdentityRequest))
        assert row.status == "failed" and row.decision is None


def test_expired_records_are_hidden_and_keys_not_silently_reused(api):
    header = api.headers()
    r = api.submit(headers_override=header)
    with api.db.session() as session, session.begin():
        row = session.get(IdentityRequest, UUID(r.json()["request_id"]))
        row.created_at = datetime.now(timezone.utc) - timedelta(days=40)
        row.expires_at = datetime.now(timezone.utc) - timedelta(days=10)
    assert api.client.get(r.headers["location"], headers=api.headers()).status_code == 404
    assert api.submit(headers_override=header).status_code == 409
    assert api.pipeline.calls == 1


def test_missing_or_malformed_idempotency_key_and_request_ids(api):
    for key in ("bad", ""):
        header = api.headers()
        header["Idempotency-Key"] = key
        assert api.submit(headers_override=header).status_code == 422
    assert api.client.get("/api/v1/identity/requests/not-a-uuid", headers=api.headers()).status_code == 422
    assert api.pipeline.calls == 0


def test_otp_verify_audit_failure_never_releases_token(api, monkeypatch):
    def broken(event):
        if event.event_type == "auth.token_issued":
            raise OSError("private path")
    monkeypatch.setattr(api.app.state.runtime.audit_sink, "write", broken)
    challenge = api.client.post("/api/v1/auth/login", json={"username": "alice", "password": PASSWORD})
    assert challenge.status_code == 200, challenge.text
    challenge_id = challenge.json()["challenge_id"]
    code = api.client.get("/api/v1/auth/dev/otp/" + challenge_id).json()["otp"]
    r = api.client.post("/api/v1/auth/login/otp/verify", json={"challenge_id": challenge_id, "otp": code})
    assert r.status_code == 500 and "access_token" not in r.text
    assert api.app.state.runtime.service.audit_failures == 1


def test_read_and_replay_are_fail_closed_on_audit_failure(api, monkeypatch):
    header = api.headers()
    r = api.submit(headers_override=header)
    def broken(event):
        raise OSError("private path")
    monkeypatch.setattr(api.app.state.runtime.audit_sink, "write", broken)
    assert api.client.get(r.headers["location"], headers=header).status_code == 500
    assert api.submit(headers_override=header).status_code == 500
    assert api.app.state.runtime.service.audit_failures == 2
    assert api.pipeline.calls == 1


def test_upload_does_not_spool_and_client_request_id_cannot_choose_record(api, monkeypatch):
    import tempfile
    def forbidden(*args, **kwargs):
        pytest.fail("upload spooled to disk")
    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", forbidden)
    forged = str(uuid4())
    header = dict(api.headers(), **{"X-Request-ID": forged})
    r = api.submit(headers_override=header)
    assert r.status_code == 201
    assert r.json()["request_id"] != forged and r.headers["x-request-id"] != forged


def test_readiness_dependency_can_be_overridden(api):
    from bankwise.api.deps import get_database
    class NotReady:
        def ready(self):
            raise RuntimeError("unavailable")
    api.app.dependency_overrides[get_database] = lambda: NotReady()
    try:
        assert api.client.get("/health/ready").status_code == 503
    finally:
        api.app.dependency_overrides.clear()


def test_migration_missing_makes_readiness_fail_without_creating_tables(api):
    with api.db.engine.begin() as connection:
        connection.execute(text("ALTER TABLE alembic_version RENAME TO hidden_alembic_version"))
    try:
        assert api.client.get("/health/ready").status_code == 503
        assert api.client.get("/health/live").status_code == 200
    finally:
        with api.db.engine.begin() as connection:
            connection.execute(text("ALTER TABLE hidden_alembic_version RENAME TO alembic_version"))


def test_failed_commit_rolls_back_and_returns_connection(api, monkeypatch):
    from bankwise.database.identity_requests import RequestPersistence
    succeed = RequestPersistence.succeeded
    def failed_flush(self, summary):
        from sqlalchemy import event
        def break_commit(session):
            raise OperationalError("UPDATE hidden", {}, Exception("private"))
        event.listen(self.session, "before_commit", break_commit, once=True)
        return succeed(self, summary)
    monkeypatch.setattr(RequestPersistence, "succeeded", failed_flush)
    response = api.submit()
    assert response.status_code == 503
    assert api.app.state.database.engine.pool.checkedout() == 0
    with api.db.session() as session:
        row = session.scalar(select(IdentityRequest))
        assert row.status == "failed" and row.decision is None


def test_operator_provisioning_and_retention_commands(api, monkeypatch, capsys):
    # API stopped while invoking CLI: durable audit has a single writer.
    from bankwise.database.cli import main
    for key, value in api.env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("BANKWISE_CONFIG_DIR", str(api.root / "configs"))
    monkeypatch.chdir(api.root)
    monkeypatch.setattr("getpass.getpass", lambda prompt: PASSWORD)
    # No API request occurs while CLI owns the file sink.
    assert main(["create-user", "--username", "new-user", "--roles", "operator"]) == 0
    with api.db.session() as session:
        user = session.scalar(select(User).where(User.username == "new-user"))
        assert user is not None and user.password_hash.startswith("$argon2id$")
    assert PASSWORD not in capsys.readouterr().out


def test_maintenance_purges_only_terminal_expired_records(api, monkeypatch):
    from bankwise.database.cli import main
    now = datetime.now(timezone.utc)
    rows = []
    with api.db.session() as session, session.begin():
        for status, expired in (("processing", True), ("failed", True), ("failed", False)):
            row = IdentityRequest(owner_id=UUID(api.users["alice"].subject_id), idempotency_key=uuid4(),
                status=status, error_code=None if status == "processing" else "internal_error",
                created_at=now - timedelta(days=40), completed_at=None if status == "processing" else now,
                expires_at=now + timedelta(days=-1 if expired else 1))
            session.add(row)
            session.flush()
            rows.append(row.id)
    for key, value in api.env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("BANKWISE_CONFIG_DIR", str(api.root / "configs"))
    monkeypatch.chdir(api.root)
    assert main(["purge-expired"]) == 0
    with api.db.session() as session:
        assert session.get(IdentityRequest, rows[0]) is not None
        assert session.get(IdentityRequest, rows[1]) is None
        assert session.get(IdentityRequest, rows[2]) is not None
    assert main(["interrupt-request", str(rows[0])]) == 0
    with api.db.session() as session:
        assert session.get(IdentityRequest, rows[0]).status == "interrupted"
    assert main(["purge-expired"]) == 0
    with api.db.session() as session:
        assert session.get(IdentityRequest, rows[0]) is None
        assert session.get(IdentityRequest, rows[2]) is not None


def test_lifespan_closes_processor_and_pool(api, tmp_path):
    class Processor:
        closed = False
        def close(self):
            self.closed = True
    processor = Processor()
    app = create_app(directory=api.root / "configs", environ=api.env, root=tmp_path / "lifecycle",
                     processor_factory=lambda: processor)
    with TestClient(app) as client:
        assert client.get("/health/ready").status_code == 200
        pool = app.state.database.engine.pool
    assert processor.closed
    assert app.state.database.engine.pool is not pool
    assert not app.state.initialized


def test_startup_failure_does_not_expose_exception_details(api, tmp_path):
    from bankwise.core.errors import ConfigError
    def broken():
        raise RuntimeError("PRIVATEPERSON secret unavailable weights")
    app = create_app(directory=api.root / "configs", environ=api.env, root=tmp_path / "broken",
                     processor_factory=broken)
    with pytest.raises(ConfigError) as error:
        with TestClient(app):
            pass
    assert "PRIVATEPERSON" not in str(error.value) and "PRIVATEPERSON" not in error.value.detail


def provision_legacy_customer(api, username):
    # Existing pre-onboarding customers remain supported; no public signup bypass.
    from bankwise.database.accounts import create_user
    from bankwise.database.models import DemoAccount, DemoCustomer
    with api.db.session() as session, session.begin():
        user = create_user(session, username, PASSWORD, ['customer','identity_processor'], api.app.state.runtime.authorizer.policy)
        session.add(DemoCustomer(owner_id=user.id, verified=False))
        session.add(DemoAccount(owner_id=user.id, balance=0, status='open'))


def test_legacy_customer_own_extraction(api):
    from bankwise.database.models import DemoAccount, DemoCustomer
    api.app.state.banking_demo = True
    body = {'username': 'new_customer', 'password': PASSWORD}
    assert api.client.post('/api/v1/auth/signup', json={**body, 'roles':['admin']}).status_code == 422
    assert api.client.post('/api/v1/auth/signup', json=body).status_code == 404
    provision_legacy_customer(api, body['username'])
    token = full_login(api, 'new_customer', PASSWORD)
    with api.db.session() as session:
        user = session.scalar(select(User).where(User.username=='new_customer'))
        assert {r.role for r in user.roles} == {'customer', 'identity_processor'}
        assert session.get(DemoCustomer, user.id).verified is False
        assert session.scalar(select(DemoAccount).where(DemoAccount.owner_id==user.id)).balance == 0
    files = {'front':('front.png', api.image, 'image/png'), 'back':('back.png', api.image, 'image/png')}
    response = api.client.post('/api/v1/identity/extract', headers={'Authorization':'Bearer '+token}, files=files)
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['fields']['national_id']=='29901010101010'
    assert 'all_text_lines' not in response.text
    assert api.client.post('/api/v1/identity/extract', files=files).status_code == 401
    assert api.client.post('/api/v1/identity/extract', headers=api.headers('processor'), files=files).status_code == 403
    assert api.client.post('/api/v1/auth/signup', json=body).status_code == 404


def test_signup_disabled_outside_demo(api):
    api.app.state.banking_demo = False
    assert api.client.post('/api/v1/auth/signup', json={'username':'new_customer','password':PASSWORD}).status_code==404


def test_own_extraction_fails_closed_on_disclosure_audit(api, monkeypatch):
    api.app.state.banking_demo = True
    body={'username':'audit_customer','password':PASSWORD}
    provision_legacy_customer(api, body['username'])
    token=full_login(api,'audit_customer',PASSWORD)
    from bankwise.core.errors import BankWiseError
    def fail(*args):
        raise BankWiseError('audit unavailable')
    monkeypatch.setattr(api.app.state.runtime.service, 'record_access', fail)
    response=api.client.post('/api/v1/identity/extract',headers={'Authorization':'Bearer '+token},
        files={'front':('f.png',api.image,'image/png'),'back':('b.png',api.image,'image/png')})
    assert response.status_code==500
    assert '29901010101010' not in response.text
    assert 'PRIVATEPERSON' not in response.text


def test_legacy_signup_cannot_create_users(api):
    api.app.state.banking_demo=True
    statuses=[api.client.post('/api/v1/auth/signup',json={'username':f'register_{i}','password':PASSWORD}).status_code for i in range(12)]
    assert set(statuses)=={404}
    with api.db.session() as session:
        assert session.scalar(select(User).where(User.username.like('register_%'))) is None


def test_identity_review_submission_decision_and_isolation(api):
    from cryptography.fernet import Fernet
    from bankwise.database.models import DemoCustomer, IdentityReview
    api.app.state.banking_demo=True
    api.app.state.review_key=Fernet.generate_key().decode()
    body={'username':'review_customer','password':PASSWORD}
    provision_legacy_customer(api, body['username'])
    login=full_login_response(api,'review_customer',PASSWORD).json()
    own={'Authorization':'Bearer '+login['access_token']}
    assert login['kyc_pending']
    assert api.client.get('/api/v1/identity/status',headers=own).json()=={'verified':False,'review':None}
    files={'front':('f.png',api.image,'image/png'),'back':('b.png',api.image,'image/png')}
    response=api.client.post('/api/v1/identity/reviews',headers=own,files=files)
    assert response.status_code==201,response.text
    rid=response.json()['id']
    assert response.json()['status']=='pending'
    assert api.client.post('/api/v1/identity/reviews',headers=own,files=files).status_code==409
    assert api.client.get('/api/v1/identity/reviews',headers=own).status_code==403
    assert api.client.post('/api/v1/identity/reviews/'+rid+'/decision',headers=own,json={'decision':'approved','reason':'checked'}).status_code==403
    with api.db.session() as session,session.begin():
        u=User(username='review_officer',password_hash=hash_password(PASSWORD),roles=[UserRole(role='identity_reviewer')])
        session.add(u);session.flush()
        officer=Principal(str(u.id),frozenset({'identity_reviewer'}))
        row=session.get(IdentityReview,UUID(rid))
        assert b'29901010101010' not in row.encrypted_payload
        assert not session.get(DemoCustomer,row.owner_id).verified
    admin={'Authorization':'Bearer '+api.app.state.runtime.service.issue_token(officer)}
    listing=api.client.get('/api/v1/identity/reviews',headers=admin)
    assert listing.json()['reviews'][0]['username']=='review_customer'
    assert 'encrypted_payload' not in listing.text and '29901010101010' not in listing.text
    detail=api.client.get('/api/v1/identity/reviews/'+rid,headers=admin)
    assert detail.status_code==200
    assert detail.json()['result']['fields']['national_id']=='29901010101010'
    result=api.client.post('/api/v1/identity/reviews/'+rid+'/decision',headers=admin,json={'decision':'approved','reason':'checked'})
    assert result.status_code==200 and result.json()['verified']
    assert api.client.get('/api/v1/identity/status',headers=own).json()['verified']
    assert full_login_response(api,'review_customer',PASSWORD).json()['kyc_pending'] is False
    assert api.client.post('/api/v1/identity/reviews/'+rid+'/decision',headers=admin,json={'decision':'approved','reason':'checked'}).status_code==409
    assert api.client.get('/api/v1/identity/reviews/'+rid,headers=admin).status_code==404
    with api.db.session() as session:
        assert session.get(IdentityReview,UUID(rid)).encrypted_payload is None


def test_review_rejection_expiry_and_self_approval(api):
    from cryptography.fernet import Fernet
    from datetime import datetime,timezone,timedelta
    from bankwise.database.models import DemoCustomer,IdentityReview
    api.app.state.review_key=Fernet.generate_key().decode();api.app.state.banking_demo=True
    with api.db.session() as session,session.begin():
        person=User(username='review_owner',password_hash=hash_password(PASSWORD),roles=[UserRole(role='customer'),UserRole(role='identity_reviewer')])
        officer=User(username='review_other',password_hash=hash_password(PASSWORD),roles=[UserRole(role='identity_reviewer')])
        session.add_all([person,officer]);session.flush()
        session.add(DemoCustomer(owner_id=person.id,verified=False))
        row=IdentityReview(owner_id=person.id,encrypted_payload=b'encrypted',expires_at=datetime.now(timezone.utc)+timedelta(days=1))
        session.add(row);session.flush();rid=str(row.id);ownerid=person.id
        owner=Principal(str(person.id),frozenset({'customer','identity_reviewer'}))
        other=Principal(str(officer.id),frozenset({'identity_reviewer'}))
    own={'Authorization':'Bearer '+api.app.state.runtime.service.issue_token(owner)}
    admin={'Authorization':'Bearer '+api.app.state.runtime.service.issue_token(other)}
    path='/api/v1/identity/reviews/'+rid
    assert api.client.get(path,headers=own).status_code==403
    assert api.client.post(path+'/decision',headers=own,json={'decision':'approved','reason':'checked'}).status_code==403
    assert api.client.post(path+'/decision',headers=admin,json={'decision':'rejected','reason':'unclear_images'}).status_code==200
    state=api.client.get('/api/v1/identity/status',headers=own).json()
    assert not state['verified'] and state['review']['reason']=='unclear_images'
    with api.db.session() as session,session.begin():
        assert session.get(IdentityReview,UUID(rid)).encrypted_payload is None
        expired=IdentityReview(owner_id=ownerid,encrypted_payload=b'expired',expires_at=datetime.now(timezone.utc)-timedelta(seconds=1))
        session.add(expired);session.flush();eid=str(expired.id)
    assert api.client.post('/api/v1/identity/reviews/'+eid+'/decision',headers=admin,json={'decision':'approved','reason':'checked'}).status_code==409
    state=api.client.get('/api/v1/identity/status',headers=own).json()
    assert state['review']['reason']=='expired' and not state['verified']
    with api.db.session() as session:
        assert session.get(IdentityReview,UUID(eid)).encrypted_payload is None
