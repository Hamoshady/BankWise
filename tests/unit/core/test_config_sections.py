import pytest

from bankwise.core.auth import AuthSettings
from bankwise.core.config import load_app_config
from bankwise.core.errors import ConfigError
from bankwise.core.storage import StorageSettings
from core_test_data import STRONG_SECRET

DEV = {"BANKWISE_ENV": "development"}


def app_yaml(tmp_path, text):
    (tmp_path / "app.yaml").write_text(text, encoding="utf-8")
    return tmp_path


def test_repository_config_has_auth_and_storage_and_requires_the_signing_secret_in_production(monkeypatch):
    config = load_app_config(environ=DEV)
    assert config.auth == AuthSettings(issuer="bankwise", audience="bankwise-api")
    assert config.storage == StorageSettings()
    assert config.audit is not None and config.deployment is not None and config.deployment.single_process is False
    assert config.required_secrets == ("jwt_secret",)
    with pytest.raises(ConfigError) as info:
        load_app_config(environ={"BANKWISE_ENV": "production"})
    assert "BANKWISE_JWT_SECRET" in info.value.detail and "missing" in info.value.detail
    # the repository config also configures a durable audit file, so production now needs its HMAC key too
    with pytest.raises(ConfigError) as audit_info:
        load_app_config(environ={"BANKWISE_ENV": "production", "BANKWISE_JWT_SECRET": STRONG_SECRET})
    assert "BANKWISE_AUDIT_HMAC_KEY" in audit_info.value.detail
    production = {"BANKWISE_ENV": "production", "BANKWISE_JWT_SECRET": STRONG_SECRET,
                  "BANKWISE_AUDIT_HMAC_KEY": "another-strong-key-Zq8Xv3Nc7Rt1Ws5Ye9Bu2Hd6"}
    assert load_app_config(environ=production).is_production


def test_sections_are_optional(tmp_path):
    config = load_app_config(app_yaml(tmp_path, "rate_limits: {}\n"), DEV)
    assert config.auth is None and config.storage is None


@pytest.mark.parametrize("text", [
    "auth: [1]", "auth: {issuer: a}", "auth: {audience: a}", "auth: {issuer: a, audience: b, extra: 1}",
    "auth: {issuer: a, audience: b, algorithm: none}", "auth: {issuer: a, audience: b, algorithm: RS256}",
    "auth: {issuer: a, audience: b, access_token_ttl_seconds: 0}",
    "auth: {issuer: a, audience: b, access_token_ttl_seconds: 999999}",
    "auth: {issuer: a, audience: b, leeway_seconds: -1}",
    "storage: [1]", "storage: {extra: 1}", "storage: {ttl_seconds: 0}", "storage: {max_bytes: -5}",
    "storage: {base_dir: ''}",
])
def test_invalid_sections_are_rejected(tmp_path, text):
    with pytest.raises(ConfigError):
        load_app_config(app_yaml(tmp_path, text), DEV)


def test_valid_custom_sections_are_loaded(tmp_path):
    text = "auth: {issuer: me, audience: you, access_token_ttl_seconds: 120, leeway_seconds: 0}\n" \
           "storage: {base_dir: tmp/up, ttl_seconds: 60, max_bytes: 5000}\n"
    config = load_app_config(app_yaml(tmp_path, text), DEV)
    assert (config.auth.issuer, config.auth.access_token_ttl_seconds, config.auth.leeway_seconds) == ("me", 120, 0)
    assert (config.storage.base_dir, config.storage.ttl_seconds, config.storage.max_bytes) == ("tmp/up", 60, 5000)


def test_audit_and_deployment_sections_are_validated(tmp_path):
    ok = load_app_config(app_yaml(tmp_path, "audit: {base_dir: a, filename: x.jsonl, max_bytes: 2048}\ndeployment: {single_process: true}\n"), DEV)
    assert ok.audit.filename == "x.jsonl" and ok.deployment.single_process is True
    for text in ("audit: [1]", "audit: {extra: 1}", "audit: {filename: '../x'}", "audit: {max_bytes: 5}",
                 "deployment: {single_process: yes please}", "deployment: {single_process: 1}", "deployment: {x: 1}"):
        with pytest.raises(ConfigError):
            load_app_config(app_yaml(tmp_path, text), DEV)


def test_oversized_config_files_are_refused(tmp_path):
    with pytest.raises(ConfigError) as info:
        load_app_config(app_yaml(tmp_path, "# " + "x" * 300000 + "\nrate_limits: {}\n"), DEV)
    assert "too large" in info.value.detail


def test_audit_key_is_only_required_when_an_audit_section_exists(tmp_path):
    env = {"BANKWISE_ENV": "production", "BANKWISE_JWT_SECRET": STRONG_SECRET}
    load_app_config(app_yaml(tmp_path, "secrets: {required_in_production: [jwt_secret]}\n"), env)
    with pytest.raises(ConfigError) as info:
        load_app_config(app_yaml(tmp_path, "secrets: {required_in_production: [jwt_secret]}\naudit: {}\n"), env)
    assert "BANKWISE_AUDIT_HMAC_KEY" in info.value.detail
