"""Environment-only database credentials; safe representations and bounded pool settings."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Mapping, Optional

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from bankwise.core.errors import ConfigError
from bankwise.core.pii import Sensitive, register_secret_value


@dataclass(frozen=True)
class DatabaseSettings:
    url: Sensitive
    pool_size: int = 5
    pool_timeout_seconds: int = 5
    connect_timeout_seconds: int = 5
    retention_days: int = 30
    schema: str = "public"

    def __post_init__(self):
        if not isinstance(self.schema, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", self.schema) or self.schema.startswith("pg_") or self.schema == "information_schema":
            raise ConfigError("database schema must be a non-reserved lowercase SQL identifier")
        try:
            url = make_url(self.url.reveal())
            if url.drivername != "postgresql+psycopg" or not url.host or not url.database or not url.username:
                raise ValueError
            if url.password:
                register_secret_value(url.password)
            register_secret_value(self.url.reveal())
        except (ArgumentError, AttributeError, TypeError, ValueError):
            raise ConfigError("BANKWISE_DATABASE_URL must be a PostgreSQL psycopg connection URL") from None
        for name, low, high in (("pool_size", 1, 20), ("pool_timeout_seconds", 1, 60),
                                ("connect_timeout_seconds", 1, 60), ("retention_days", 1, 365)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ConfigError(f"database {name} must be between {low} and {high}")


def load_database_settings(environ: Optional[Mapping[str, str]] = None) -> DatabaseSettings:
    source = os.environ if environ is None else environ
    values = {"schema": source.get("BANKWISE_DATABASE_SCHEMA", "public")}
    for name in ("pool_size", "pool_timeout_seconds", "connect_timeout_seconds", "retention_days"):
        raw = source.get("BANKWISE_DATABASE_" + name.upper())
        if raw is not None:
            try:
                values[name] = int(raw)
            except (TypeError, ValueError):
                raise ConfigError(f"database {name} must be an integer") from None
    return DatabaseSettings(Sensitive(source.get("BANKWISE_DATABASE_URL", "")), **values)
