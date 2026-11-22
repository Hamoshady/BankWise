import pytest

from bankwise.core.errors import ConfigError
from bankwise.database.config import load_database_settings


@pytest.mark.parametrize("url", ["", "sqlite://", "postgresql://u:p@localhost/db", "garbage", "postgresql+psycopg:///db"])
def test_database_url_rejects_missing_or_wrong_engine(url):
    with pytest.raises(ConfigError) as error:
        load_database_settings({"BANKWISE_DATABASE_URL": url})
    assert url not in str(error.value) if url else True


def test_database_settings_redact_credentials():
    config = load_database_settings({"BANKWISE_DATABASE_URL": "postgresql+psycopg://u:secret-value@localhost/db"})
    assert "secret-value" not in repr(config)
    assert config.retention_days == 30


@pytest.mark.parametrize("value", ["0", "100", "not-an-int"])
def test_invalid_pool_settings(value):
    with pytest.raises(ConfigError):
        load_database_settings({"BANKWISE_DATABASE_URL": "postgresql+psycopg://u:p@localhost/db",
                                "BANKWISE_DATABASE_POOL_SIZE": value})


@pytest.mark.parametrize('schema', ['', 'public,other', 'a;DROP TABLE users', 'pg_catalog', 'information_schema', 'UPPER', 'x' * 64])
def test_schema_rejects_unsafe_or_reserved_names(schema):
    with pytest.raises(ConfigError):
        load_database_settings({'BANKWISE_DATABASE_URL': 'postgresql+psycopg://u:p@localhost/db',
                                'BANKWISE_DATABASE_SCHEMA': schema})


def test_schema_configuration_defaults_and_override():
    env = {'BANKWISE_DATABASE_URL': 'postgresql+psycopg://u:p@localhost/db'}
    assert load_database_settings(env).schema == 'public'
    assert load_database_settings(dict(env, BANKWISE_DATABASE_SCHEMA='bankwise_api')).schema == 'bankwise_api'


def test_offline_migrations_emit_configured_search_path(monkeypatch):
    from io import StringIO
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    monkeypatch.setenv('BANKWISE_DATABASE_URL', 'postgresql+psycopg://u:p@localhost/db')
    monkeypatch.setenv('BANKWISE_DATABASE_SCHEMA', 'bankwise_api')
    output = StringIO()
    cfg = Config(str(Path(__file__).resolve().parents[2] / 'alembic.ini'), output_buffer=output)
    command.upgrade(cfg, 'head', sql=True)
    assert 'SET search_path TO "bankwise_api"' in output.getvalue()
    assert output.getvalue().index('SET search_path') < output.getvalue().index('CREATE TABLE')
