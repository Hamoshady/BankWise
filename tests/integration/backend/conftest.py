"""Opt-in tests own a disposable PostgreSQL container; never accept a database URL."""
import os
from pathlib import Path
import secrets
import subprocess
import time
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import text

from bankwise.core.pii import Sensitive
from bankwise.database.config import DatabaseSettings
from bankwise.database.session import Database

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="session")
def postgres_settings():
    if os.environ.get("BANKWISE_TEST_POSTGRES") != "1":
        pytest.skip("set BANKWISE_TEST_POSTGRES=1 to create a disposable Docker PostgreSQL database")
    name = "bankwise-tests-" + uuid4().hex[:12]
    password = secrets.token_urlsafe(32)
    env = dict(os.environ, POSTGRES_PASSWORD=password)
    subprocess.run(["docker", "run", "--detach", "--name", name, "--label", "bankwise.disposable-test=true",
                    "--tmpfs", "/var/lib/postgresql/data", "-e", "POSTGRES_PASSWORD", "-e", "POSTGRES_USER=bankwise_test",
                    "-e", "POSTGRES_DB=bankwise_test", "-p", "127.0.0.1::5432", "postgres:16-alpine"],
                   env=env, check=True, capture_output=True)
    try:
        for _ in range(60):
            # The image briefly starts a socket-only server during initdb. Wait for the final TCP server.
            ready = subprocess.run(["docker", "exec", name, "pg_isready", "-h", "127.0.0.1", "-U", "bankwise_test"],
                                   capture_output=True)
            if ready.returncode == 0:
                break
            time.sleep(.25)
        else:
            pytest.fail("disposable PostgreSQL did not start")
        port = subprocess.check_output(["docker", "inspect", "--format",
            '{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostPort}}', name], text=True).strip()
        yield DatabaseSettings(Sensitive(f"postgresql+psycopg://bankwise_test:{password}@127.0.0.1:{port}/bankwise_test"))
    finally:
        subprocess.run(["docker", "rm", "--force", name], check=True, capture_output=True)


@pytest.fixture
def migrated_database(postgres_settings, monkeypatch):
    monkeypatch.setenv("BANKWISE_DATABASE_URL", postgres_settings.url.reveal())
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "head")
    db = Database(postgres_settings)
    with db.engine.begin() as connection:
        connection.execute(text("TRUNCATE identity_requests, user_roles, users CASCADE"))
    try:
        yield db
    finally:
        db.close()
