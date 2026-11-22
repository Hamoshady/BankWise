"""Credentials come from the environment, never alembic.ini or log output."""
from alembic import context
from sqlalchemy import text

from bankwise.database.config import load_database_settings
from bankwise.database.models import Base
from bankwise.database.session import Database

target_metadata = Base.metadata

if context.is_offline_mode():
    context.configure(url=load_database_settings().url.reveal(), target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"}, compare_type=True)
    with context.begin_transaction():
        schema = load_database_settings().schema
        context.execute(f'SET search_path TO "{schema}"')
        context.run_migrations()
else:
    database = Database(load_database_settings())
    try:
        with database.engine.connect() as connection:
            if connection.scalar(text("SELECT current_schema()")) != database.settings.schema:
                raise RuntimeError("configured database schema must exist before migrations")
            connection.commit()
            context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        database.close()
