"""Start-up for the security layer: builds everything from configuration and REFUSES to start in production
when a guarantee cannot be met. An application entry point (API, worker, CLI) calls `build_security()` once,
before it serves anything. Nothing in this repository calls it yet.

Production start-up fails (ConfigError) unless all of these hold:
* the signing secret and the audit HMAC key are present and strong (checked by `load_app_config`);
* a durable audit sink is configured (the `audit` section), because operational logs are not an audit trail;
* the operator has declared `deployment.single_process: true`, because rate limits and token revocations are
  held in this process's memory and would silently stop working across several processes.
Development stays practical: missing audit/deployment sections only produce warnings."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Tuple

from bankwise.core.audit import AuditSink, HashChainedFileAuditSink, LoggingAuditSink
from bankwise.core.auth import TokenService
from bankwise.core.config import (
    AppConfig, get_secret, load_app_config, load_logging_settings, load_rbac_policy,
)
from bankwise.core.errors import ConfigError
from bankwise.core.guard import AUTH_POLICY, SecurityService
from bankwise.core.logging import configure_logging
from bankwise.core.rate_limit import InMemoryRateLimiter
from bankwise.core.security import Authorizer, safe_output_path
from bankwise.core.storage import SecureFileStore


@dataclass(frozen=True)
class SecurityRuntime:
    config: AppConfig
    service: SecurityService
    audit_sink: AuditSink
    store: Optional[SecureFileStore]
    warnings: Tuple[str, ...]


def build_security(directory: Optional[Path] = None, environ: Optional[Mapping[str, str]] = None,
                   root: Optional[Path] = None) -> SecurityRuntime:
    """`root` is the directory that relative paths in the config (storage, audit) resolve against; it defaults to
    the process working directory."""
    source = os.environ if environ is None else environ
    base = Path(root) if root is not None else Path.cwd()
    configure_logging(load_logging_settings(directory, source))           # redaction is active before anything else
    config = load_app_config(directory, source)                           # production secrets are validated here
    if config.auth is None:
        raise ConfigError("app.yaml must define an 'auth' section")
    if AUTH_POLICY not in config.rate_limits:
        raise ConfigError(f"app.yaml rate_limits must define '{AUTH_POLICY}'")

    warnings = []
    if config.is_production:
        if config.audit is None:
            raise ConfigError("production requires a durable audit sink: add an 'audit' section to app.yaml")
        if config.deployment is None or not config.deployment.single_process:
            raise ConfigError("production requires deployment.single_process: true (rate limits and token "
                              "revocation are process-local; use a shared store before running several processes)")

    if config.audit is not None:
        folder = Path(config.audit.base_dir)
        folder = folder if folder.is_absolute() else base / folder
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        sink: AuditSink = HashChainedFileAuditSink(
            safe_output_path(folder, config.audit.filename), get_secret("audit_hmac_key", source),
            config.audit.max_bytes)
    else:
        sink = LoggingAuditSink()
        warnings.append("no durable audit sink configured: audit events only go to the operational log")
    if not config.is_production:
        warnings.append("development mode: process-local rate limits and token revocation, weak secrets allowed")

    service = SecurityService(
        TokenService(config.auth, get_secret("jwt_secret", source)),
        Authorizer(load_rbac_policy(directory, source)),
        InMemoryRateLimiter(config.rate_limits),
        sink,
    )
    store = SecureFileStore(config.storage, root=base) if config.storage is not None else None
    return SecurityRuntime(config, service, sink, store, tuple(warnings))
