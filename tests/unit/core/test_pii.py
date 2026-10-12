import pytest

from bankwise.core.errors import ConfigError
from bankwise.core.pii import PII_KEY_ENV, fingerprint, mask_national_id


def test_mask_hides_middle_digits():
    masked = mask_national_id("29503140101234")
    assert masked == "2" + "*" * 11 + "34"
    assert "9503140101" not in masked


def test_mask_handles_short_values():
    assert mask_national_id("12") == "**"


def test_fingerprint_requires_key(monkeypatch):
    monkeypatch.delenv(PII_KEY_ENV, raising=False)
    with pytest.raises(ConfigError):
        fingerprint("29503140101234")


def test_fingerprint_is_stable_and_key_dependent(monkeypatch):
    monkeypatch.setenv(PII_KEY_ENV, "key-one")
    first = fingerprint("29503140101234")
    assert first == fingerprint("29503140101234")
    monkeypatch.setenv(PII_KEY_ENV, "key-two")
    assert fingerprint("29503140101234") != first