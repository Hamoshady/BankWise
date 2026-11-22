"""Isolated schema migrations preserve conflicting legacy tables in the same database."""
from dataclasses import replace
from pathlib import Path
from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from bankwise.database.session import Database

ROOT = Path(__file__).resolve().parents[3]


def test_schema_migrations_leave_legacy_tables_untouched(postgres_settings, monkeypatch):
    admin = Database(postgres_settings)
    cfg = Config(str(ROOT / 'alembic.ini'))
    settings = replace(postgres_settings, schema='isolated_rag_test')
    monkeypatch.setenv('BANKWISE_DATABASE_URL', settings.url.reveal())
    monkeypatch.setenv('BANKWISE_DATABASE_SCHEMA', settings.schema)
    db = Database(settings)
    try:
        with admin.engine.begin() as conn:
            conn.execute(text('CREATE SCHEMA isolated_rag_test'))
            conn.execute(text('CREATE TABLE public.legacy_marker (id integer PRIMARY KEY)'))
            conn.execute(text('INSERT INTO public.legacy_marker VALUES (42)'))
        command.upgrade(cfg, 'head')
        command.check(cfg)
        db.ready()
        with db.engine.connect() as conn:
            assert conn.scalar(text('SELECT current_schema()')) == settings.schema
            assert conn.scalar(text('SELECT count(*) FROM users')) == 0
            assert conn.scalar(text("SELECT to_regclass('legacy_marker')")) is None
        with admin.engine.connect() as conn:
            assert conn.scalar(text('SELECT id FROM public.legacy_marker')) == 42
        command.downgrade(cfg, 'base')
        with admin.engine.connect() as conn:
            assert conn.scalar(text('SELECT id FROM public.legacy_marker')) == 42
    finally:
        db.close()
        with admin.engine.begin() as conn:
            conn.execute(text('DROP SCHEMA IF EXISTS isolated_rag_test CASCADE'))
            conn.execute(text('DROP TABLE IF EXISTS public.legacy_marker'))
        admin.close()


def test_missing_schema_fails_without_public_fallback(postgres_settings, monkeypatch):
    settings = replace(postgres_settings, schema='not_provisioned')
    db = Database(settings)
    monkeypatch.setenv('BANKWISE_DATABASE_URL', settings.url.reveal())
    monkeypatch.setenv('BANKWISE_DATABASE_SCHEMA', settings.schema)
    try:
        with pytest.raises(RuntimeError, match='schema'):
            db.ready()
        with pytest.raises(RuntimeError, match='schema'):
            command.upgrade(Config(str(ROOT / 'alembic.ini')), 'head')
    finally:
        db.close()
