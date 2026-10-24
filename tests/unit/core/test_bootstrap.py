import json
import shutil
import sys
from pathlib import Path

import pytest

from bankwise.core.audit import verify_audit_file
from bankwise.core.bootstrap import build_security
from bankwise.core.errors import ConfigError, PermissionDenied
from bankwise.core.pii import Sensitive
from bankwise.core.security import Principal
from core_test_data import STRONG_SECRET

REPO_CONFIGS = Path(__file__).resolve().parents[3] / "configs"
AUDIT_KEY = "audit-key-Zq8Xv3Nc7Rt1Ws5Ye9Bu2Hd6Jf4Lg0"
POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits only")

PRODUCTION_APP = """
secrets:
  required_in_production: [jwt_secret]
auth: {issuer: bankwise, audience: bankwise-api}
storage: {base_dir: vault, ttl_seconds: 600, max_bytes: 100000}
rate_limits:
  auth_attempt: {limit: 5, window_seconds: 60}
  identity_submit: {limit: 10, window_seconds: 60}
audit: {base_dir: auditdir, filename: audit.jsonl}
deployment: {single_process: true}
"""
PROD_ENV = {"BANKWISE_ENV": "production", "BANKWISE_JWT_SECRET": STRONG_SECRET, "BANKWISE_AUDIT_HMAC_KEY": AUDIT_KEY}


@pytest.fixture
def configs(tmp_path):
    folder = tmp_path / "configs"
    folder.mkdir()
    for name in ("rbac.yaml", "logging.yaml"):
        shutil.copy(REPO_CONFIGS / name, folder / name)
    (folder / "app.yaml").write_text(PRODUCTION_APP, encoding="utf-8")
    return folder


def edit(configs, old, new):
    path = configs / "app.yaml"
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_production_starts_and_everything_is_wired_end_to_end(configs, tmp_path):
    runtime = build_security(configs, PROD_ENV, root=tmp_path)
    assert runtime.config.is_production and runtime.warnings == () and runtime.store is not None
    token = runtime.service.issue_token(Principal("user-1", frozenset({"operator"})))
    assert runtime.service.guard(f"Bearer {token}", "identity:submit", "ip:1").subject_id == "user-1"
    with pytest.raises(PermissionDenied):
        runtime.service.guard(f"Bearer {token}", "identity:review", "ip:1")
    audit = verify_audit_file(tmp_path / "auditdir" / "audit.jsonl", Sensitive(AUDIT_KEY))
    assert audit.ok and audit.records == 2                                                 # issue + denial are on disk
    dumped = (tmp_path / "auditdir" / "audit.jsonl").read_text()
    assert token not in dumped and STRONG_SECRET not in dumped and AUDIT_KEY not in dumped


@pytest.mark.parametrize("missing", ["BANKWISE_JWT_SECRET", "BANKWISE_AUDIT_HMAC_KEY"])
def test_production_refuses_to_start_without_its_secrets(configs, tmp_path, missing):
    env = {k: v for k, v in PROD_ENV.items() if k != missing}
    with pytest.raises(ConfigError) as info:
        build_security(configs, env, root=tmp_path)
    assert missing in info.value.detail and STRONG_SECRET not in info.value.detail


@pytest.mark.parametrize("name", ["BANKWISE_JWT_SECRET", "BANKWISE_AUDIT_HMAC_KEY"])
def test_production_refuses_weak_secrets(configs, tmp_path, name):
    with pytest.raises(ConfigError) as info:
        build_security(configs, {**PROD_ENV, name: "weak"}, root=tmp_path)
    assert "too_short" in info.value.detail and "weak" not in info.value.detail.replace("weak secret", "")


def test_production_refuses_to_run_without_a_durable_audit_sink(configs, tmp_path):
    edit(configs, "audit: {base_dir: auditdir, filename: audit.jsonl}\n", "")
    with pytest.raises(ConfigError) as info:
        build_security(configs, PROD_ENV, root=tmp_path)
    assert "durable audit" in info.value.detail


@pytest.mark.parametrize("deployment", ["deployment: {single_process: false}\n", ""])
def test_production_requires_the_single_process_declaration(configs, tmp_path, deployment):
    edit(configs, "deployment: {single_process: true}\n", deployment)
    with pytest.raises(ConfigError) as info:
        build_security(configs, PROD_ENV, root=tmp_path)
    assert "single_process" in info.value.detail and "process-local" in info.value.detail


def test_start_up_fails_without_the_auth_section_or_the_failure_budget_policy(configs, tmp_path):
    edit(configs, "auth: {issuer: bankwise, audience: bankwise-api}\n", "")
    with pytest.raises(ConfigError) as info:
        build_security(configs, PROD_ENV, root=tmp_path)
    assert "auth" in info.value.detail
    edit_text = PRODUCTION_APP.replace("  auth_attempt: {limit: 5, window_seconds: 60}\n", "")
    (configs / "app.yaml").write_text(edit_text, encoding="utf-8")
    with pytest.raises(ConfigError) as info2:
        build_security(configs, PROD_ENV, root=tmp_path)
    assert "auth_attempt" in info2.value.detail


def test_development_is_practical_but_warns(configs, tmp_path):
    edit(configs, "audit: {base_dir: auditdir, filename: audit.jsonl}\n", "")
    edit(configs, "deployment: {single_process: true}\n", "")
    runtime = build_security(configs, {"BANKWISE_ENV": "development", "BANKWISE_JWT_SECRET": STRONG_SECRET}, root=tmp_path)
    assert not runtime.config.is_production
    assert any("no durable audit" in w for w in runtime.warnings) and any("development mode" in w for w in runtime.warnings)
    token = runtime.service.issue_token(Principal("user-1", frozenset({"operator"})))
    assert runtime.service.authenticate(f"Bearer {token}", "ip:1")


def test_a_missing_environment_means_production_rules_apply(configs, tmp_path):
    with pytest.raises(ConfigError):
        build_security(configs, {"BANKWISE_JWT_SECRET": STRONG_SECRET}, root=tmp_path)


def test_a_tampered_audit_file_stops_start_up(configs, tmp_path):
    build_security(configs, PROD_ENV, root=tmp_path).service.issue_token(Principal("u", frozenset()))
    audit = tmp_path / "auditdir" / "audit.jsonl"
    audit.write_bytes(audit.read_bytes().replace(b'"outcome":"success"', b'"outcome":"failure"'))
    with pytest.raises(Exception) as info:
        build_security(configs, PROD_ENV, root=tmp_path)
    assert "verification" in getattr(info.value, "detail", "")


@POSIX
def test_vault_and_audit_directories_are_created_owner_only(configs, tmp_path):
    runtime = build_security(configs, PROD_ENV, root=tmp_path)
    assert ((tmp_path / "vault").stat().st_mode & 0o777) == 0o700
    assert ((tmp_path / "auditdir").stat().st_mode & 0o777) == 0o700
    assert runtime.store is not None
