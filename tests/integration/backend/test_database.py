from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from bankwise.database.models import IdentityRequest, User, UserRole

ROOT = Path(__file__).resolve().parents[3]


def user():
    return User(username="test-user", password_hash="$argon2id$test", roles=[UserRole(role="operator")])


def test_migrations_are_reversible_and_match_models(migrated_database):
    db = migrated_database
    cfg = Config(str(ROOT / "alembic.ini"))
    db.ready()
    command.check(cfg)
    command.downgrade(cfg, "base")
    assert "users" not in inspect(db.engine).get_table_names()
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")
    db.ready()


def test_session_rollback_and_connections_return_to_pool(migrated_database):
    db = migrated_database
    with pytest.raises(RuntimeError):
        with db.session() as session, session.begin():
            session.add(user())
            session.flush()
            raise RuntimeError("abort")
    with db.session() as session:
        assert session.scalar(select(User)) is None
        session.add(user())  # no implicit commit
    with db.session() as session:
        assert session.scalar(select(User)) is None
    assert db.engine.pool.checkedout() == 0


def test_unique_username_and_foreign_keys(migrated_database):
    db = migrated_database
    with db.session() as session, session.begin():
        u = user()
        session.add(u)
    with pytest.raises(IntegrityError), db.session() as session, session.begin():
        session.add(user())
    with pytest.raises(IntegrityError), db.session() as session, session.begin():
        session.add(UserRole(user_id=uuid4(), role="operator"))
    with db.session() as session:
        found = session.get(User, u.id)
        assert found.roles[0].user_id == u.id
        assert found.roles[0].user is found
    assert db.engine.pool.checkedout() == 0


@pytest.mark.parametrize("changes", [
    {"status": "invented"}, {"decision": "VERIFIED"}, {"error_code": "raw OCR"},
    {"status": "completed"}, {"expires_at": datetime(2000, 1, 1, tzinfo=timezone.utc)},
    {"owner_id": uuid4()},
])
def test_invalid_identity_writes_are_rejected(migrated_database, changes):
    db = migrated_database
    with db.session() as session, session.begin():
        u = user()
        session.add(u)
    args = dict(owner_id=u.id, idempotency_key=uuid4(), status="processing",
                expires_at=datetime.now(timezone.utc) + timedelta(days=30))
    args.update(changes)
    with pytest.raises(IntegrityError), db.session() as session, session.begin():
        session.add(IdentityRequest(**args))


def test_idempotency_unique_per_owner_and_user_delete_restricted(migrated_database):
    db = migrated_database
    key = uuid4()
    with db.session() as session, session.begin():
        u = user()
        session.add(u)
        session.flush()
        session.add(IdentityRequest(owner_id=u.id, idempotency_key=key, status="processing",
                                   expires_at=datetime.now(timezone.utc) + timedelta(days=30)))
    with pytest.raises(IntegrityError), db.session() as session, session.begin():
        session.add(IdentityRequest(owner_id=u.id, idempotency_key=key, status="processing",
                                   expires_at=datetime.now(timezone.utc) + timedelta(days=30)))
    with pytest.raises(IntegrityError), db.session() as session, session.begin():
        session.execute(text("DELETE FROM users WHERE id = :id"), {"id": u.id})
