"""One session per operation/request; explicit transactions, no startup DDL."""
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from bankwise.database.config import DatabaseSettings

_MIGRATIONS = Path(__file__).with_name("migrations")


@lru_cache(maxsize=1)
def schema_revision() -> str:
    """Read the Alembic head from the migration scripts instead of a hand-maintained constant."""
    from alembic.script import ScriptDirectory

    head = ScriptDirectory(str(_MIGRATIONS)).get_current_head()
    if not head:
        raise RuntimeError("database migrations have no head revision")
    return head


SCHEMA_REVISION = schema_revision()


class Database:
    def __init__(self, settings: DatabaseSettings):
        self.settings = settings
        self.engine = create_engine(
            settings.url.reveal(), pool_pre_ping=True, pool_size=settings.pool_size,
            max_overflow=0, pool_timeout=settings.pool_timeout_seconds,
            connect_args={"connect_timeout": settings.connect_timeout_seconds,
                          "options": f"-c statement_timeout=10000 -c lock_timeout=5000 -c search_path={settings.schema}"},
            echo=False, hide_parameters=True,
        )
        self.sessions = sessionmaker(self.engine, expire_on_commit=False, autoflush=False)

    @contextmanager
    def session(self):
        with self.sessions() as session:
            try:
                yield session
            except BaseException:
                session.rollback()
                raise
            # Uncommitted changes are rolled back on close. Callers explicitly commit before success.

    def ready(self):
        with self.engine.connect() as connection:
            if connection.scalar(text("SELECT current_schema()")) != self.settings.schema:
                raise RuntimeError("configured database schema is missing or inaccessible")
            rows = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
            if rows != [SCHEMA_REVISION]:
                raise RuntimeError("database schema is not current")
            connection.execute(text("SELECT id FROM users LIMIT 0"))
            connection.execute(text("SELECT user_id FROM user_roles LIMIT 0"))
            connection.execute(text("SELECT id FROM identity_requests LIMIT 0"))

            connection.execute(text("SELECT id FROM rag_documents LIMIT 0"))
            connection.execute(text("SELECT document_id, dimension FROM rag_document_chunks LIMIT 0"))
            connection.execute(text("SELECT id, state FROM assistant_conversations LIMIT 0"))
            connection.execute(text("SELECT id, status FROM assistant_operations LIMIT 0"))
            connection.execute(text("SELECT id, status FROM demo_accounts LIMIT 0"))
            connection.execute(text("SELECT id, status FROM otp_challenges LIMIT 0"))

    def close(self):
        self.engine.dispose()
