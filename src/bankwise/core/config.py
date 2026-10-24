"""Configuration loading and validation (YAML files + environment overrides).

* Files live in `configs/` (override the directory with BANKWISE_CONFIG_DIR). Unknown keys, wrong types,
  out-of-range values and duplicate YAML keys are rejected with `ConfigError`; nothing silently falls back.
* Secrets are NEVER in files: they come from environment variables only (see SECRET_ENV_VARS).
* BANKWISE_ENV selects 'development' or 'production'. When it is unset or empty the app assumes PRODUCTION,
  so a forgotten setting fails closed. In production, required secrets must be present and strong.
* This module holds application-wide settings only. OCR-specific settings stay in the OCR package."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yaml

from bankwise.core.audit import AuditSettings
from bankwise.core.auth import AuthSettings
from bankwise.core.errors import ConfigError
from bankwise.core.logging import VALID_FORMATS, VALID_LEVELS, LoggingSettings
from bankwise.core.pii import Sensitive, register_secret_value
from bankwise.core.rate_limit import RateLimit
from bankwise.core.security import RbacPolicy
from bankwise.core.storage import StorageSettings

CONFIG_DIR_ENV = "BANKWISE_CONFIG_DIR"
ENVIRONMENT_ENV = "BANKWISE_ENV"
LOG_LEVEL_ENV = "BANKWISE_LOG_LEVEL"
LOG_FORMAT_ENV = "BANKWISE_LOG_FORMAT"
SECRET_ENV_VARS: Dict[str, str] = {
    "jwt_secret": "BANKWISE_JWT_SECRET",
    "pii_hmac_key": "BANKWISE_PII_HMAC_KEY",
    "audit_hmac_key": "BANKWISE_AUDIT_HMAC_KEY",
}
MAX_CONFIG_BYTES = 256 * 1024
MIN_SECRET_LENGTH = 32
MIN_DISTINCT_CHARACTERS = 8
PLACEHOLDER_MARKERS = ("your_", "changeme", "change_me", "replace_me", "placeholder", "example")
_POLICY_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


@dataclass(frozen=True)
class DeploymentSettings:
    """`single_process` is an explicit statement by the operator that exactly ONE application process runs.
    Production start-up refuses to proceed without it, because rate limits and token revocations are kept in that
    process's memory (see bootstrap.py)."""

    single_process: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.single_process, bool):
            raise ConfigError("deployment single_process must be true or false")


class Environment(str, Enum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"


@dataclass(frozen=True)
class AppConfig:
    environment: Environment
    required_secrets: Tuple[str, ...]
    rate_limits: Mapping[str, RateLimit]
    auth: Optional[AuthSettings] = None
    storage: Optional[StorageSettings] = None
    audit: Optional[AuditSettings] = None
    deployment: Optional[DeploymentSettings] = None

    @property
    def is_production(self) -> bool:
        return self.environment == Environment.PRODUCTION


# ------------------------------------------------------------------ YAML
class _UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.constructor.ConstructorError(None, None, f"duplicate key {key!r}", key_node.start_mark)
            seen.add(key)
        return super().construct_mapping(node, deep)


def config_dir(environ: Optional[Mapping[str, str]] = None) -> Path:
    source = os.environ if environ is None else environ
    override = (source.get(CONFIG_DIR_ENV) or "").strip()
    return Path(override) if override else Path(__file__).resolve().parents[3] / "configs"


def read_yaml(path: Path) -> Dict[str, Any]:
    try:
        if path.stat().st_size > MAX_CONFIG_BYTES:
            raise ConfigError(f"config file '{path.name}' is too large")
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.load(handle, Loader=_UniqueKeyLoader)
    except ConfigError:
        raise
    except (OSError, yaml.YAMLError, TypeError, ValueError, UnicodeDecodeError) as error:
        raise ConfigError(f"cannot read config file '{path.name}': {type(error).__name__}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"config file '{path.name}' must contain a mapping")
    return data


def _only_keys(raw: Mapping[str, Any], allowed: Tuple[str, ...], where: str) -> None:
    unknown = sorted(str(key) for key in raw if key not in allowed)
    if unknown:
        raise ConfigError(f"unknown keys in {where}: {unknown}")


# ------------------------------------------------------------------ environment and secrets
def parse_environment(environ: Optional[Mapping[str, str]] = None) -> Environment:
    source = os.environ if environ is None else environ
    raw = (source.get(ENVIRONMENT_ENV) or "").strip().lower()
    if not raw:
        return Environment.PRODUCTION
    try:
        return Environment(raw)
    except ValueError:
        raise ConfigError(f"{ENVIRONMENT_ENV} must be 'development' or 'production'") from None


def weakness_reasons(value: str) -> List[str]:
    reasons: List[str] = []
    if len(value) < MIN_SECRET_LENGTH:
        reasons.append("too_short")
    if any(marker in value.lower() for marker in PLACEHOLDER_MARKERS):
        reasons.append("placeholder")
    if len(set(value)) < MIN_DISTINCT_CHARACTERS:
        reasons.append("low_variety")
    return reasons


def _secret_value(name: str, environ: Mapping[str, str]) -> Tuple[str, Optional[str]]:
    try:
        var = SECRET_ENV_VARS[name]
    except KeyError:
        raise ConfigError(f"unknown secret name '{name}'") from None
    value = (environ.get(var) or "").strip()
    return var, (value or None)


def get_secret(name: str, environ: Optional[Mapping[str, str]] = None) -> Sensitive:
    """Read a secret from the environment. Missing is always an error; weak is an error in production.
    The value is wrapped in `Sensitive` and registered so that log redaction masks it everywhere."""
    source = os.environ if environ is None else environ
    var, value = _secret_value(name, source)
    if value is None:
        raise ConfigError(f"missing environment variable {var}")
    if parse_environment(source) == Environment.PRODUCTION:
        reasons = weakness_reasons(value)
        if reasons:
            raise ConfigError(f"weak secret {var}: {', '.join(reasons)}")
    register_secret_value(value)
    return Sensitive(value)


def _validate_required_secrets(names: Tuple[str, ...], environ: Mapping[str, str]) -> None:
    problems: List[str] = []
    for name in names:
        var, value = _secret_value(name, environ)
        reasons = ["missing"] if value is None else weakness_reasons(value)
        if reasons:
            problems.append(f"{var} ({', '.join(reasons)})")
    if problems:
        raise ConfigError("invalid production secrets: " + "; ".join(problems))


# ------------------------------------------------------------------ loaders
def _section(raw: Mapping[str, Any], name: str, factory: Any, keys: Tuple[str, ...]) -> Any:
    """Optional config section built into a validated settings object (absent -> None)."""
    if name not in raw:
        return None
    section = raw[name]
    if not isinstance(section, dict):
        raise ConfigError(f"app.yaml '{name}' must be a mapping")
    _only_keys(section, keys, f"app.yaml {name}")
    try:
        return factory(**section)
    except TypeError:
        raise ConfigError(f"app.yaml '{name}' is missing required keys") from None


def load_app_config(directory: Optional[Path] = None, environ: Optional[Mapping[str, str]] = None) -> AppConfig:
    source = os.environ if environ is None else environ
    raw = read_yaml((directory or config_dir(source)) / "app.yaml")
    _only_keys(raw, ("secrets", "rate_limits", "auth", "storage", "audit", "deployment"), "app.yaml")

    secrets_raw = raw.get("secrets", {})
    if not isinstance(secrets_raw, dict):
        raise ConfigError("app.yaml 'secrets' must be a mapping")
    _only_keys(secrets_raw, ("required_in_production",), "app.yaml secrets")
    required = secrets_raw.get("required_in_production", [])
    if not isinstance(required, list) or any(name not in SECRET_ENV_VARS for name in required):
        raise ConfigError(f"required_in_production must be a list of known secrets {sorted(SECRET_ENV_VARS)}")

    limits_raw = raw.get("rate_limits", {})
    if not isinstance(limits_raw, dict):
        raise ConfigError("app.yaml 'rate_limits' must be a mapping")
    limits: Dict[str, RateLimit] = {}
    for name, rule in limits_raw.items():
        if not isinstance(name, str) or not _POLICY_NAME_RE.match(name) or not isinstance(rule, dict):
            raise ConfigError("each rate limit needs a lowercase name and a mapping value")
        _only_keys(rule, ("limit", "window_seconds"), f"rate limit '{name}'")
        if set(rule) != {"limit", "window_seconds"}:
            raise ConfigError(f"rate limit '{name}' needs 'limit' and 'window_seconds'")
        limits[name] = RateLimit(rule["limit"], rule["window_seconds"])

    config = AppConfig(parse_environment(source), tuple(required), limits,
                       _section(raw, "auth", AuthSettings, ("issuer", "audience", "access_token_ttl_seconds",
                                                            "leeway_seconds", "algorithm")),
                       _section(raw, "storage", StorageSettings, ("base_dir", "ttl_seconds", "max_bytes", "max_files")),
                       _section(raw, "audit", AuditSettings, ("base_dir", "filename", "max_bytes")),
                       _section(raw, "deployment", DeploymentSettings, ("single_process",)))
    if config.is_production:
        needed = tuple(dict.fromkeys(config.required_secrets + (("audit_hmac_key",) if config.audit else ())))
        _validate_required_secrets(needed, source)
    return config


def load_logging_settings(directory: Optional[Path] = None, environ: Optional[Mapping[str, str]] = None) -> LoggingSettings:
    source = os.environ if environ is None else environ
    raw = read_yaml((directory or config_dir(source)) / "logging.yaml")
    _only_keys(raw, ("level", "format", "max_message_chars"), "logging.yaml")
    level = (source.get(LOG_LEVEL_ENV) or "").strip().upper() or raw.get("level", "INFO")
    fmt = (source.get(LOG_FORMAT_ENV) or "").strip().lower() or raw.get("format", "json")
    if level not in VALID_LEVELS or fmt not in VALID_FORMATS:
        raise ConfigError(f"logging level must be one of {VALID_LEVELS} and format one of {VALID_FORMATS}")
    return LoggingSettings(level=level, format=fmt, max_message_chars=raw.get("max_message_chars", 2000))


def load_rbac_policy(directory: Optional[Path] = None, environ: Optional[Mapping[str, str]] = None) -> RbacPolicy:
    source = os.environ if environ is None else environ
    return RbacPolicy.from_mapping(read_yaml((directory or config_dir(source)) / "rbac.yaml"))
