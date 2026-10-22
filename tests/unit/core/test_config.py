import pytest

from bankwise.core.config import (
    Environment, config_dir, get_secret, load_app_config, load_logging_settings, load_rbac_policy,
    parse_environment, weakness_reasons,
)
from bankwise.core.errors import ConfigError
from bankwise.core.pii import redact_text
from core_test_data import STRONG_SECRET

VALID_APP = """
secrets:
  required_in_production: [jwt_secret]
rate_limits:
  auth_attempt: {limit: 3, window_seconds: 30}
"""


def write(directory, name, text):
    (directory / name).write_text(text, encoding="utf-8")


def test_repository_configs_load_and_validate():
    app = load_app_config(environ={"BANKWISE_ENV": "development"})
    assert app.environment is Environment.DEVELOPMENT and not app.is_production
    assert app.rate_limits["auth_attempt"].limit == 5
    assert load_logging_settings(environ={}).level == "INFO"
    policy = load_rbac_policy()
    assert {"operator", "reviewer", "auditor", "admin"} <= set(policy.roles)


def test_unset_or_blank_environment_means_production_and_garbage_is_rejected():
    assert parse_environment({}) is Environment.PRODUCTION
    assert parse_environment({"BANKWISE_ENV": "  "}) is Environment.PRODUCTION
    assert parse_environment({"BANKWISE_ENV": " Development "}) is Environment.DEVELOPMENT
    with pytest.raises(ConfigError):
        parse_environment({"BANKWISE_ENV": "staging"})


def test_config_dir_can_be_overridden(tmp_path):
    assert config_dir({"BANKWISE_CONFIG_DIR": str(tmp_path)}) == tmp_path
    write(tmp_path, "app.yaml", VALID_APP)
    app = load_app_config(environ={"BANKWISE_ENV": "development", "BANKWISE_CONFIG_DIR": str(tmp_path)})
    assert app.rate_limits["auth_attempt"].window_seconds == 30 and app.required_secrets == ("jwt_secret",)


@pytest.mark.parametrize("text", [
    "rate_limits: [1, 2]",                                                    # wrong type
    "unexpected: 1",                                                          # unknown top-level key
    "secrets: {required_in_production: [nope]}",                              # unknown secret name
    "secrets: {required_in_production: [jwt_secret], extra: 1}",              # unknown nested key
    "rate_limits: {Bad Name: {limit: 1, window_seconds: 1}}",
    "rate_limits: {a: {limit: 0, window_seconds: 1}}",                        # limit < 1
    "rate_limits: {a: {limit: true, window_seconds: 1}}",                     # bool is not an int here
    "rate_limits: {a: {limit: 1, window_seconds: -5}}",
    "rate_limits: {a: {limit: 1}}",                                           # missing window
    "rate_limits: {a: {limit: 1, window_seconds: 1, burst: 2}}",
    "secrets: {}\nsecrets: {}",                                               # duplicate key
    "- just\n- a list",
    "key: [unclosed",
])
def test_invalid_app_config_fails_with_a_safe_public_error(tmp_path, text):
    write(tmp_path, "app.yaml", text)
    with pytest.raises(ConfigError) as info:
        load_app_config(tmp_path, {"BANKWISE_ENV": "development"})
    assert str(tmp_path) not in str(info.value) and str(tmp_path) not in repr(info.value.to_public())
    assert info.value.to_public()["code"] == "config_invalid"


def test_missing_file_names_only_the_file_not_the_directory(tmp_path):
    with pytest.raises(ConfigError) as info:
        load_app_config(tmp_path, {"BANKWISE_ENV": "development"})
    assert "app.yaml" in info.value.detail and str(tmp_path) not in info.value.detail


def test_production_requires_strong_secrets_and_never_echoes_them(tmp_path):
    write(tmp_path, "app.yaml", VALID_APP)
    env = {"BANKWISE_ENV": "production", "BANKWISE_CONFIG_DIR": str(tmp_path)}
    with pytest.raises(ConfigError) as missing:
        load_app_config(environ=env)
    assert "BANKWISE_JWT_SECRET" in missing.value.detail and "missing" in missing.value.detail
    with pytest.raises(ConfigError) as weak:
        load_app_config(environ={**env, "BANKWISE_JWT_SECRET": "changeme-but-long-enough-1234567890-xx"})
    assert "placeholder" in weak.value.detail and "changeme" not in weak.value.detail
    assert load_app_config(environ={**env, "BANKWISE_JWT_SECRET": STRONG_SECRET}).is_production


def test_development_does_not_require_secrets(tmp_path):
    write(tmp_path, "app.yaml", VALID_APP)
    assert not load_app_config(tmp_path, {"BANKWISE_ENV": "development"}).is_production


def test_weakness_rules():
    assert weakness_reasons(STRONG_SECRET) == []
    assert "too_short" in weakness_reasons("short")
    assert "placeholder" in weakness_reasons("your_secret_goes_here_" + "x1" * 20)
    assert "low_variety" in weakness_reasons("ab" * 30)


def test_get_secret_returns_a_masked_wrapper_and_registers_it_for_redaction():
    secret = get_secret("jwt_secret", {"BANKWISE_ENV": "production", "BANKWISE_JWT_SECRET": f"  {STRONG_SECRET}\r\n"})
    assert secret.reveal() == STRONG_SECRET and STRONG_SECRET not in repr(secret)
    assert STRONG_SECRET not in redact_text(f"leaked {STRONG_SECRET} oops")


def test_get_secret_rules():
    with pytest.raises(ConfigError):
        get_secret("jwt_secret", {"BANKWISE_ENV": "development"})                       # missing is always an error
    with pytest.raises(ConfigError):
        get_secret("jwt_secret", {"BANKWISE_ENV": "production", "BANKWISE_JWT_SECRET": "weak"})
    assert get_secret("jwt_secret", {"BANKWISE_ENV": "development", "BANKWISE_JWT_SECRET": "weak"})
    with pytest.raises(ConfigError):
        get_secret("not_a_secret", {})


def test_logging_settings_environment_overrides_and_validation(tmp_path):
    write(tmp_path, "logging.yaml", "level: WARNING\nformat: console\nmax_message_chars: 100\n")
    base = load_logging_settings(tmp_path, {})
    assert (base.level, base.format, base.max_message_chars) == ("WARNING", "console", 100)
    over = load_logging_settings(tmp_path, {"BANKWISE_LOG_LEVEL": "debug", "BANKWISE_LOG_FORMAT": "JSON"})
    assert (over.level, over.format) == ("DEBUG", "json")
    for bad_env in ({"BANKWISE_LOG_LEVEL": "LOUD"}, {"BANKWISE_LOG_FORMAT": "xml"}):
        with pytest.raises(ConfigError):
            load_logging_settings(tmp_path, bad_env)
    for bad_file in ("level: SHOUT", "format: yaml", "max_message_chars: 5", "max_message_chars: true", "extra: 1"):
        write(tmp_path, "logging.yaml", bad_file)
        with pytest.raises(ConfigError):
            load_logging_settings(tmp_path, {})


def test_invalid_rbac_file_is_rejected(tmp_path):
    write(tmp_path, "rbac.yaml", "permissions: [a:b]\nroles: {r: [a:c]}\n")
    with pytest.raises(ConfigError):
        load_rbac_policy(tmp_path)
